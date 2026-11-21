"""Live engine of the workshop (docs/ATELIER.md §5).

The state is a pure function of (model, journal): replaying the journal on the model always gives the same
proposals. For any set of placements, the engine gives exactly what the batch pipeline gives (solve.run with
d_board 0.25 for the candidates, reliability.py for their stability): both go through solve.propose.

Incremental: the score of a cell is cached per condition and recomputed only when its known sides change (a piece
placed next to it). An exclusion (✗) only changes the probabilities, never the scores.
"""
import os, sys, time, hashlib
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, load_json, load_raw
from match import load_model
from solve import board_sockets, frontier_cells, score_cells, propose, quality_of, placed_socket, load_score_cache, NB
from fit_view import fit_piece_pts, piece_side_points, rot_q
from wave import plate_turns, MIN_STABILITY, MIN_PCT_CELL, MIN_PCT_GLOBAL
from export_svg import display_turns
from reliability import D_BOARD
import journal

MAIN_D_BOARD = 0.25
# calibration: prior success rate per reliability bucket and its weight (in pseudo-trials), corrected by real answers
PRIORS = {"A": (0.60, 4.0), "B": (0.30, 4.0), "C": (0.10, 4.0)}
MIN_CHANCE = 0.12          # below: the candidate is not proposed in normal mode
MAX_TRIES_PER_CELL = 5     # after this many ✗ in a cell, move on (the cell sleeps until a neighbour changes)
CHANCE_TIE = 0.03          # chances closer than this are ties: the nearest cell to the previous one wins


class Condition:
    def __init__(self, name, pieces, prof, d_board):
        self.name, self.pieces, self.prof, self.d = name, pieces, prof, d_board
        self.ids = {p["id"] for p in pieces}
        self.cache = {}                                  # cell -> (sockets version, scored)


class LiveEngine:
    def __init__(self, data_dir=DATA, progress=None):
        t0 = time.time()
        prog = progress or (lambda stage, done, total: None)
        prog("board", 0, 1)
        self.data_dir = data_dir
        self.b = load_json(os.path.join(data_dir, "board.json"))
        z = np.load(os.path.join(data_dir, "board_sockets.npz"))
        self.base = board_sockets(self.b, z)
        self.C = {c["id"]: c for c in self.b["cells"]}
        self.pos = {(c["r"], c["c"]): c["id"] for c in self.b["cells"]}
        prog("catalog", 0, 1)
        self.raw = load_raw(os.path.join(data_dir, "pieces_raw.npz"))
        _, pieces, prof = load_model(os.path.join(data_dir, "pieces.json"), os.path.join(data_dir, "pieces_raw.npz"))
        self.P = {p["id"]: p for p in pieces}
        cats = [("all photos", pieces, prof)]
        cat_dir = os.path.join(data_dir, "model", "catalogs")
        for d in sorted(os.listdir(cat_dir)) if os.path.isdir(cat_dir) else []:
            _, pc, pr = load_model(os.path.join(cat_dir, d, "pieces.json"), os.path.join(cat_dir, d, "pieces_raw.npz"))
            cats.append((f"photos of {d}", pc, pr))
        self.conditions = [Condition(f"{n}, d_board {d}", pc, pr, d) for n, pc, pr in cats for d in D_BOARD]
        self.main = next(i for i, c in enumerate(self.conditions) if c.name == f"all photos, d_board {MAIN_D_BOARD}")
        cpath = os.path.join(data_dir, "model", "score_cache.npz")
        self.cached_scores = load_score_cache(cpath) if os.path.exists(cpath) else 0   # model_export: start-up in seconds
        vpath = os.path.join(data_dir, "model", "version.json")
        self.model_version = load_json(vpath)["version"] if os.path.exists(vpath) else "dev"
        self._tick = 0                                  # global, never reset: a cache key is never reused
        self.reset()
        self.load_time = time.time() - t0

    # ------------------------------------------------------------------ state
    def reset(self):
        self.sockets = dict(self.base)
        self.filled, self.placed, self.excluded = set(), [], set()
        self.version = {}                               # cell -> tick of the last change of its known sides (0 = board)
        self.fitted = {}                                # cell -> placed piece fitted in the cell

    def neighbours(self, cid):
        r, c = self.C[cid]["r"], self.C[cid]["c"]
        for k, (dr, dc) in enumerate(NB):
            nid = self.pos.get((r + dr, c + dc))
            if nid is not None:
                yield k, nid

    def place(self, cid, pid, q):
        """Same logic as solve.effective_sockets for one placement."""
        if not self.C[cid]["empty"]:            # covered by a recalibration photo: already a measured board cell
            self.placed.append(dict(cell=cid, piece=pid, rotation=q * 90))
            return True
        known = {k: self.sockets[(cid, k)] for k in range(4) if (cid, k) in self.sockets}
        if not known:
            return False
        full, R, ma, mb, rms = fit_piece_pts(self.raw[pid], q, known)
        self.fitted[cid] = dict(piece=pid, rotation=q * 90, contour=full, rms=rms)
        self.filled.add(cid)
        self.placed.append(dict(cell=cid, piece=pid, rotation=q * 90))
        self._tick += 1
        self.version[cid] = self._tick
        for k, nid in self.neighbours(cid):
            if not self.C[nid]["empty"] or nid in self.filled:
                continue
            j = (k - q) % 4
            side = (R @ (rot_q(piece_side_points(self.raw[pid], j), q) - ma).T).T + mb
            self.sockets[(nid, (k + 2) % 4)] = placed_socket(side[::-1].copy())
            self._tick += 1
            self.version[nid] = self._tick
        return True

    def apply(self, placements):
        """Rebuilds the state for a placements dict (journal.placements or data/placements.json).
        Scores cached for cells that kept the board sides (version 0) are reused; any changed cell is rescored."""
        self.reset()
        # A placement with no known side (its only placed neighbour was taken out since) is retried after the others,
        # which may give it sides; failing that it stays placed, unfitted (the cell is occupied, the piece is used).
        todo = list(placements["placed"])
        while todo:
            left = [x for x in todo if not self.place(x["cell"], x["piece"], x["rotation"] // 90)]
            if len(left) == len(todo):
                for x in left:
                    self.filled.add(x["cell"])
                    self.placed.append(dict(cell=x["cell"], piece=x["piece"], rotation=x["rotation"]))
                break
            todo = left
        self.excluded = set(placements.get("excluded", []))

    def _cell_version(self, cid):
        return self.version.get(cid, 0)

    # ------------------------------------------------------------------ proposals
    def solve(self, cond):
        placed_ids = {x["piece"] for x in self.placed}
        avail = np.array([i for i, p in enumerate(cond.pieces) if p["id"] not in placed_ids], int)
        pieces = [cond.pieces[i] for i in avail]
        cells = frontier_cells(self.b, self.sockets, self.filled)
        scored = []
        for cell in cells:
            key, v = cell["id"], self._cell_version(cell["id"])
            hit = cond.cache.get(key)
            if hit is None or hit[0] != v:
                hit = (v, score_cells(cond.pieces, cond.prof, [cell], d_board=cond.d)[0])
                cond.cache[key] = hit
            s = hit[1]
            scored.append(dict(cost=s["cost"][avail], per_side=s["per_side"][avail], alive=s["alive"][avail]))
        props, sigma = propose(pieces, cells, scored, self.excluded)
        return props, sigma, cells

    def proposals(self, progress=None):
        """Candidates of the main condition with their stability over all conditions (= reliability.py)."""
        prog = progress or (lambda stage, done, total: None)
        firsts, results = [], []
        for n, cond in enumerate(self.conditions):
            prog("reliability", n, len(self.conditions))
            props, sigma, cells = self.solve(cond)
            results.append((props, sigma, cells))
            firsts.append({p["cell"]: (p["candidates"][0]["piece"], p["candidates"][0]["rotation"])
                           for p in props if p["candidates"]})
        props, sigma, cells = results[self.main]
        for p in props:
            for c in p["candidates"]:
                eligible = [k for k, cond in enumerate(self.conditions) if c["piece"] in cond.ids]
                win = sum(1 for k in eligible if firsts[k].get(p["cell"]) == (c["piece"], c["rotation"]))
                c["stability"] = round(win / len(eligible), 2) if eligible else 0.0
        return props, sigma


# ---------------------------------------------------------------------- calibration and choice (pure functions)
def bucket(prop, cand, sigma):
    """Reliability bucket of a candidate: A = the wave rule, B = stable in at least half the conditions, C = other."""
    q = quality_of(float(np.mean(cand["rms_sides"])), sigma)
    if (not prop.get("doubtful") and q != "poor" and cand.get("stability", 0) >= MIN_STABILITY
            and cand["pct_cell"] >= MIN_PCT_CELL and cand["pct_global"] >= MIN_PCT_GLOBAL):
        return "A", q
    if q != "poor" and cand.get("stability", 0) >= 0.5:
        return "B", q
    return "C", q


def calibration(outcomes):
    """Chance per bucket = prior corrected by the real answers (Beta posterior mean)."""
    out = {}
    for b, (m, w) in PRIORS.items():
        n = sum(1 for x, _ in outcomes if x == b)
        k = sum(1 for x, ok in outcomes if x == b and ok)
        out[b] = dict(chance=(m * w + k) / (w + n), trials=n, successes=k)
    return out


def choose(engine, props, sigma, events):
    """Next proposal: dict or None. Deterministic: depends only on the state and the journal."""
    eff = journal.effective(events)
    cal = calibration(journal.outcomes(events))
    skipped = journal.skipped_cells(events, lambda c: [n for _, n in engine.neighbours(c)])
    tries = {}
    for e in eff:
        if e["type"] == "does_not_fit":
            tries[e["cell"]] = tries.get(e["cell"], 0) + 1
    last = eff[-1] if eff else None
    options = []
    for p in props:
        if not p["candidates"] or p["cell"] in skipped:
            continue
        c = p["candidates"][0]
        b, q = bucket(p, c, sigma)
        options.append(dict(prop=p, cand=c, bucket=b, quality=q, chance=cal[b]["chance"],
                            tries=tries.get(p["cell"], 0)))
    if not options:
        return None, cal
    good = [o for o in options if o["chance"] >= MIN_CHANCE and o["quality"] != "poor"
            and not o["prop"].get("doubtful") and o["tries"] < MAX_TRIES_PER_CELL]
    weak = not good
    pool = good or options
    # after ✗, stay on the same cell while it still has an acceptable candidate (peel its candidates)
    if last and last["type"] == "does_not_fit":
        same = [o for o in pool if o["prop"]["cell"] == last["cell"]]
        if same and not weak:
            return finish(engine, same[0], cal, weak), cal
    ref = engine.C[last["cell"]] if last else None

    def key(o):
        d = 0.0 if ref is None else np.hypot(engine.C[o["prop"]["cell"]]["r"] - ref["r"], engine.C[o["prop"]["cell"]]["c"] - ref["c"])
        return (-round(o["chance"] / CHANCE_TIE), d, -o["cand"].get("stability", 0), -o["cand"]["pct_cell"], o["prop"]["cell"])
    return finish(engine, min(pool, key=key), cal, weak), cal


def finish(engine, o, cal, weak):
    p, c = o["prop"], o["cand"]
    pid, q = c["piece"], c["rotation"] // 90
    piece = engine.P[pid]
    cell = engine.C[p["cell"]]
    known = {k: engine.sockets[(p["cell"], k)] for k in range(4) if (p["cell"], k) in engine.sockets}
    full, R, ma, mb, rms = fit_piece_pts(engine.raw[pid], q, known)
    pid_hash = hashlib.sha1(f"{p['cell']}:{pid}:{q}:{len(engine.placed)}:{len(engine.excluded)}".encode()).hexdigest()[:10]
    return dict(
        id=pid_hash, cell=p["cell"], r=cell["r"], c=cell["c"], center=[round(v, 1) for v in cell["center_u"]],
        piece=pid, sheet=piece["sheet"], row=piece["row"], col=piece["col"], rotation=q * 90,
        plate_turns=plate_turns(piece, q * 90), display_turns=display_turns(piece),
        other_rotations=[dict(rotation=((q + k) % 4) * 90, plate_turns=plate_turns(piece, ((q + k) % 4) * 90)) for k in (1, 2, 3)],
        rank=o["tries"] + 1, n_known=p["n_known"], chance=round(o["chance"], 3), bucket=o["bucket"],
        stability=c.get("stability"), pct_cell=c["pct_cell"], quality=o["quality"], weak=weak,
        fit=np.round(full[::3], 1).tolist(), fit_rms=round(rms, 2),
        alternatives=[dict(piece=a["piece"], rotation=a["rotation"], pct_cell=a["pct_cell"]) for a in p["candidates"][1:4]])


def fit_quality(engine, cid, pid, q):
    """Mean deviation (u) of a piece fitted in a cell: used to warn about a doubtful ✓."""
    known = {k: engine.sockets[(cid, k)] for k in range(4) if (cid, k) in engine.sockets}
    if not known:
        return None
    return fit_piece_pts(engine.raw[pid], q, known)[4]
