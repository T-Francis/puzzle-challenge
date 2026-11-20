"""Data of the public demo (demo/index.html): demo/demo_data.js + demo/board_clean.jpg.

The demo shows the DIRECT PATH: each piece flies once from its sheet to its final cell (state of the simulation after
self-correction), in the order where each cell was first filled. It never shows the search (removals, swaps).

- Clean background: the rectified board photo, paper of the holes repainted dark (they read as holes, and the seams
  between drawn pieces are invisible), white slivers at the outer edge removed, 1 px per u.
- Pieces at their real position: one global least-squares adjustment of every drawn piece (`demo_fit.py`):
  sides on the real board sockets, neighbouring pieces on each other, tilt ≤ 2°. What stays misfit is measured per
  piece (fit rms, u) and exported: the page can colour the pieces by fit quality (button, off by default).
- JavaScript file (window.DEMO = …): the page also works opened from disk (file://).
"""
import os, sys, json
import cv2
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, DATA, BOARD_DIR, load_json, load_raw
from export_svg import simplify, display_turns
from solve import load_board, board_sockets
from board_model import paper_mask, PX_PER_U
from demo_fit import adjust

OUT = os.path.join(ROOT, "demo")
HOLE_BGR = (30, 25, 22)          # dark slate: reads as an empty hole on the dark puzzle
EDGE_BGR = (16, 15, 15)          # puzzle black, for slivers of paper at the outer edge
GOOD, FAIR = 1.5, 2.2            # fit classes in units of SIGMA_FIT
SIGMA_FIT = 1.57                 # u: the drawing residuals are point distances after the global fit (demo_fit), not engine
                                 # scores (curve distances): scale of the paired rms, kept fixed


def clean_background():
    img = cv2.imread(os.path.join(BOARD_DIR, "board_rectified.jpg"))
    empty = cv2.imread(os.path.join(DATA, "board_empty_mask.png"), cv2.IMREAD_GRAYSCALE) > 0
    paper = paper_mask(img) > 0
    holes = empty | (cv2.dilate(empty.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0) & paper
    band = np.zeros(paper.shape, bool)
    m = int(6 * PX_PER_U)
    band[:m, :] = band[-m:, :] = True
    band[:, :m] = band[:, -m:] = True
    out = img.copy()
    out[holes] = HOLE_BGR
    out[band & paper & ~holes] = EDGE_BGR
    soft = cv2.GaussianBlur(out, (0, 0), 1.0)                      # feather the repainted edges
    edge = cv2.dilate(holes.astype(np.uint8), np.ones((3, 3), np.uint8)) ^ cv2.erode(holes.astype(np.uint8), np.ones((3, 3), np.uint8))
    out[edge > 0] = soft[edge > 0]
    small = cv2.resize(out, (int(round(img.shape[1] / PX_PER_U)), int(round(img.shape[0] / PX_PER_U))), interpolation=cv2.INTER_AREA)
    cv2.imwrite(os.path.join(OUT, "board_clean.jpg"), small, [cv2.IMWRITE_JPEG_QUALITY, 86])
    # colour of the black pieces as the photo renders them: the drawn pieces must match it
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    dark = ~holes & (hsv[..., 2] < 95) & (hsv[..., 1] < 70)
    b, g, r = (int(np.median(img[..., k][dark])) for k in range(3))
    return [r, g, b]


def main():
    os.makedirs(OUT, exist_ok=True)
    piece_rgb = clean_background()
    b, z, pl = load_board()
    C = {c["id"]: c for c in b["cells"]}
    base = board_sockets(b, z)
    P = load_json(os.path.join(DATA, "pieces.json"))["pieces"]
    raw = load_raw()
    sim = load_json(os.path.join(DATA, "simulation", "simulation.json"))

    pieces = []
    for p in P:
        c = simplify(raw[p["id"]]["contour_u"], 0.35)
        pieces.append(dict(id=p["id"], sheet=p["sheet"], row=p["row"], col=p["col"], k=display_turns(p),
                           d=np.round(c, 1).ravel().tolist()))
    first = {}
    for s in sim["steps"]:
        if s["type"] == "place" and s["cell"] not in first:
            first[s["cell"]] = s["n"]
    final = sim["final"]["placements"]
    order = sorted(final, key=lambda c: first.get(c, 10 ** 9))
    items = [(x["cell"], x["piece"], x["rotation"] // 90) for x in pl["placed"]] + \
            [(cid, final[cid]["piece"], final[cid]["rotation"] // 90) for cid in order]
    fit, summ = adjust(items, raw, base, C)
    sigma = SIGMA_FIT

    def tr(cid):
        f = fit[cid]
        return [round(f["x"], 1), round(f["y"], 1), round(f["angle_deg"], 2)]

    def rms(cid):
        v = fit[cid]["fit_u"]
        return None if v is None else round(v, 2)
    real = [[x["piece"], *tr(x["cell"])] for x in pl["placed"]]
    path = [[cid, final[cid]["piece"], *tr(cid), rms(cid)] for cid in order]
    q = np.array([p[5] for p in path if p[5] is not None])
    classes = dict(good=int((q <= GOOD * sigma).sum()), fair=int(((q > GOOD * sigma) & (q <= FAIR * sigma)).sum()),
                   poor=int((q > FAIR * sigma).sum()))
    print("fit classes (pieces):", classes, flush=True)
    holes = []
    for k in sorted((k for k in z.files if k.startswith("frontier_")), key=lambda k: int(k.split("_")[1])):
        h = cv2.approxPolyDP(z[k].astype(np.float32).reshape(-1, 1, 2), 0.8, True).reshape(-1, 2)
        holes.append(np.round(h, 1).ravel().tolist())
    data = dict(
        board=dict(cols=b["cols"], rows=b["rows"], cw=b["cell_w_u"], ch=b["cell_h_u"],
                   w=round(b["cols"] * b["cell_w_u"], 1), h=round(b["rows"] * b["cell_h_u"], 1), background="board_clean.jpg", piece_rgb=piece_rgb),
        holes=holes, pieces=pieces, real=real, path=path,
        fit=dict(sigma_u=sigma, good_u=round(GOOD * sigma, 2), fair_u=round(FAIR * sigma, 2), classes=classes, summary=summ),
        stats=dict(good_sides_pct=sim["final"]["measures"]["good_sides_pct"], placements=len(path)))
    with open(os.path.join(OUT, "demo_data.js"), "w", encoding="utf-8") as f:
        f.write("// Generated by src/demo_export.py — do not edit by hand.\nwindow.DEMO = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";\n")
    print(f"demo/demo_data.js: {len(pieces)} pieces, {len(path)} flights (global fit), "
          f"{len(real)} real placements, {len(holes)} hole outlines, "
          f"{os.path.getsize(os.path.join(OUT, 'demo_data.js')) / 1e6:.2f} MB; demo/board_clean.jpg")


if __name__ == "__main__":
    main()
