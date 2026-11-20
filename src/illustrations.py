"""README images (docs/images/), reproducible: sh scripts/pipeline.sh --script src/illustrations.py

- puzzle.jpg            : the puzzle in progress (reference photo of the board, rectified)
- sheet_catalog.jpg     : a half sheet photographed and the same in the catalog
- fusion.png            : principle of the fusion (overlaid views + fused piece)
- board.jpg             : board model (sockets read on the photo, whole board + zoom)
- fitting.jpg           : virtual fitting of the current wave
The screenshots (atelier_en.png, demo.jpg, catalog.png): sh scripts/screenshots.sh (headless browser).
"""
import os, sys, glob, subprocess
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, DATA, CATALOG, BOARD_DIR, REPORTS, load_json, load_photo, load_raw
from fusion import rot90, rigid, shape_scores
from export_svg import CELL

OUT = os.path.join(ROOT, "docs", "images")
FONTS = "/usr/share/fonts/truetype/dejavu/"


def text(img, items):
    """Draws (text, (x, y), size, BGR colour[, bold]) items with DejaVu Sans, outlined for legibility."""
    im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(im)
    for it in items:
        t, xy, size, col = it[:4]
        f = ImageFont.truetype(FONTS + ("DejaVuSans-Bold.ttf" if len(it) > 4 and it[4] else "DejaVuSans.ttf"), size)
        halo = (0, 0, 0) if sum(col) > 380 else (255, 255, 255)
        d.text(xy, t, font=f, fill=tuple(col[::-1]), stroke_width=max(2, size // 9), stroke_fill=halo)
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def fit_width(img, w):
    return cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])), interpolation=cv2.INTER_AREA)


def svg_png(svg, width, out):
    subprocess.run(["rsvg-convert", "-w", str(width), "-b", "white", svg, "-o", out], check=True)
    return cv2.imread(out)


def puzzle():
    # the reference photo, rectified on the puzzle frame by the board model (no perspective, no background)
    img = cv2.imread(os.path.join(BOARD_DIR, "board_background.jpg"))
    cv2.imwrite(os.path.join(OUT, "puzzle.jpg"), fit_width(img, 840), [cv2.IMWRITE_JPEG_QUALITY, 86])


def sheet_catalog():
    photo = fit_width(load_photo("sheets/C_col06-10_2026-11-19.jpg"), 640)
    tmp = os.path.join(OUT, "_sheet_C.png")
    full = svg_png(os.path.join(CATALOG, "sheet_C.svg"), 1600, tmp)
    os.remove(tmp)
    s = full.shape[1] / (10 * CELL)                     # px per u in the rendering
    half = full[:, int(5 * CELL * s):]                  # columns 6 to 10
    half = cv2.resize(half, (int(half.shape[1] * photo.shape[0] / half.shape[0]), photo.shape[0]))
    sep = np.full((photo.shape[0], 20, 3), 255, np.uint8)
    out = np.hstack([photo, sep, half])
    out = text(out, [("Photo — sheet C, columns 6 to 10", (12, 8), 22, (40, 40, 40), True),
                     ("Catalog (SVG)", (photo.shape[1] + 32, 8), 22, (40, 40, 40), True)])
    cv2.imwrite(os.path.join(OUT, "sheet_catalog.jpg"), out, [cv2.IMWRITE_JPEG_QUALITY, 86])


def fusion(ids=("C006", "A020", "B058")):
    PJ = load_json(os.path.join(DATA, "pieces.json"))
    P = {p["id"]: p for p in PJ["pieces"]}
    raw = load_raw()
    z = np.load(os.path.join(DATA, "measurements", "measurements_raw.npz"))
    cols = [(60, 60, 220), (220, 130, 30), (40, 170, 40)]
    S, pad = 3.0, 30
    tiles = []
    for pid in ids:
        r = raw[pid]
        Cf = r["corners_u"]
        canvas = np.full((int(2 * 95 * S) + 2 * pad + 60, int(2 * 95 * S) + 2 * pad, 3), 255, np.uint8)   # + room for the legend
        o = np.array([canvas.shape[1] / 2, (canvas.shape[0] - 60) / 2 + 10])
        to_px = lambda Q: np.round((Q * S + o) * 8).astype(np.int32).reshape(-1, 1, 2)
        legend = []
        for i, v in enumerate(P[pid]["views"]):
            key = v["view"]
            sc = shape_scores(z[f"{key}__profiles"], r["profiles"][None])[0]
            q = int(np.argmin(sc))
            order = [(j - q) % 4 for j in range(4)]
            s = v.get("scale", 1.0)                   # scale of the view (calibration of its photo)
            Cv = rot90(z[f"{key}__corners_u"][order] * s, q)
            Rm, ma, mb = rigid(Cv, Cf)
            Pv = (rot90(z[f"{key}__contour_u"] * s, q) - ma) @ Rm.T + mb
            col = cols[i % 3]
            used = v["kept"] and v["weight"] > 0                # weight 0: corners not refined, the view does not count
            cv2.polylines(canvas, [to_px(Pv)], True, col, 2 if used else 1, cv2.LINE_AA, shift=3)
            why = "dropped" if not v["kept"] else "dropped (corners not found)"
            legend.append((f"{view_name(key)}: " + (f"deviation {v['fusion_deviation_u']:.1f} u" if used else why), col))
        cv2.polylines(canvas, [to_px(r["contour_u"])], True, (0, 0, 0), 3, cv2.LINE_AA, shift=3)
        canvas = text(canvas, [(pid[0] + str(int(pid[1:])), (12, 8), 28, (0, 0, 0), True)]
                      + [(t, (12, canvas.shape[0] - 14 - 24 * (len(legend) + 1 - k)), 17, c)
                         for k, (t, c) in enumerate(legend + [("fused piece", (0, 0, 0))])])
        tiles.append(canvas)
    sep = np.full((tiles[0].shape[0], 2, 3), 225, np.uint8)
    cv2.imwrite(os.path.join(OUT, "fusion.png"), np.hstack([x for t in tiles for x in (t, sep)][:-1]))


def view_name(key):
    """'C_col06-10_2026-11-19#12' -> 'sheet C, columns 6–10 (Nov 19)'."""
    p = key.split("#")[0].split("_")
    date = next(x for x in p if x.count("-") == 2 and x[:4].isdigit())
    day = f"Nov {int(date[-2:])}"
    cols = next((x for x in p if x.startswith("col")), None)
    return f"sheet {p[0]}, " + (f"columns {int(cols[3:5])}–{int(cols[6:8])}" if cols else "whole sheet") + f" ({day})"


SOCKET_BGR = {"T": (120, 220, 60), "B": (90, 80, 245), "F": (255, 170, 80)}     # tab green, blank red, flat blue


def board(focus="L18-C23", win=7, H=1100):
    """Board model: the rectified photo dimmed, every socket read on it coloured by the side it requires. Left the
    whole board, right a zoom on win × win cells around `focus` (the first proposal of the workshop: D51's hole)."""
    bg = cv2.imread(os.path.join(BOARD_DIR, "board_background.jpg"))            # 1 px per u
    b = load_json(os.path.join(DATA, "board.json"))
    z = np.load(os.path.join(DATA, "board_sockets.npz"))
    cw, ch, NR, NC = b["cell_w_u"], b["cell_h_u"], b["rows"], b["cols"]
    req = {(c["id"], s["name"]): s["required_type"] for c in b["cells"] if c["empty"]
           for s in c["sides"] if s.get("extracted")}

    def render(k, thick, crop=None):
        """The board at k px per u (only the crop (x0, y0, x1, y1) in u if given), sockets drawn."""
        x0, y0, x1, y1 = crop or (0, 0, bg.shape[1], bg.shape[0])
        part = bg[int(y0):int(y1), int(x0):int(x1)]
        img = cv2.resize((part * 0.6).astype(np.uint8), None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
        for (cid, side), t in req.items():
            P = (z[f"{cid}__{side}__points"] - [x0, y0]) * k
            cv2.polylines(img, [np.round(P * 8).astype(np.int32).reshape(-1, 1, 2)], False, SOCKET_BGR[t], thick, cv2.LINE_AA, shift=3)
        return img

    fr, fc = (int(x) - 1 for x in focus[1:].split("-C"))
    r0, c0 = min(max(fr - win // 2, 0), NR - win), min(max(fc - 1, 0), NC - win)    # focus near the left: the holes along the right frame
    crop = (c0 * cw, r0 * ch, (c0 + win) * cw, (r0 + win) * ch)
    kz = H / (win * ch)
    zoom = render(kz, 5, crop)
    labels = []
    for c in b["cells"]:
        if c["empty"] and r0 <= c["r"] < r0 + win and c0 <= c["c"] < c0 + win:
            cx, cy = c["center_u"]
            big = c["id"] == focus
            labels.append((c["id"].replace("-", " · "), (int((cx - crop[0]) * kz) - (52 if big else 38), int((cy - crop[1]) * kz) - 12),
                           24 if big else 17, (255, 255, 255) if big else (210, 210, 210), big))
    zoom = text(zoom, labels)
    kf = H / bg.shape[0]
    full = render(kf, 2)
    cv2.rectangle(full, (int(crop[0] * kf), int(crop[1] * kf)), (int(crop[2] * kf), int(crop[3] * kf)), (255, 255, 255), 2, cv2.LINE_AA)
    gap = np.full((H, 16, 3), 255, np.uint8)
    top = np.hstack([full, gap, zoom])
    foot = np.full((88, top.shape[1], 3), 255, np.uint8)
    n_t = sum(t == "T" for t in req.values()); n_b = sum(t == "B" for t in req.values())
    x, items = 14, []
    for t, name, n in (("T", "tab required", n_t), ("B", "blank required", n_b)):
        cv2.line(foot, (x, 30), (x + 44, 30), SOCKET_BGR[t], 8, cv2.LINE_AA)
        items.append((f"{name} ({n})", (x + 54, 16), 22, (40, 40, 40)))
        x += 330
    items.append((f"{b['n_empty']} empty cells · {len(req)} sockets read on the photo of the board, each side of an empty "
                  f"cell that touches a placed piece · right: zoom on the white frame", (14, 54), 18, (90, 90, 90)))
    foot = text(foot, items)
    cv2.imwrite(os.path.join(OUT, "board.jpg"), np.vstack([top, foot]), [cv2.IMWRITE_JPEG_QUALITY, 88])


def fitting():
    waves = sorted(glob.glob(os.path.join(REPORTS, "wave_*_fitting.jpg")))
    if waves:
        cv2.imwrite(os.path.join(OUT, "fitting.jpg"), fit_width(cv2.imread(waves[-1]), 900), [cv2.IMWRITE_JPEG_QUALITY, 85])


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in sys.argv[1:] and [globals()[a] for a in sys.argv[1:]] or (puzzle, sheet_catalog, fusion, board, fitting):
        f(); print("ok", f.__name__, flush=True)


if __name__ == "__main__":
    main()
