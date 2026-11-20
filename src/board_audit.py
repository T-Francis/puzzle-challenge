"""Audit of the board holes that touch a pale moon piece.

A pale moon piece is almost as bright as the paper of an empty hole: if its cut line is not seen, the hole outline goes
around part of the piece and the sockets built on it are wrong (L21-C4, 2026-11-20). Re-run after any new board photo.

Pale piece = filled cell whose box holds paper-like pixels (bright, unsaturated: board_model.paper_mask) outside the
empty mask. For every empty cell with a pale neighbour (8-neighbourhood), automatic indicators:
  - dim_in_hole: area (u²) of the hole, within the cell box + tabs, dimmer than the local paper (a pale piece kept as
    paper is ≈ 25 grey levels dimmer: L21-C4). Also computed on the raw mask (before clear_filled_cells) as a check
    that the indicator does see the L21-C4 case;
  - per socket: missing (neighbour placed, no socket extracted), chord length vs the cell pitch, best match among the
    412 pieces x sides of the required type in units of sigma (baseline: every socket of the board).
Contact sheets: for each audited cell, the rectified reference photo and a second view: the most central of the other
board photos of the manifest, or the reference photo itself unrectified (native resolution) when there is none
(SIFT homography, local refinement), with the mask outline (cyan), the sockets (green) and the missing sides (red).

Outputs: reports/board_audit.md, reports/board_audit_<n>.jpg.
Usage: sh scripts/pipeline.sh --script src/board_audit.py
"""
import os, sys, glob, hashlib
import cv2
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, BOARD_DIR, REPORTS, SIDE_NAMES, MANIFEST, load_json, save_json, load_photo, photo_key
from match import load_model, offset, D_PIECE
from solve import load_board, local_profile, side_scores
from board_model import PX_PER_U, paper_mask, empty_mask, expected_empty_cells

PALE_SHARE = 0.01        # a filled cell is pale if ≥ 1 % of its box looks like paper outside the empty mask
DIM_DROP = 12            # grey levels below the local paper: "dim" pixel (same threshold as clear_filled_cells)
DIM_FLAG = 50            # u² of dim paper in a cell box (+ tabs) to flag it (L21-C3 before the fix: see the report)
TILE_MARGIN = 0.6        # tile = cell ± (0.5 + margin) cell
PER_PAGE = 12
D_BOARD = 0.25                    # board shadow offset used by the engine (solve.run(d_board=0.25)); D_PIECE: match.D_PIECE


def cell_box(C, cid, cw, ch, margin=0.0, shape=None):
    cx, cy = C[cid]["center_u"]
    w, h = cw * (0.5 + margin), ch * (0.5 + margin)
    x0, y0, x1, y1 = (int(v * PX_PER_U) for v in (cx - w, cy - h, cx + w, cy + h))
    if shape is not None:
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, shape[1]), min(y1, shape[0])
    return x0, y0, x1, y1


def pale_cells(b, C, pm, empty):
    """{filled cell: share of paper-like pixels in its box, outside the empty mask}."""
    out = {}
    for c in b["cells"]:
        if c["empty"]:
            continue
        x0, y0, x1, y1 = cell_box(C, c["id"], b["cell_w_u"], b["cell_h_u"], -0.05, pm.shape)
        m = ~empty[y0:y1, x0:x1]
        if m.sum():
            share = float((pm[y0:y1, x0:x1] & m).sum() / m.sum())
            if share >= PALE_SHARE:
                out[c["id"]] = share
    return out


def dim_map(g, mask):
    """Pixels of the mask (edges and their shadow excluded) dimmer than the local paper level.
    Local paper = smoothed mean of the paper pixels (normalised convolution, σ = 25 u): follows the lighting gradient."""
    inner = (cv2.erode(mask.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0).astype(np.float32)
    s = 25 * PX_PER_U
    num = cv2.GaussianBlur(g * inner, (0, 0), s)
    den = cv2.GaussianBlur(inner, (0, 0), s)
    local = num / np.maximum(den, 1e-3)
    return (inner > 0) & (g < local - DIM_DROP)


def dim_area(dim, box):
    x0, y0, x1, y1 = box
    return float(dim[y0:y1, x0:x1].sum()) / PX_PER_U ** 2


def socket_best(z, cells, pieces, prof, sigma):
    """{(cell, k): (required type, chord length, best rms / sigma)} for every extracted socket of the board."""
    types = np.array([[s["type"] for s in p["sides"]] for p in pieces])
    Pp = offset(prof, D_PIECE)
    pkey = hashlib.sha1(Pp.tobytes()).hexdigest()
    out = {}
    for c in cells:
        for k, s in enumerate(c["sides"]):
            key = f"{c['id']}__{s['name']}__points"
            if not s.get("extracted") or key not in z.files:
                continue
            sp, req = local_profile(z[key])
            if req == "F":
                continue
            S = offset(sp[None, None], -D_BOARD)[0, 0]
            best = float(side_scores(S, req, Pp, types, pkey)[types == req].min())
            out[(c["id"], k)] = (req, float(s["L"]), best / sigma)
    return out


# ---------------------------------------------------------------- second view (other photos, else the raw reference)
def view_homographies(rimg):
    """{photo file: (H rectified px -> photo px, inlier pairs (rect px, photo px))} for the second views."""
    sift = cv2.SIFT_create(nfeatures=40000)
    small = cv2.resize(cv2.cvtColor(rimg, cv2.COLOR_BGR2GRAY), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    kr, dr = sift.detectAndCompute(small, None)
    pr = np.float32([k.pt for k in kr]) * 2
    out = {}
    sources = [o["file"] for o in MANIFEST["board"].get("others", [])] or [MANIFEST["board"]["reference"]]
    for f in sources:
        img = cv2.cvtColor(load_photo(f), cv2.COLOR_BGR2GRAY)
        qs = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        kq, dq = sift.detectAndCompute(qs, None)
        pq = np.float32([k.pt for k in kq]) * 2
        m = cv2.FlannBasedMatcher(dict(algorithm=1, trees=5), dict(checks=64)).knnMatch(dq, dr, k=2)
        good = [a for a, b2 in (x for x in m if len(x) == 2) if a.distance < 0.75 * b2.distance]
        src = pr[[g.trainIdx for g in good]]
        dst = pq[[g.queryIdx for g in good]]
        H, inl = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
        inl = inl.ravel() > 0
        print(f"{f}: {len(good)} matches, {inl.sum()} inliers", flush=True)
        out[f] = (H, src[inl], dst[inl], img.shape)
    return out


def view_tile(views, vimgs, box, centre_px):
    """Tile of the most central second-view photo over the rectified box (px), with a homography refined locally."""
    best, bestd = None, None
    for f, (H, src, dst, shape) in views.items():
        p = cv2.perspectiveTransform(np.float32([[centre_px]]), H)[0, 0]
        h, w = shape
        if not (0 <= p[0] < w and 0 <= p[1] < h):
            continue
        d = max(abs(p[0] / w - 0.5), abs(p[1] / h - 0.5))
        if bestd is None or d < bestd:
            best, bestd = f, d
    if best is None:
        return None, None
    H, src, dst, _ = views[best]
    near = np.linalg.norm(src - centre_px, axis=1) < 300 * PX_PER_U
    if near.sum() >= 40:
        Hl, _ = cv2.findHomography(src[near], dst[near], cv2.RANSAC, 4.0)
        if Hl is not None:
            H = Hl
    x0, y0, x1, y1 = box
    T = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], float)
    tile = cv2.warpPerspective(vimgs[best], H @ T, (x1 - x0, y1 - y0), flags=cv2.INTER_AREA | cv2.WARP_INVERSE_MAP,
                               borderValue=(255, 255, 255))
    return tile, photo_key(best)


def draw_overlay(tile, box, mask, sockets, missing_sides):
    x0, y0, x1, y1 = box
    t = tile.copy()
    cs, _ = cv2.findContours(mask[y0:y1, x0:x1].astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    cv2.drawContours(t, cs, -1, (255, 255, 0), 1)
    for pts in sockets:
        p = np.round((pts * PX_PER_U - [x0, y0]) * 8).astype(np.int32)
        cv2.polylines(t, [p], False, (0, 200, 0), 2, cv2.LINE_AA, shift=3)
    for a, b2 in missing_sides:
        cv2.line(t, tuple(int(v) for v in (a * PX_PER_U - [x0, y0])), tuple(int(v) for v in (b2 * PX_PER_U - [x0, y0])),
                 (0, 0, 255), 3)
    return t


def main():
    rimg = cv2.imread(os.path.join(BOARD_DIR, "board_rectified.jpg"))
    empty = cv2.imread(os.path.join(DATA, "board_empty_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
    raw_empty = empty_mask(rimg, expected=expected_empty_cells())[0] > 0
    g = cv2.blur(cv2.cvtColor(rimg, cv2.COLOR_BGR2GRAY).astype(np.float32), (9, 9))
    b, z, _ = load_board()
    cw, ch = b["cell_w_u"], b["cell_h_u"]
    C = {c["id"]: c for c in b["cells"]}
    pos = {(c["r"], c["c"]): c for c in b["cells"]}
    pm = paper_mask(rimg) > 0
    pale = pale_cells(b, C, pm, empty)
    sigma = load_json(os.path.join(DATA, "evaluation.json"))["fusion of all photos"]["sigma_u"]
    _, pieces, prof = load_model()
    best = socket_best(z, [c for c in b["cells"] if c["empty"]], pieces, prof, sigma)
    save_json(os.path.join(REPORTS, "board_audit.json"),
              dict(sigma_u=sigma, sockets={f"{cid}:{SIDE_NAMES[k]}": dict(type=v[0], chord_u=round(v[1], 2),
                                                                           best_sigma=round(v[2], 3))
                                           for (cid, k), v in sorted(best.items())}))
    all_best = np.array([v[2] for v in best.values()])
    p95 = float(np.quantile(all_best, 0.95))
    # chord length of the sockets relative to the pitch: flag outside the central 95 % of the board
    rel = {key: v[1] - (cw if key[1] in (0, 2) else ch) for key, v in best.items()}
    lo, hi = np.quantile(list(rel.values()), [0.025, 0.975])
    dim = dim_map(g, empty)
    dim_raw = dim_map(g, raw_empty)

    # cells to audit
    rows = []
    for c in b["cells"]:
        if not c["empty"]:
            continue
        hits = [pos[(c["r"] + dr, c["c"] + dc)]["id"] for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                if (dr or dc) and (c["r"] + dr, c["c"] + dc) in pos and pos[(c["r"] + dr, c["c"] + dc)]["id"] in pale]
        no_fit = any(best.get((c["id"], k), (0, 0, 0))[2] > p95 for k in range(4))
        if not hits and not no_fit:          # audited: touches a pale piece, or a socket that no piece fits
            continue
        box = cell_box(C, c["id"], cw, ch, 0.35, empty.shape)
        socks, flags = [], []
        for k, s in enumerate(c["sides"]):
            if s["neighbour"] != "placed":
                continue
            if not s.get("extracted"):
                flags.append(f"{SIDE_NAMES[k]} missing")
                continue
            v = best.get((c["id"], k))
            if v is None:
                continue
            req, L, bs = v
            socks.append(f"{SIDE_NAMES[k]}:{req} {bs:.2f}σ")
            if bs > p95:
                flags.append(f"{SIDE_NAMES[k]} best {bs:.1f}σ")
            if not lo <= rel[(c["id"], k)] <= hi:
                flags.append(f"{SIDE_NAMES[k]} length {L:.0f} u")
        d, d_raw = dim_area(dim, box), dim_area(dim_raw, box)
        if d > DIM_FLAG:
            flags.append(f"dim paper {d:.0f} u²")
        rows.append(dict(cell=c["id"], pale=sorted(hits, key=lambda h: -pale[h]),
                         pale_max=max([pale[h] for h in hits], default=0.0), socks=socks, flags=flags, dim=d,
                         dim_raw=d_raw))
    rows.sort(key=lambda r: (-len(r["flags"]), -r["dim"], -r["pale_max"]))
    # sockets facing the frame (outer ring of the puzzle) vs facing an inner placed piece
    def faces_frame(key):
        c = C[key[0]]
        dr, dc = [(-1, 0), (0, 1), (1, 0), (0, -1)][key[1]]
        r2, c2 = c["r"] + dr, c["c"] + dc
        return r2 in (0, b["rows"] - 1) or c2 in (0, b["cols"] - 1)
    frame_split = {}
    for name, sel in (("facing the frame", True), ("facing an inner piece", False)):
        v = np.array([x[2] for key, x in best.items() if faces_frame(key) == sel])
        frame_split[name] = v
        print(f"best/σ {name}: n={len(v)} median {np.median(v):.2f} p75 {np.quantile(v, .75):.2f} "
              f"p90 {np.quantile(v, .9):.2f} > p95: {(v > p95).sum()}")
    # geometry of every socket: chord vs pitch, knob centre vs middle of the chord
    geo = {}
    for key in best:
        cid, k = key
        sp, req = local_profile(z[f"{cid}__{SIDE_NAMES[k]}__points"])
        L = sp[-1, 0]
        i = np.argmax(np.abs(sp[:, 1]))
        geo[key] = (rel[key], sp[i, 0] - L / 2)
    for name, sel in (("facing the frame", True), ("facing an inner piece", False)):
        ks = [key for key in best if faces_frame(key) == sel]
        dl = np.array([abs(geo[key][0]) for key in ks])
        dk = np.array([abs(geo[key][1]) for key in ks])
        bs = np.array([best[key][2] for key in ks])
        bad = (dl > 5) | (dk > 8)
        print(f"geometry {name}: |chord−pitch| median {np.median(dl):.1f} p90 {np.quantile(dl, .9):.1f}; "
              f"|knob offset| median {np.median(dk):.1f} p90 {np.quantile(dk, .9):.1f}; "
              f"suspect (>5 u or >8 u) {bad.sum()}/{len(ks)}, their best/σ median {np.median(bs[bad]) if bad.any() else 0:.2f}"
              f" vs others {np.median(bs[~bad]):.2f}")
    n_pale = sum(1 for r in rows if r["pale"])
    print(f"pale filled cells {len(pale)}; empty cells touching one {n_pale}, other cells with a socket no piece fits "
          f"{len(rows) - n_pale}; flagged {sum(1 for r in rows if r['flags'])}; best/σ p95 over {len(all_best)} "
          f"sockets = {p95:.2f}; length deviation 95 % range [{lo:.1f}, {hi:.1f}] u", flush=True)
    for r in rows:
        if r["flags"] or r["cell"] in ("L21-C3", "L22-C3"):
            print(f"  {r['cell']:8s} dim {r['dim']:5.0f} (raw {r['dim_raw']:5.0f})  {'; '.join(r['flags'])}")

    # contact sheets
    views = view_homographies(rimg)
    vimgs = {f: load_photo(f) for f in views}
    for old in glob.glob(os.path.join(REPORTS, "board_audit_*.jpg")):
        os.remove(old)
    tiles = []
    for r in rows:
        c = C[r["cell"]]
        box = cell_box(C, r["cell"], cw, ch, TILE_MARGIN, empty.shape)
        sk = [z[f"{r['cell']}__{s['name']}__points"] for s in c["sides"] if s.get("extracted")
              and f"{r['cell']}__{s['name']}__points" in z.files]
        cx, cy = c["center_u"]
        corners = np.array([[cx - cw / 2, cy - ch / 2], [cx + cw / 2, cy - ch / 2], [cx + cw / 2, cy + ch / 2],
                            [cx - cw / 2, cy + ch / 2]])
        miss = [(corners[k], corners[(k + 1) % 4]) for k, s in enumerate(c["sides"])
                if s["neighbour"] == "placed" and not s.get("extracted")]
        x0, y0, x1, y1 = box
        left = draw_overlay(rimg[y0:y1, x0:x1], box, empty, sk, miss)
        qt, qname = view_tile(views, vimgs, box, np.array([cx, cy]) * PX_PER_U)
        right = draw_overlay(qt, box, empty, sk, miss) if qt is not None else np.full_like(left, 255)
        pair = np.hstack([left, np.full((left.shape[0], 6, 3), 255, np.uint8), right])
        head = np.full((54, pair.shape[1], 3), 255, np.uint8)
        col = (0, 0, 200) if r["flags"] else (0, 0, 0)
        cv2.putText(head, f"{r['cell']}  (reference | {qname})", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65, col, 2)
        cv2.putText(head, ("; ".join(r["flags"]) or "no flag")[:70], (6, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 1)
        tiles.append(np.vstack([head, pair]))
    th = max(t.shape[0] for t in tiles)
    tw = max(t.shape[1] for t in tiles)
    pages = []
    for n in range(0, len(tiles), PER_PAGE):
        grid = []
        chunk = tiles[n:n + PER_PAGE]
        for i in range(0, len(chunk), 3):
            line = [cv2.copyMakeBorder(t, 0, th - t.shape[0], 0, tw - t.shape[1], cv2.BORDER_CONSTANT, value=(255,) * 3)
                    for t in chunk[i:i + 3]]
            line += [np.full((th, tw, 3), 255, np.uint8)] * (3 - len(line))
            grid.append(cv2.copyMakeBorder(np.hstack([cv2.copyMakeBorder(t, 8, 8, 8, 8, cv2.BORDER_CONSTANT,
                                                                         value=(200,) * 3) for t in line]),
                                           0, 0, 0, 0, cv2.BORDER_CONSTANT))
        name = f"board_audit_{n // PER_PAGE + 1}.jpg"
        cv2.imwrite(os.path.join(REPORTS, name), np.vstack(grid), [cv2.IMWRITE_JPEG_QUALITY, 85])
        pages.append((name, [r["cell"] for r in rows[n:n + PER_PAGE]]))

    # report
    l21 = next((r for r in rows if r["cell"] == "L21-C3"), None)
    rep = ["# Board audit — holes touching a pale moon piece", "",
           "Generated by `src/board_audit.py`. Re-run after any new board photo.", "",
           f"- Pale filled cells (≥ {PALE_SHARE:.0%} of the box paper-like outside the empty mask): **{len(pale)}**",
           f"- Empty cells touching one (8-neighbourhood): **{n_pale}**; other empty cells audited because a socket "
           f"fits no piece (> p95 below, whole board): **{len(rows) - n_pale}**; audited cells with at least one flag: "
           f"**{sum(1 for r in rows if r['flags'])}**",
           f"- Best match of a socket among the pieces, in σ = {sigma} u, over the {len(all_best)} sockets of the board: "
           f"median {np.median(all_best):.2f}, p95 {p95:.2f}, max {all_best.max():.2f} (flag: > p95)",
           *[f"  - {name}: {len(v)} sockets, median {np.median(v):.2f}, p75 {np.quantile(v, .75):.2f}, "
             f"p90 {np.quantile(v, .9):.2f}, above p95: {(v > p95).sum()}" for name, v in frame_split.items()],
           f"- Chord length − pitch, central 95 % of the board: [{lo:.1f}, {hi:.1f}] u (flag: outside)",
           f"- Dim paper (hole pixels ≥ {DIM_DROP} grey levels below the local paper, flag > {DIM_FLAG} u²). Check on "
           f"the raw mask (before `clear_filled_cells`): L21-C3 "
           + (f"{l21['dim_raw']:.0f} u² raw → {l21['dim']:.0f} u² now" if l21 else "not audited"), "",
           "Contact sheets (left: reference photo rectified; right: second view, named on each tile; cyan = "
           "outline of the empty mask, green = sockets, red = missing socket):", ""]
    rep += [f"- `reports/{n}`: {', '.join(c)}" for n, c in pages]
    rep += ["", "| Cell | Pale neighbours | Sockets (best/σ) | Dim paper u² (raw) | Flags |", "|---|---|---|---|---|"]
    for r in rows:
        rep.append(f"| {r['cell']} | {', '.join(r['pale'][:4])} | {', '.join(r['socks']) or '—'} | "
                   f"{r['dim']:.0f} ({r['dim_raw']:.0f}) | {'; '.join(r['flags']) or '—'} |")
    with open(os.path.join(REPORTS, "board_audit.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rep) + "\n")
    print("reports/board_audit.md,", ", ".join(n for n, _ in pages))


if __name__ == "__main__":
    main()
