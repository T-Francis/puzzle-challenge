"""Stage 4 — solving: elimination + shape score + % per piece.

Rotation q = number of clockwise quarter turns applied to the catalog piece (N on top) to place it on the
board. Side k of the board receives side (k - q) % 4 of the piece.
"""
import os, sys, hashlib
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, SIDE_NAMES, load_json, save_json
from match import load_model, offset, procrustes_rms, curve_dist, D_PIECE

LYING_PENALTY = 2.0      # penalty (log-likelihood units) for a lying piece (odd q)
NB = [(-1, 0), (0, 1), (1, 0), (0, -1)]          # neighbour in the direction of side k (N, E, S, W)


def load_placements():
    path = os.path.join(DATA, "placements.json")
    return load_json(path) if os.path.exists(path) else {"placed": [], "excluded": []}


def load_board():
    b = load_json(os.path.join(DATA, "board.json"))
    z = np.load(os.path.join(DATA, "board_sockets.npz"))
    return b, z, load_placements()


def local_profile(seg):
    """Points of a socket (clockwise around the cell) -> 128-point profile + required type."""
    p0, p1 = seg[0], seg[-1]
    L = np.linalg.norm(p1 - p0)
    ux = (p1 - p0) / L
    uy = np.array([ux[1], -ux[0]])
    loc = np.stack([(seg - p0) @ ux, (seg - p0) @ uy], 1)
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(loc, axis=0), axis=1))]
    t = np.linspace(0, d[-1], 128)
    prof = np.stack([np.interp(t, d, loc[:, 0]), np.interp(t, d, loc[:, 1])], 1)
    ymax, ymin = loc[:, 1].max(), loc[:, 1].min()
    req = "T" if (ymax > 0.15 * L and ymax >= -ymin) else ("B" if -ymin > 0.15 * L else "F")
    return prof, req


def board_sockets(b, z):
    """Sockets measured on the board photo: {(cell, k): points}."""
    sockets = {}
    for c in b["cells"]:
        if c["empty"]:
            for k, s in enumerate(c["sides"]):
                key = f"{c['id']}__{s['name']}__points"
                if s.get("extracted") and key in z.files:
                    sockets[(c["id"], k)] = z[key]
    return sockets


def effective_sockets(b, z, placements):
    """Board sockets + sockets created by the confirmed pieces (fitted in their cell).
    Returns sockets {(cell, k): points}, filled cells, transforms of the placed pieces."""
    from fit_view import fit_piece_pts, piece_side_points, rot_q
    from common import load_raw
    C = {c["id"]: c for c in b["cells"]}
    pos = {(c["r"], c["c"]): c["id"] for c in b["cells"]}
    sockets = board_sockets(b, z)
    raw = load_raw() if placements["placed"] else None
    filled, fitted = set(), {}
    for pl in placements["placed"]:
        cid, pid, q = pl["cell"], pl["piece"], pl["rotation"] // 90
        if not C[cid]["empty"]:      # covered by a recalibration photo: part of the measured board, nothing to fit
            filled.add(cid); continue
        known = {k: sockets[(cid, k)] for k in range(4) if (cid, k) in sockets}
        if not known:
            print("warning: placement without any known side ignored", pl); continue
        full, R, ma, mb, rms = fit_piece_pts(raw[pid], q, known)
        fitted[cid] = dict(piece=pid, rotation=pl["rotation"], contour=full, rms=rms)
        filled.add(cid)
        r, c = C[cid]["r"], C[cid]["c"]
        for k, (dr, dc) in enumerate(NB):
            nid = pos.get((r + dr, c + dc))
            if nid is None or not C[nid]["empty"] or nid in filled:
                continue
            j = (k - q) % 4                                    # side of the placed piece facing the neighbour
            side = (R @ (rot_q(piece_side_points(raw[pid], j), q) - ma).T).T + mb
            sockets[(nid, (k + 2) % 4)] = placed_socket(side[::-1].copy())   # clockwise around the neighbouring cell
    return sockets, filled, fitted


def frontier_cells(b, sockets, filled):
    cells = []
    for c in b["cells"]:
        if not c["empty"] or c["id"] in filled:
            continue
        known = []
        for k in range(4):
            if (c["id"], k) in sockets:
                prof, req = local_profile(sockets[(c["id"], k)])
                if req != "F":
                    known.append((k, req, prof))
        if known:
            cells.append(dict(id=c["id"], r=c["r"], c=c["c"], known=known, doubtful=c["id"] in DOUBTFUL))
    return cells


# doubtful cells (data/params.json): e.g. edge of a pale moon mistaken for paper
DOUBTFUL = set(load_json(os.path.join(DATA, "params.json")).get("doubtful_cells", []))


CURVE_TOP_K = 40      # candidates re-scored as curves per socket (the true piece: within the 21 best paired, 22 sides)
_CACHE = {}


def side_scores(S, req, Pp, types, key):
    """Score (P,4) of every piece side against one socket S (profile, u): curve distance (match.curve_dist) for the
    CURVE_TOP_K best sides of the required type by paired rms, the worst of those for the others (no better than any
    examined side). Cached: the simulation and the workshop score the same sockets again and again."""
    ck = (key, req, S.tobytes())
    hit = _CACHE.get(ck)                  # .get: another thread may clear the cache meanwhile
    if hit is not None:
        return hit
    flat = Pp.reshape(-1, *Pp.shape[2:])
    cand = np.where(types.reshape(-1) == req)[0]
    if not len(cand):
        out = np.full(types.shape, np.inf)
    else:
        pr = procrustes_rms(S, flat)                  # every side (the simulation penalises a wrong type, finitely)
        o = cand[np.argsort(pr[cand])]
        top = o[:CURVE_TOP_K]
        cd = curve_dist(S, flat[top])
        # the others: worse than the worst examined, in proportion to their paired rms (keeps them apart: a common
        # floor would make hundreds of ties and dilute the probabilities)
        out = np.maximum(cd.max() * pr / pr[top[-1]], cd.max())
        out[top] = cd
        out = out.reshape(types.shape)
    if len(_CACHE) > 50000:
        _CACHE.clear()
    _CACHE[ck] = out
    return out


def save_score_cache(path):
    """Freeze the side scores computed so far (model_export: the board sockets of every condition), so that the
    workshop does not spend minutes on them at start-up. Keys = (catalog hash, required type, socket bytes)."""
    out = {}
    for h in sorted({k[0] for k in _CACHE}):                 # one group per catalog (their sizes differ)
        keys = [k for k in _CACHE if k[0] == h]
        out[f"{h}__reqs"] = np.array([k[1] for k in keys])
        out[f"{h}__sockets"] = np.stack([np.frombuffer(k[2]) for k in keys])
        out[f"{h}__scores"] = np.stack([_CACHE[k] for k in keys])
    np.savez_compressed(path, **out)


def load_score_cache(path):
    """Entries of a frozen cache; a stale entry is harmless (its key never matches a new catalog or socket)."""
    z = np.load(path)
    n = 0
    for key in z.files:
        if key.endswith("__reqs"):
            h = key[:-len("__reqs")]
            for r, s, v in zip(z[key], z[f"{h}__sockets"], z[f"{h}__scores"]):
                _CACHE[(h, str(r), s.tobytes())] = v
                n += 1
    return n


def placed_socket(points, d_board=0.25):
    """A side of a placed piece (catalog outline) used as a socket for its neighbour: the catalog outline is D_PIECE too
    wide (match.D_PIECE), a board socket only d_board: widen the socket by the difference so that the same d_board
    treatment applies to every socket."""
    return offset(points[None, None], -(D_PIECE - d_board))[0, 0]


def score_cells(pieces, prof, cells, d_piece=D_PIECE, d_board=0.0):
    """For each cell: cost (P,4) = sum of the squared side scores of the known sides (inf if eliminated).
    Side score = curve distance (u) between the socket and the piece side (side_scores)."""
    Pp = offset(prof, d_piece)
    types = np.array([[s["type"] for s in p["sides"]] for p in pieces])           # (P,4)
    key = hashlib.sha1(Pp.tobytes()).hexdigest()                                # catalog content + d_piece
    out = []
    for cell in cells:
        cost = np.zeros((len(pieces), 4))
        alive = np.ones((len(pieces), 4), bool)
        per_side = np.zeros((len(pieces), 4, len(cell["known"])))
        for n, (k, req, sp) in enumerate(cell["known"]):
            S = offset(sp[None, None], -d_board)[0, 0] if d_board else sp
            rms_j = side_scores(S, req, Pp, types, key)                             # (P,4) per piece side
            for q in range(4):
                j = (k - q) % 4
                alive[:, q] &= types[:, j] == req
                cost[:, q] += rms_j[:, j] ** 2
                per_side[:, q, n] = rms_j[:, j]
        cost[~alive] = np.inf
        out.append(dict(cost=cost, per_side=per_side, alive=alive))
    return out


def lying_penalty(pieces):
    """(P,4) penalty: a piece lying on its side (odd q) is unlikely unless it is almost square."""
    aspect = np.array([p["core_w"] / p["core_h"] for p in pieces])
    pen = np.zeros((len(pieces), 4))
    pen[:, [1, 3]] = np.where(aspect > 0.95, 0.0, LYING_PENALTY)[:, None]
    return pen


def likelihoods(scored, sigma, pieces):
    pen = lying_penalty(pieces)
    return [-s["cost"] / (2 * sigma ** 2) - pen for s in scored]


def sinkhorn(A, col_cap, iters=200):
    A = A.copy()
    for _ in range(iters):
        A /= A.sum(1, keepdims=True) + 1e-300
        A *= (col_cap / (A.sum(0) + 1e-300))[None]
    A /= A.sum(1, keepdims=True) + 1e-300
    return A


# Thresholds in σ for the curve-distance scores (they were 1.5 / 2.2 / 3.0 for the paired rms). The scores
# are compressed: true matches ≈ 0.86 σ (p90 0.99 σ, accuracy.py B), wrong sides within 1.08 σ: ≈ 1.8 per socket,
# within 1.3 σ: ≈ 7.7.
QUALITY_GOOD, QUALITY_FAIR, DEAD_END = 1.1, 1.35, 1.6


def quality_of(rms_mean, sigma):
    """Absolute check: a good match has a mean rms per side close to sigma."""
    return "good" if rms_mean <= QUALITY_GOOD * sigma else ("fair" if rms_mean <= QUALITY_FAIR * sigma else "poor")


def run(d_board=0.0, sigma=None, verbose=True):
    data, pieces, prof = load_model()
    b, z, placements = load_board()
    placed_pieces = {p["piece"] for p in placements["placed"]}
    excluded = set(placements.get("excluded", []))
    keep = [i for i, p in enumerate(pieces) if p["id"] not in placed_pieces]
    sockets, placed_cells, fitted = effective_sockets(b, z, placements)
    cells = frontier_cells(b, sockets, placed_cells)
    # scored against the whole catalog, then the placed pieces dropped (as live.py): the re-scored candidates of a
    # socket (side_scores) must not depend on which pieces are already placed
    scored = [dict(cost=s["cost"][keep], per_side=s["per_side"][keep], alive=s["alive"][keep])
              for s in score_cells(pieces, prof, cells, d_board=d_board)]
    pieces = [pieces[i] for i in keep]
    props, sigma = propose(pieces, cells, scored, excluded, sigma)
    n_empty = b["n_empty"] - len(placed_cells)
    res = dict(sigma=sigma, d_board=d_board, n_frontier_cells=len(cells), n_pieces=len(pieces),
               n_empty_cells_left=n_empty, n_placed=len(placements["placed"]), proposals=props,
               placed_refit={k: dict(piece=v["piece"], rms=round(v["rms"], 2)) for k, v in fitted.items()})
    if verbose:
        conf = [p for p in props if p["candidates"]]
        top = np.array([p["candidates"][0]["pct_cell"] for p in conf])
        print(f"sigma={sigma:.2f} u  d_board={d_board:+.1f}: {len(cells)} frontier cells; "
              f"top1 >50 %: {(top > 50).sum()}, >80 %: {(top > 80).sum()}; "
              f"median survivors {np.median([p['survivors'] for p in props]):.0f}")
    return res


def propose(pieces, cells, scored, excluded, sigma=None):
    """Probabilities per cell and globally, candidates per cell. Shared by solve.run (batch) and live.py (workshop).
    sigma: typical rms of a good match = median of the best rms per side (cells with 1 known side)."""
    best1 = [np.sqrt(np.min(s["cost"])) for s, c in zip(scored, cells) if len(c["known"]) == 1 and np.isfinite(np.min(s["cost"]))]
    if sigma is None:
        sigma = float(np.median(best1))
    Ls = likelihoods(scored, sigma, pieces)
    # pieces x frontier cells matrix (max over rotations) + "elsewhere" column
    LL = np.stack([L.max(1) for L in Ls], 1)                          # (P, F)
    m = np.max(LL[np.isfinite(LL)])
    A = np.exp(np.where(np.isfinite(LL), LL - m, -np.inf))
    elsewhere = np.array([np.median(A[i][np.isfinite(LL[i])]) if np.isfinite(LL[i]).any() else 1e-12 for i in range(len(pieces))])
    A_full = np.concatenate([A, elsewhere[:, None]], 1)
    cap = np.r_[np.ones(len(cells)), max(1.0, len(pieces) - len(cells))]
    S = sinkhorn(A_full, cap)
    index = {p["id"]: i for i, p in enumerate(pieces)}
    excluded_by_cell = {}
    for e in excluded:
        c, pid = e.split(":")
        excluded_by_cell.setdefault(c, []).append(pid)
    props = []
    for f, (cell, L, s) in enumerate(zip(cells, Ls, scored)):
        flat = L.ravel()
        ok = np.isfinite(flat)
        if not ok.any():
            props.append(dict(cell=cell["id"], n_known=len(cell["known"]), candidates=[], survivors=0)); continue
        w = np.exp(flat[ok] - flat[ok].max())
        pc = np.zeros_like(flat); pc[ok] = w / w.sum()
        # failures confirmed by the user: (cell, piece) already tried without success
        for pid in excluded_by_cell.get(cell["id"], ()):
            if pid in index:
                pc[4 * index[pid]:4 * index[pid] + 4] = 0
        if pc.sum() > 0:
            pc /= pc.sum()
        order = np.argsort(-pc)[:5]
        cands = []
        for o in order:
            i, q = divmod(int(o), 4)
            if pc[o] <= 0:
                continue
            cands.append(dict(piece=pieces[i]["id"], rotation=q * 90, pct_cell=round(100 * float(pc[o]), 1),
                              pct_global=round(100 * float(S[i, f]), 1),
                              rms_sides=[round(float(v), 2) for v in s["per_side"][i, q]]))
        best_rms = float(np.mean(cands[0]["rms_sides"])) if cands else np.inf
        props.append(dict(cell=cell["id"], n_known=len(cell["known"]), doubtful=cell["doubtful"],
                          sides=[f"{SIDE_NAMES[k]}:{t}" for k, t, _ in cell["known"]], quality=quality_of(best_rms, sigma),
                          rms_mean=round(best_rms, 2),
                          survivors=int(np.isfinite(L).any(1).sum()), candidates=cands))
    return props, sigma


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        for d in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            r = run(d_board=d)
            best = [p["candidates"][0]["rms_sides"] for p in r["proposals"] if p["candidates"]]
            print("   median rms of the best:", np.median([np.mean(x) for x in best]).round(3))
    else:
        r = run(d_board=float(sys.argv[1]) if len(sys.argv) > 1 else 0.0)
        save_json(os.path.join(DATA, "proposals.json"), r)
