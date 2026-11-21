"""Stage 3 — board model.

Puzzle photo -> outer corners of the frame (fitted lines) -> homography to the 28 x 36 grid (reference photo of the
manifest, in reading direction) -> occupancy of the cells -> sockets (edges of the placed pieces around each empty
cell) -> data/board.json
"""
import os, sys
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter, gaussian_filter1d, map_coordinates
from skimage.measure import find_contours
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, BOARD_DIR, REPORTS, BOARD_PHOTO, SIDE_NAMES, load_json, save_json, load_photo, cell_rc

NC, NR = 28, 36
PX_PER_U = 2.0
NPROF = 128


PAPER_LEVEL = 220.0     # grey level of the paper after normalisation (= the 2026-11-18 reference, where it was even)
DISAGREE_SURE = (0.2, 0.8)  # paper share at a cell centre: between these, a disagreement with the journal is too close to call


def illumination(rimg):
    """Local level of the paper (grey levels, full size) and the paper samples used (small image, 1/8)."""
    H, W = rimg.shape[:2]
    f = 8
    small = cv2.resize(rimg, (W // f, H // f), interpolation=cv2.INTER_AREA)
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)
    sat = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[..., 1]
    cell = 95.0 * PX_PER_U / f
    # paper samples: bright, unsaturated, smooth at the scale of a few u (pale moons have craters and cut lines), in
    # plates of ≥ 2 cells (a pale moon piece is one cell; its moon is cut into pieces)
    std = np.sqrt(np.maximum(cv2.blur(g * g, (5, 5)) - cv2.blur(g, (5, 5)) ** 2, 0))
    cand = ((sat < 60) & (g > 0.55 * np.percentile(g, 95)) & (std < 4)).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(cand, connectivity=4)
    big = np.zeros(n, bool)
    big[1:] = st[1:, 4] > 2 * cell * cell
    cand = big[lab].astype(np.float32)

    def level(c):
        out = []
        for s in (2, 8):
            num = gaussian_filter(g * c, s * cell)
            den = gaussian_filter(c, s * cell)
            out.append((num / np.maximum(den, 1e-6), den))
        (b2, d2), (b8, _) = out
        return np.where(d2 > 0.05, b2, b8)
    B = level(cand)
    for _ in range(3):
        c = cand * (np.abs(g / B - 1) < 0.12)
        B = level(c)
    return cv2.resize(B, (W, H), interpolation=cv2.INTER_CUBIC), c


def normalize_illumination(rimg):
    """Rectified board with an even lighting: every pixel divided by the local level of the paper, brought to
    PAPER_LEVEL. The absolute thresholds below (paper > 150, dim paper −12, contour levels) were tuned on an evenly lit
    photo; the 64 Mpx photo of 2026-11-20 has a soft shadow at the bottom right (paper ≈ 150 instead of 210), which
    the paper test rejected. Paper level = normalised convolution of smooth paper plates (σ = 2 cells), iterated to
    drop shadows of pieces; where no paper is near (moon area), wider (σ = 8 cells): pale moons must NOT serve as
    paper samples, or they would be brought to the paper level. Pieces lie under the same light: dividing the whole
    image keeps the contrast piece / paper."""
    B, _ = illumination(rimg)
    gain = (PAPER_LEVEL / np.maximum(B, 20.0))[..., None]
    print(f"illumination: paper level p5 / p50 / p95 = {np.percentile(B, [5, 50, 95]).round(0).tolist()}")
    return np.clip(rimg.astype(np.float32) * gain, 0, 255).astype(np.uint8)


def paper_mask(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    w = ((g > 150) & (hsv[..., 1] < 60)).astype(np.uint8)
    return cv2.morphologyEx(w, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))


def bridge_thin_gaps(paper, allowed, d=4):
    """Pixels of `allowed` with paper on BOTH sides within d px (horizontally, vertically or diagonally): pen strokes
    and seams between the sheets of paper the puzzle lies on (64 Mpx photo of 2026-11-20). A cut line between a pale
    moon piece and the paper has paper on one side only and is kept."""
    P = np.pad(paper > 0, d)
    h, w = paper.shape
    out = np.zeros(paper.shape, bool)
    for vy, vx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = np.zeros(paper.shape, bool); b = np.zeros(paper.shape, bool)
        for k in range(1, d + 1):
            a |= P[d + k * vy:d + k * vy + h, d + k * vx:d + k * vx + w]
            b |= P[d - k * vy:d - k * vy + h, d - k * vx:d - k * vx + w]
        out |= a & b
    return out & (allowed > 0) & (paper == 0)


CELL_W_U, CELL_H_U = 90.2, 100.4     # median core of standing pieces (u) -> one cell of the rectified board


def expected_empty_cells():
    """Empty cells known without the photo: params.json empty_cells (recount of 2026-11-19) minus the placements."""
    params = load_json(os.path.join(DATA, "params.json"))
    cells = params.get("empty_cells")
    if not cells:
        return None
    placed = {x["cell"] for x in load_json(os.path.join(DATA, "placements.json")).get("placed", [])}
    return [c for c in cells if c not in placed]


def holds_cells(lab, n, cells, half_u=20.0, share=0.15):
    """Per component label: number of the given cells whose centre window (± half_u) it covers by ≥ share."""
    out = np.zeros(n, int)
    h = int(half_u * PX_PER_U)
    for cid in cells:
        r, c = cell_rc(cid)
        x, y = int((c + 0.5) * CELL_W_U * PX_PER_U), int((r + 0.5) * CELL_H_U * PX_PER_U)
        win = lab[max(0, y - h):y + h, max(0, x - h):x + h]
        v, k = np.unique(win, return_counts=True)
        for vi, ki in zip(v, k):
            if vi and ki > share * (2 * h) ** 2:
                out[vi] += 1
    return out


def empty_mask(rimg, tex_max=1.8, expected=None):
    """Empty paper = bright, unsaturated areas, cut along the thin dark lines (edges of the pieces).

    Hole or pale moon piece? With `expected` (list of the cells known empty, params.json): a paper-like area is a hole
    iff it covers the centre of an expected empty cell. On a 64 Mpx board photo, blur and soft shadows
    make a small hole among the moons (L18-C23) look like a pale moon piece (grey, saturation, texture alike), and the
    paper grain raises the texture of the big holes above the former threshold. Without `expected`: former rule,
    smooth areas only (median local std < tex_max: pale moons have craters and cut lines)."""
    w = paper_mask(rimg)
    g = cv2.cvtColor(rimg, cv2.COLOR_BGR2GRAY).astype(np.float32)
    tex = np.sqrt(np.maximum(cv2.blur(g * g, (15, 15)) - cv2.blur(g, (15, 15)) ** 2, 0))
    # cut along the thin dark lines (edges and cuts of the pieces): separates a pale moon from the paper
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    lines = cv2.dilate((bh > 14).astype(np.uint8), np.ones((3, 3), np.uint8))
    w_full = w
    w = w & (1 - lines)
    n, lab, st, _ = cv2.connectedComponentsWithStats(w)
    holds = holds_cells(lab, n, expected) if expected else None
    keep = np.zeros(n, bool)
    comps = []
    for i in range(1, n):
        if st[i, 4] < 3000:
            continue
        m = lab == i
        t = float(np.median(tex[m]))
        if (holds[i] > 0) if expected else (t < tex_max):
            keep[i] = True
            comps.append(dict(area_px=int(st[i, 4]), texture=t, bbox=st[i, :4].tolist(),
                              expected_cells=int(holds[i]) if expected else None))
    if expected:
        # smooth paper-like areas holding no expected cell (former rule): a hole the journal says filled (a placement
        # recorded but not done, a piece removed) — reported, never silently dropped (workshop recalibration)
        unexp = [i for i in range(1, n) if not keep[i] and st[i, 4] >= 0.5 * CELL_W_U * CELL_H_U * PX_PER_U ** 2
                 and float(np.median(tex[lab == i])) < tex_max]
        if unexp:
            m = np.isin(lab, unexp)
            h = int(20.0 * PX_PER_U)
            exp_set = set(expected)
            cells = []
            for r in range(NR):
                for c in range(NC):
                    cid = f"L{r + 1}-C{c + 1}"
                    x, y = int((c + 0.5) * CELL_W_U * PX_PER_U), int((r + 0.5) * CELL_H_U * PX_PER_U)
                    if cid not in exp_set and m[max(0, y - h):y + h, max(0, x - h):x + h].mean() > 0.5:
                        cells.append(cid)
            if cells:
                comps.append(dict(unexpected_cells=cells))
    kept = keep[lab].astype(np.uint8)
    # restore the "line" pixels that are actually paper connected to the kept areas
    # (geodesic reconstruction limited to 3 px) to keep the exact shape of the hole edges
    grown = kept.copy()
    for _ in range(3):
        grown = cv2.dilate(grown, np.ones((3, 3), np.uint8)) & w_full
    # thin lines across the empty paper (pen strokes, seams of the paper sheets): not piece-dark, paper on both sides
    gap = bridge_thin_gaps(grown, g > 90)
    if gap.any():
        print(f"thin lines bridged across the empty paper: {int(gap.sum())} px")
        grown = grown | gap.astype(np.uint8)
    return grown, comps


def clear_filled_cells(rimg, white, cw, ch, cells, margin=0.35, drop=12):
    """Remove from the empty paper the pale piece of a cell checked by eye as filled (params.json).

    A pale moon piece touching a hole can join the paper through a gap in its cut lines (L21-C4: edge of a silver
    moon). Its surface is darker than the paper (≈ 199 vs 225 grey levels) and textured: within the cell box
    (+ margin for its tabs), the pixels dimmer than the paper level of the neighbouring hole are removed, then what
    is no longer connected to the hole outside the box. Only the listed cells are touched."""
    g = cv2.blur(cv2.cvtColor(rimg, cv2.COLOR_BGR2GRAY).astype(np.float32), (9, 9))
    out = white.copy()
    cwp, chp = cw * PX_PER_U, ch * PX_PER_U
    for cid in cells:
        r, c = cell_rc(cid)
        y0, y1 = int((r - margin) * chp), int((r + 1 + margin) * chp)
        x0, x1 = int((c - margin) * cwp), int((c + 1 + margin) * cwp)
        box = np.zeros(white.shape, bool)
        box[max(0, y0):y1, max(0, x0):x1] = True
        near = np.zeros(white.shape, bool)
        near[max(0, int(y0 - chp)):int(y1 + chp), max(0, int(x0 - cwp)):int(x1 + cwp)] = True
        ref = (out > 0) & near & ~box
        if ref.sum() < 1000:
            continue
        paper = float(np.median(g[ref]))
        before = int((out > 0).sum())
        out[box & (g < paper - drop)] = 0
        # keep only the paper of the box still connected to the hole outside it
        _, lab = cv2.connectedComponents(out)
        outside = np.unique(lab[(out > 0) & ~box])
        out[box & ~np.isin(lab, outside[outside > 0])] = 0
        # small leftovers of the piece just outside the box (tip of a tab), cut off from the hole: removed too
        cut = cv2.dilate(((white > 0) & (out == 0)).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        n, lab, st, _ = cv2.connectedComponentsWithStats(out)
        for i in np.unique(lab[cut & (out > 0)]):
            if i and st[i, 4] < 3000:
                out[lab == i] = 0
        print(f"cell {cid}: {before - int((out > 0).sum())} px of pale piece removed from the empty paper"
              f" (paper level {paper:.0f})")
    return out


def robust_line(P, iters=6):
    """Line fitted by iterated least squares, rejecting distant points (tabs of the frame)."""
    keep = np.ones(len(P), bool)
    for _ in range(iters):
        m = P[keep].mean(0)
        _, _, vt = np.linalg.svd(P[keep] - m)
        d, n = vt[0], vt[1]
        r = (P - m) @ n
        thr = max(2.0, 2.5 * np.median(np.abs(r[keep])))
        keep = np.abs(r) < thr
    return m, d


def intersect(l1, l2):
    (m1, d1), (m2, d2) = l1, l2
    t = np.linalg.solve(np.array([d1, -d2]).T, m2 - m1)
    return m1 + t[0] * d1


def puzzle_outline(img):
    """Outer outline (px) of the puzzle = the dark band of the frame pieces, seen against the paper it lies on.

    Taking "everything but the paper touching the photo border" fails as soon as the margin is tight (64 Mpx board
    photo: a dark table shows behind a thin paper strip, a pen line joins the frame
    to the top border). Here: dark pixels (Otsu between pieces and paper) on a ≈ 1000 px image, opened to cut thin
    lines (pen strokes) but not the frame (≈ 30 px wide at that scale); the frame = the largest dark component that
    does not touch the photo border (the table, when visible, does). Its outline is then taken at full resolution."""
    H, W = img.shape[:2]
    s = 1000.0 / max(H, W)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gs = cv2.resize(g, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA)
    thr, _ = cv2.threshold(gs, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    k = max(3, int(round(max(gs.shape) / 200)))
    ell = lambda n: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n, n))
    dark = cv2.morphologyEx((gs < thr).astype(np.uint8), cv2.MORPH_OPEN, ell(k))
    n, lab, st, _ = cv2.connectedComponentsWithStats(dark, connectivity=4)
    best, area = None, 0
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if x <= 1 or y <= 1 or x + w >= gs.shape[1] - 1 or y + h >= gs.shape[0] - 1:
            continue
        if w * h > area:
            best, area = i, w * h
    if best is None:
        raise RuntimeError("frame not found: every dark area touches the photo border (margin too tight?)")
    # full resolution, restricted to the neighbourhood of the frame component
    guide = cv2.resize((lab == best).astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)
    guide = cv2.dilate(guide, ell(int(2 * k / s) | 1))
    kf = int(round(k / s)) | 1
    full = cv2.morphologyEx(((g < thr) & (guide > 0)).astype(np.uint8), cv2.MORPH_OPEN, ell(kf))
    cnts, _ = cv2.findContours(full, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    return max(cnts, key=cv2.contourArea).reshape(-1, 2).astype(float)


def frame_corners(img, report=None):
    """Corners of the outer frame (top-left, top-right, bottom-right, bottom-left), px. Each side = robust line on the
    straight outer edge of the frame pieces. report: dict filled with the straightness of each side (residuals of the
    outline to its line along the side, px), to judge lens distortion."""
    H, W = img.shape[:2]
    c = puzzle_outline(img)
    box = cv2.boxPoints(cv2.minAreaRect(c.astype(np.float32).reshape(-1, 1, 2)))
    # order: top-left, top-right, bottom-right, bottom-left
    s = box.sum(1); d = box[:, 0] - box[:, 1]
    tl, br = box[np.argmin(s)], box[np.argmax(s)]
    tr, bl = box[np.argmax(d)], box[np.argmin(d)]
    rect = [tl, tr, br, bl]
    band = 0.013 * max(H, W)          # 60 px on the 16 Mpx reference: the tabs of the frame lie farther out
    lines = []
    for k in range(4):
        a, b = rect[k], rect[(k + 1) % 4]
        L = np.linalg.norm(b - a)
        u = (b - a) / L
        nrm = np.array([-u[1], u[0]])
        rel = c - a
        along = rel @ u
        across = rel @ nrm
        sel = (along > 0.08 * L) & (along < 0.92 * L) & (np.abs(across) < band)
        lines.append(robust_line(c[sel]))
        if report is not None:
            m, d = lines[-1]
            P = c[sel]
            t = (P - m) @ d
            r = (P - m) @ np.array([-d[1], d[0]])
            ok = np.abs(r) < max(2.0, 2.5 * np.median(np.abs(r)))
            # residual profile in 10 bins along the side (median per bin): a bow = distortion
            bins = np.linspace(t[ok].min(), t[ok].max(), 11)
            prof = [float(np.median(r[ok][(t[ok] >= bins[i]) & (t[ok] < bins[i + 1])]))
                    for i in range(10) if ((t[ok] >= bins[i]) & (t[ok] < bins[i + 1])).sum() > 20]
            report[["top", "right", "bottom", "left"][k]] = dict(points=int(ok.sum()), profile_px=prof,
                                                                 rms_px=float(np.sqrt(np.mean(r[ok] ** 2))))
    corners = np.array([intersect(lines[k - 1], lines[k]) for k in range(4)])
    return corners, c


def rectify(img, corners):
    cw, ch = CELL_W_U, CELL_H_U
    Wu, Hu = NC * cw, NR * ch
    dst = np.array([[0, 0], [Wu, 0], [Wu, Hu], [0, Hu]], np.float32) * PX_PER_U
    Hm = cv2.getPerspectiveTransform(corners.astype(np.float32), dst)
    out = cv2.warpPerspective(img, Hm, (int(Wu * PX_PER_U), int(Hu * PX_PER_U)), flags=cv2.INTER_CUBIC,
                              borderValue=(255, 255, 255))
    return out, Hm, cw, ch


def offset_field(m, cw, ch):
    """Local offset of the grid, measured on the straight edges of the placed pieces bordering the empty area.
    Returns dx, dy (px) per cell (NR, NC)."""
    mf = gaussian_filter(m.astype(np.float32), 2.0)
    gy, gx = np.gradient(mf)
    edge = (m > 0) & (cv2.erode(m, np.ones((3, 3), np.uint8)) == 0)
    ys, xs = np.nonzero(edge)
    gxe, gye = gx[ys, xs], gy[ys, xs]
    horiz = np.abs(gye) > 3 * np.abs(gxe)
    vert = np.abs(gxe) > 3 * np.abs(gye)
    cwp, chp = cw * PX_PER_U, ch * PX_PER_U
    samples = []    # (x, y, dx or nan, dy or nan)
    # horizontal grid lines
    for r in range(1, NR):
        yl = r * chp
        for c in range(NC):
            sel = horiz & (np.abs(ys - yl) < 0.45 * chp) & (xs >= c * cwp) & (xs < (c + 1) * cwp)
            if sel.sum() < 0.3 * cwp:
                continue
            d = ys[sel] - yl
            h, e = np.histogram(d, bins=np.arange(-0.45 * chp, 0.45 * chp + 2, 2))
            k = np.argmax(h)
            if h[k] < 0.15 * cwp:
                continue
            mode = d[(d >= e[k] - 3) & (d < e[k + 1] + 3)].mean()
            samples.append(((c + 0.5) * cwp, yl, np.nan, mode))
    for c in range(1, NC):
        xl = c * cwp
        for r in range(NR):
            sel = vert & (np.abs(xs - xl) < 0.45 * cwp) & (ys >= r * chp) & (ys < (r + 1) * chp)
            if sel.sum() < 0.3 * chp:
                continue
            d = xs[sel] - xl
            h, e = np.histogram(d, bins=np.arange(-0.45 * cwp, 0.45 * cwp + 2, 2))
            k = np.argmax(h)
            if h[k] < 0.15 * chp:
                continue
            mode = d[(d >= e[k] - 3) & (d < e[k + 1] + 3)].mean()
            samples.append((xl, (r + 0.5) * chp, mode, np.nan))
    S = np.array(samples)
    dx = np.zeros((NR, NC)); dy = np.zeros((NR, NC))
    # local estimate (kernel of 1.2 cells) shrunk towards a smooth trend of the whole board (quadratic surface, robust
    # fit) where the local evidence is weak. Falling back to 0 there was wrong by ≈ 28 u (a quarter of
    # a piece) inside the large empty area at the top left, so its sockets snapped to the neck of a knob.
    W0 = 0.2

    def feats(x, y):
        x, y = x / (NC * cwp) - 0.5, y / (NR * chp) - 0.5
        return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], -1)
    trend = {}
    for col in (2, 3):
        ok = ~np.isnan(S[:, col])
        F, v = feats(S[ok, 0], S[ok, 1]), S[ok, col]
        keep = np.ones(len(v), bool)
        for _ in range(5):
            beta = np.linalg.lstsq(F[keep], v[keep], rcond=None)[0]
            res = np.abs(v - F @ beta)
            keep = res < max(3 * np.median(res[keep]), 2 * PX_PER_U)
        trend[col] = beta
    for r in range(NR):
        for c in range(NC):
            cx, cy = (c + 0.5) * cwp, (r + 0.5) * chp
            d2 = ((S[:, 0] - cx) / cwp) ** 2 + ((S[:, 1] - cy) / chp) ** 2
            for col, out in ((2, dx), (3, dy)):
                ok = ~np.isnan(S[:, col])
                w = np.exp(-d2[ok] / (2 * 1.2 ** 2))
                t = float(feats(np.array(cx), np.array(cy)) @ trend[col])
                out[r, c] = ((w * S[ok, col]).sum() + W0 * t) / (w.sum() + W0)
    return dx, dy, S


def occupancy(m, cw, ch, dx, dy, box=(0.36, 0.64)):
    cwp, chp = cw * PX_PER_U, ch * PX_PER_U
    occ = np.zeros((NR, NC))
    for r in range(NR):
        for c in range(NC):
            x0, x1 = int((c + box[0]) * cwp + dx[r, c]), int((c + box[1]) * cwp + dx[r, c])
            y0, y1 = int((r + box[0]) * chp + dy[r, c]), int((r + box[1]) * chp + dy[r, c])
            z = m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
            occ[r, c] = z.mean() if z.size else 0
    return occ


def main(photo=BOARD_PHOTO, out=None, img=None, expected=None):
    """Board model of a photo. Pipeline (default): the reference photo of the manifest, outputs in data/, board/,
    reports/. Workshop recalibration (src/recalibrate.py): out = a work directory receiving every output, img = the
    photo already loaded, expected = the empty cells expected from the journal. Returns a summary (disagreements)."""
    D, BD, RP = (out, out, out) if out else (DATA, BOARD_DIR, REPORTS)
    img = load_photo(photo) if img is None else img
    corners, outline = frame_corners(img)
    rimg, Hm, cw, ch = rectify(img, corners)
    rimg = normalize_illumination(rimg)
    os.makedirs(BD, exist_ok=True)
    cv2.imwrite(os.path.join(BD, "board_rectified.jpg"), rimg, [cv2.IMWRITE_JPEG_QUALITY, 92])
    expected = expected_empty_cells() if expected is None else expected
    white, comps = empty_mask(rimg, expected=expected)
    filled = load_json(os.path.join(DATA, "params.json")).get("verified_filled_cells", [])
    raw_white = white   # grid offsets: the outline of a pale piece follows real cut lines, still valid evidence
    white = clear_filled_cells(rimg, white, cw, ch, filled)
    cv2.imwrite(os.path.join(D, "board_empty_mask.png"), white * 255)
    print("empty areas kept:", [(c["area_px"], round(c["texture"], 2)) for c in comps if "area_px" in c])
    # occupancy: share of paper at the centre of each cell, recentred by the local offset field
    dx, dy, S = offset_field(raw_white, cw, ch)
    print("offset samples:", len(S), " |dx| max %.0f px, |dy| max %.0f px" % (np.abs(dx).max(), np.abs(dy).max()))
    occ = occupancy(white, cw, ch, dx, dy)
    # small empty areas (< 4 cells): N = area / cell cells are assigned, those overlapping the area the most
    cwp, chp = cw * PX_PER_U, ch * PX_PER_U
    ncomp, lab, st, _ = cv2.connectedComponentsWithStats(white)
    for i in range(1, ncomp):
        a = st[i, 4]
        if a > 4 * cwp * chp or a < 0.3 * cwp * chp:
            continue
        m = (lab == i)
        x, y, w, h = st[i, :4]
        cand = []
        for r in range(max(0, int(y // chp) - 1), min(NR, int((y + h) // chp) + 2)):
            for c in range(max(0, int(x // cwp) - 1), min(NC, int((x + w) // cwp) + 2)):
                cov = m[int(r * chp):int((r + 1) * chp), int(c * cwp):int((c + 1) * cwp)].mean()
                cand.append((cov, r, c))
        N = max(1, int(round(a / (cwp * chp))))
        ys_, xs_ = np.nonzero(m)
        for cov, r, c in sorted(cand, reverse=True)[:N]:
            occ[r, c] = max(occ[r, c], 0.99)
            if N == 1:   # single-cell hole: its centre gives the real position of the cell
                dx[r, c] = xs_.mean() - (c + 0.5) * cwp
                dy[r, c] = ys_.mean() - (r + 0.5) * chp
            print(f"small empty area ({a} px) -> cell L{r + 1}-C{c + 1} (overlap {cov:.2f})")
    # cells checked by eye as filled (params.json): the neighbouring paper spills into their centre
    for cid in load_json(os.path.join(DATA, "params.json")).get("verified_filled_cells", []):
        r, c = cell_rc(cid)
        print(f"cell {cid} forced filled (measured occupancy {occ[r, c]:.2f})")
        occ[r, c] = 0.0
    # known empty cells (params.json, minus the placements): the photo must agree; the list wins, disagreements are
    # reported (an outdated list, a cell numbering reversed by a rotated photo, a mask error). Only a clear contradiction
    # counts: a hole closed in by placed pieces shows little paper at its centre (their tabs reach into it: L7-C24
    # measured 0.47 on 2026-11-21), while a piece placed without being recorded leaves ≈ 0 and a piece taken out ≈ 1.
    disagree = []
    if expected:
        exp = np.zeros((NR, NC), bool)
        for cid in expected:
            exp[cell_rc(cid)] = True
        close = []
        for r in range(NR):
            for c in range(NC):
                if exp[r, c] == (occ[r, c] > 0.5):
                    continue
                d = dict(cell=f"L{r + 1}-C{c + 1}", expected="empty" if exp[r, c] else "filled", occupancy=round(float(occ[r, c]), 2))
                (close if DISAGREE_SURE[0] <= occ[r, c] <= DISAGREE_SURE[1] else disagree).append(d)
                occ[r, c] = 0.99 if exp[r, c] else 0.0
        if close:
            print("too close to call, the list kept:", [(d["cell"], d["expected"], d["occupancy"]) for d in close])
        known = {d["cell"] for d in disagree}
        for comp in comps:
            for cid in comp.get("unexpected_cells", []):
                if cid not in known:          # paper-like area dropped by the expected rule: reported, not kept
                    disagree.append(dict(cell=cid, expected="filled", occupancy=None))
        print(f"known empty cells: {len(expected)}; photo disagrees on {len(disagree)}:",
              [(d["cell"], d["expected"], d["occupancy"]) for d in disagree])
    np.save(os.path.join(D, "board_occ_raw.npy"), occ)
    np.save(os.path.join(D, "board_offsets.npy"), np.stack([dx, dy]))
    amb = [(r, c, round(float(occ[r, c]), 2)) for r in range(NR) for c in range(NC) if 0.2 < occ[r, c] < 0.8]
    print("ambiguous cells (20-80 %):", amb)
    vis = rimg.copy()
    for r in range(NR + 1):
        y = int(r * ch * PX_PER_U); cv2.line(vis, (0, y), (vis.shape[1], y), (255, 0, 0), 3)
    for c in range(NC + 1):
        x = int(c * cw * PX_PER_U); cv2.line(vis, (x, 0), (x, vis.shape[0]), (255, 0, 0), 3)
    for r in range(NR):
        for c in range(NC):
            if occ[r, c] > 0.5:
                cx, cy = int((c + 0.5) * cw * PX_PER_U + dx[r, c]), int((r + 0.5) * ch * PX_PER_U + dy[r, c])
                cv2.circle(vis, (cx, cy), 18, (0, 160, 0) if occ[r, c] > 0.8 else (0, 140, 255), -1)
    os.makedirs(RP, exist_ok=True)
    cv2.imwrite(os.path.join(RP, "C6_grid_occupancy.jpg"), cv2.resize(vis, None, fx=0.25, fy=0.25))
    print("photo", photo, "corners", corners.round(1).tolist())
    print("cells with >50% paper:", int((occ > 0.5).sum()), " >80%:", int((occ > 0.8).sum()),
          " between 20 and 80%:", int(((occ > 0.2) & (occ < 0.8)).sum()))
    save_json(os.path.join(D, "board_geom.json"), dict(photo=photo, corners_px=corners, H=Hm, cell_w_u=cw,
                                                          cell_h_u=ch, px_per_u=PX_PER_U, cols=NC, rows=NR,
                                                          disagreements=disagree))
    if photo == BOARD_PHOTO or out:
        build_sockets(rimg, white, occ, dx, dy, cw, ch, ref=raw_white, out=D, board_dir=BD)   # levels before the clean-up
    return dict(corners_px=corners.tolist(), disagreements=disagree, n_empty=int((occ > 0.5).sum()), rimg=rimg)


# ---------------------------------------------------------------- sockets
def frontier_contours(rimg, white, ref=None):
    """Sub-pixel contours (u) of the empty areas, clockwise on screen (like the pieces).
    ref: mask on which the paper / dark levels are measured (default: white)."""
    g = gaussian_filter(cv2.cvtColor(rimg, cv2.COLOR_BGR2GRAY).astype(np.float32), 1.2)
    inner = cv2.erode(white, np.ones((7, 7), np.uint8))
    outer = 1 - cv2.dilate(white, np.ones((7, 7), np.uint8))
    ref = white if ref is None else ref
    ring = cv2.dilate(ref, np.ones((25, 25), np.uint8)) - cv2.dilate(ref, np.ones((9, 9), np.uint8))
    vp = float(np.median(g[cv2.erode(ref, np.ones((15, 15), np.uint8)) > 0]))
    vd = float(np.median(g[ring > 0]))
    level = 0.5 * (vp + vd)
    sm = np.where(inner > 0, np.maximum(g, vp), g)
    sm = np.where(outer > 0, np.minimum(sm, vd), sm)
    out = []
    for cc in find_contours(sm, level):
        if len(cc) < 200 or np.linalg.norm(cc[0] - cc[-1]) > 1.5:
            continue
        xy = cc[:-1, ::-1] / PX_PER_U
        sa = np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1])
        # the contour must enclose paper: orientation fixed to clockwise
        if sa < 0:
            xy = xy[::-1].copy()
        out.append(xy)
    return out, dict(level=level, paper=vp, dark=vd)


def contour_angles(P, k=12):
    n = len(P)
    a = P[(np.arange(n) - k) % n] - P
    b = P[(np.arange(n) + k) % n] - P
    cos = (a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1) + 1e-9)
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def contour_flatness(P, m=24):
    """Max distance (u) of the points P[i-m..i+m] to the chord P[i-m] -> P[i+m]: small on a flat stretch of the outline
    (a seam notch between two placed pieces), large next to a knob or at a real corner of the hole. m = 24 points ≈ 12 u."""
    n = len(P)
    a, b = P[(np.arange(n) - m) % n], P[(np.arange(n) + m) % n]
    t = b - a
    t = t / (np.linalg.norm(t, axis=1, keepdims=True) + 1e-9)
    dev = np.zeros(n)
    for j in range(-m, m + 1, 2):
        v = P[(np.arange(n) + j) % n] - a
        dev = np.maximum(dev, np.abs(v[:, 0] * t[:, 1] - v[:, 1] * t[:, 0]))
    return dev


def snap_corner(allpts, allang, A, L, straight=False, allflat=None, flat_max=3.0):
    """Estimated corner -> nearest sharp point of the contour (if any), otherwise the nearest point.
    straight: the outline goes straight through this corner (run of empty cells along a row of placed pieces, e.g.
    along the frame). There, the nearest sharp point may be the neck of a tab or blank, up to half a knob away from the
    seam: only sharp points on a flat stretch of the outline (seam notches) are kept, otherwise the
    nearest point of the outline is taken (a projection across the wall was tried: no clear gain)."""
    d = np.linalg.norm(allpts - A, axis=1)
    near = np.where(d < 0.22 * L)[0]
    if len(near):
        sharp = near[allang[near] < 130]
        if straight:
            sharp = sharp[allflat[sharp] < flat_max]
        if len(sharp):
            return int(sharp[np.argmin(allang[sharp] + 2.0 * d[sharp])])
    return int(np.argmin(d))


SEAM_CORNERS = True
SEAM_HALF, SEAM_DEPTH = 0.25, (3.0, 13.0)     # search window along the wall (× L), depth into the placed pieces (u)
SEAM_CONTRAST, SEAM_SPREAD = 15.0, 2.0        # grey levels below the median of the window; agreement of 3 depth bands (u)
SEAM_MAX_DL = 12.0                            # |side length − pitch| allowed with seam corners (pieces: sd ≈ 4.7 u)


def seam_offset(g, allpts, E, t, n, L):
    """Seam between two placed pieces next to a corner on a straight wall.
    The outline gives no hint there (the pieces are flush), but the seam is a dark line across the wall, inside the
    placed pieces. E: corner estimate (u), t: unit vector along the side, n: unit normal into the placed pieces.
    Returns the point of the wall facing the seam (u), or None when no clear, straight dark line is found."""
    rel = allpts - E
    st, sn = rel @ t, rel @ n
    sel = (np.abs(st) < SEAM_HALF * L) & (np.abs(sn) < 0.25 * L)
    if sel.sum() < 10:
        return None
    w = float(np.median(sn[sel]))                 # position of the wall
    s = np.arange(-SEAM_HALF * L, SEAM_HALF * L + 0.01, 0.5)
    d = np.arange(SEAM_DEPTH[0], SEAM_DEPTH[1] + 0.01, 1.0)
    pts = E[None, None] + s[None, :, None] * t + (w + d)[:, None, None] * n
    vals = map_coordinates(g, [pts[..., 1] * PX_PER_U, pts[..., 0] * PX_PER_U], order=1, mode="nearest")
    prof = gaussian_filter1d(vals.mean(0), 1.5)
    i = int(np.argmin(prof))
    if np.median(prof) - prof[i] < SEAM_CONTRAST or i in (0, len(s) - 1):
        return None
    mins = [s[int(np.argmin(gaussian_filter1d(vals[b].mean(0), 1.5)))] for b in np.array_split(np.arange(len(d)), 3)]
    if np.ptp(mins) > SEAM_SPREAD:
        return None
    return E + s[i] * t + w * n


def build_sockets(rimg, white, occ, dx, dy, cw, ch, ref=None, out=DATA, board_dir=BOARD_DIR):
    g = cv2.cvtColor(rimg, cv2.COLOR_BGR2GRAY).astype(np.float32)
    nout = [np.array([0., -1.]), np.array([1., 0.]), np.array([0., 1.]), np.array([-1., 0.])]
    n_seam = 0
    conts, lv = frontier_contours(rimg, white, ref)
    allpts = np.vstack(conts)
    allang = np.concatenate([contour_angles(c) for c in conts])
    allflat = np.concatenate([contour_flatness(c) for c in conts])
    owner = np.concatenate([np.full(len(c), i) for i, c in enumerate(conts)])
    offs = np.cumsum([0] + [len(c) for c in conts])
    empty = occ > 0.5
    cells, sockets = [], {}
    nb = [(-1, 0), (0, 1), (1, 0), (0, -1)]
    for r in range(NR):
        for c in range(NC):
            cx = (c + 0.5) * cw + dx[r, c] / PX_PER_U
            cy = (r + 0.5) * ch + dy[r, c] / PX_PER_U
            cell = dict(r=r, c=c, id=f"L{r + 1}-C{c + 1}", empty=bool(empty[r, c]), occ=float(occ[r, c]),
                        center_u=[cx, cy])
            if empty[r, c]:
                Cn = np.array([[cx - cw / 2, cy - ch / 2], [cx + cw / 2, cy - ch / 2],
                               [cx + cw / 2, cy + ch / 2], [cx - cw / 2, cy + ch / 2]])
                sides = []
                for k, (drr, dcc) in enumerate(nb):
                    rr, c2 = r + drr, c + dcc
                    inside = 0 <= rr < NR and 0 <= c2 < NC
                    side = dict(name=SIDE_NAMES[k], neighbour="empty" if (inside and empty[rr, c2]) else "placed")
                    if side["neighbour"] == "placed":
                        A, B = Cn[k], Cn[(k + 1) % 4]
                        L = np.linalg.norm(B - A)

                        def straight(j):
                            # outline straight through the corner shared with side j: the cell across side j is
                            # empty and its own side k faces a placed piece (or the outside)
                            r1, c1 = r + nb[j][0], c + nb[j][1]
                            if not (0 <= r1 < NR and 0 <= c1 < NC and empty[r1, c1]):
                                return False
                            r2, c2b = r1 + drr, c1 + dcc
                            return not (0 <= r2 < NR and 0 <= c2b < NC and empty[r2, c2b])
                        def corner(E, j, seams):
                            # straight wall: the seam line gives the corner (the grid only gives the mean pitch,
                            # while the pieces vary by ± 5 u); otherwise / failing that, the former snapping
                            if seams and straight(j):
                                q = seam_offset(g, allpts, E, (B - A) / L, nout[k], L)
                                if q is not None:
                                    return int(np.argmin(np.linalg.norm(allpts - q, axis=1))), 1
                            return snap_corner(allpts, allang, E, L, straight(j), allflat), 0
                        (ia, sa), (ib, sb) = corner(A, (k - 1) % 4, SEAM_CORNERS), corner(B, (k + 1) % 4, SEAM_CORNERS)
                        if sa + sb and abs(np.linalg.norm(allpts[ib] - allpts[ia]) - L) > SEAM_MAX_DL:
                            # side length beyond the spread of the real pieces: a wrong dark line, back to the grid
                            (ia, sa), (ib, sb) = corner(A, (k - 1) % 4, False), corner(B, (k + 1) % 4, False)
                        n_seam += sa + sb
                        ok = owner[ia] == owner[ib] and np.linalg.norm(allpts[ia] - A) < 0.35 * L \
                            and np.linalg.norm(allpts[ib] - B) < 0.35 * L
                        if ok:
                            ci = owner[ia]; P = conts[ci]; n = len(P)
                            a, b = ia - offs[ci], ib - offs[ci]
                            idx = np.arange(a, a + ((b - a) % n) + 1) % n
                            seg = P[idx]
                            arc = np.sum(np.linalg.norm(np.diff(seg, axis=0), axis=1))
                            ok = 0.6 * L < arc < 2.6 * L
                        if ok:
                            p0, p1 = seg[0], seg[-1]
                            Ls = np.linalg.norm(p1 - p0)
                            ux = (p1 - p0) / Ls
                            uy = np.array([ux[1], -ux[0]])
                            loc = np.stack([(seg - p0) @ ux, (seg - p0) @ uy], 1)
                            d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(loc, axis=0), axis=1))]
                            t = np.linspace(0, d[-1], NPROF)
                            prof = np.stack([np.interp(t, d, loc[:, 0]), np.interp(t, d, loc[:, 1])], 1)
                            ymax, ymin = loc[:, 1].max(), loc[:, 1].min()
                            req = "T" if (ymax > 0.15 * Ls and ymax >= -ymin) else ("B" if -ymin > 0.15 * Ls else "F")
                            side.update(required_type=req, L=float(Ls), extracted=True)
                            sockets[f"{cell['id']}__{SIDE_NAMES[k]}"] = dict(points=seg, profile=prof)
                        else:
                            side.update(extracted=False)
                    sides.append(side)
                cell["sides"] = sides
                cell["n_known"] = sum(1 for s in sides if s.get("extracted"))
            cells.append(cell)
    n_empty = int(empty.sum())
    known = [c for c in cells if c["empty"]]
    hist = np.bincount([c["n_known"] for c in known], minlength=5)
    nT = sum(1 for c in known for s in c["sides"] if s.get("required_type") == "T")
    nB = sum(1 for c in known for s in c["sides"] if s.get("required_type") == "B")
    nfail = sum(1 for c in known for s in c["sides"] if s["neighbour"] == "placed" and not s.get("extracted"))
    print(f"empty cells {n_empty}; known sides per cell: {hist.tolist()}; sockets: {nT} tabs required, {nB} blanks required; failures {nfail}; corners on a seam line {n_seam}")
    save_json(os.path.join(out, "board.json"), dict(cols=NC, rows=NR, cell_w_u=cw, cell_h_u=ch, levels=lv,
                                                     n_empty=n_empty, cells=cells))
    np.savez_compressed(os.path.join(out, "board_sockets.npz"),
                        **{f"{k}__{f}": v for k, d in sockets.items() for f, v in d.items()},
                        **{f"frontier_{i}": c for i, c in enumerate(conts)})
    write_board_svg(rimg, cells, sockets, conts, cw, ch, out=board_dir)


def write_board_svg(rimg, cells, sockets, conts, cw, ch, out=BOARD_DIR):
    Wu, Hu = NC * cw, NR * ch
    small = cv2.resize(rimg, (int(Wu), int(Hu)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(os.path.join(out, "board_background.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 82])
    col = {"T": "#1f9d55", "B": "#d64545", "F": "#2a6fdb"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {Wu:.1f} {Hu:.1f}" '
             f'width="{Wu / 2.5:.0f}" height="{Hu / 2.5:.0f}" data-grid="{NC}x{NR}" data-unit="u">',
             f'<title>Board — {NC} x {NR} grid, {sum(c["empty"] for c in cells)} empty cells</title>',
             f'<image href="board_background.jpg" x="0" y="0" width="{Wu:.1f}" height="{Hu:.1f}" opacity="0.85"/>',
             '<g id="grid" stroke="#2a6fdb" stroke-width="0.6" opacity="0.35">']
    parts += [f'<line x1="0" y1="{r * ch:.1f}" x2="{Wu:.1f}" y2="{r * ch:.1f}"/>' for r in range(NR + 1)]
    parts += [f'<line x1="{c * cw:.1f}" y1="0" x2="{c * cw:.1f}" y2="{Hu:.1f}"/>' for c in range(NC + 1)]
    parts.append('</g><g id="hole-edges" fill="none" stroke="#000" stroke-width="1">')
    for P in conts:
        Q = cv2.approxPolyDP(P.astype(np.float32).reshape(-1, 1, 2), 0.3, True).reshape(-1, 2)
        parts.append('<path d="M' + " L".join(f"{x:.1f},{y:.1f}" for x, y in Q) + ' Z"/>')
    parts.append('</g><g id="sockets" fill="none" stroke-width="2.5">')
    for k, s in sockets.items():
        cid, side = k.split("__")
        cell = next(c for c in cells if c["id"] == cid)
        req = next(sd for sd in cell["sides"] if sd["name"] == side)["required_type"]
        Q = cv2.approxPolyDP(s["points"].astype(np.float32).reshape(-1, 1, 2), 0.3, False).reshape(-1, 2)
        parts.append(f'<polyline data-cell="{cid}" data-side="{side}" data-required="{req}" stroke="{col[req]}" points="'
                     + " ".join(f"{x:.1f},{y:.1f}" for x, y in Q) + '"/>')
    parts.append('</g><g id="empty-cells" font-family="sans-serif" text-anchor="middle">')
    for c in cells:
        if c["empty"]:
            x, y = c["center_u"]
            fill = "#ffb000" if c["n_known"] >= 2 else ("#2a6fdb" if c["n_known"] == 1 else "#999")
            parts.append(f'<g data-cell="{c["id"]}"><circle cx="{x:.1f}" cy="{y:.1f}" r="7" fill="{fill}" opacity="0.8"/>'
                         f'<text x="{x:.1f}" y="{y + 20:.1f}" font-size="11" fill="#333">{c["r"] + 1}·{c["c"] + 1}</text></g>')
    parts.append('</g></svg>')
    with open(os.path.join(out, "board.svg"), "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


if __name__ == "__main__":
    main(*sys.argv[1:])
