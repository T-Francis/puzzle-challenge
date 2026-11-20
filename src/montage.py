"""Check sheets: thumbnails of pieces on their best photo (measured contour + corners + canonical side N).
Usage: montage.py A001,B018 name.jpg   |   montage.py flags <alert> <max> name.jpg"""
import os, sys
import cv2
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, REPORTS, load_json, load_photo, load_raw

COL = {"T": (0, 190, 0), "B": (0, 0, 255), "F": (255, 0, 0)}


def thumb(img, p, r, size=300):
    C = r["corners_px"]
    pts = r["contour_px"]
    cx, cy = pts.mean(0)
    half = 230
    x0, y0 = int(cx - half), int(cy - half)
    crop = img[max(0, y0):y0 + 2 * half, max(0, x0):x0 + 2 * half].copy()
    off = np.array([max(0, x0), max(0, y0)])
    big = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    P = ((pts - off) * 2 * 8).astype(np.int32)   # shift=3 -> /8
    # sides coloured by type, in the order N, E, S, W
    idx = [int(np.argmin(np.linalg.norm(pts - c, axis=1))) for c in C]
    n = len(pts)
    for j in range(4):
        a, b = idx[j], idx[(j + 1) % 4]
        seg = np.arange(a, b + 1) if b >= a else np.r_[np.arange(a, n), np.arange(0, b + 1)]
        cv2.polylines(big, [P[seg].reshape(-1, 1, 2)], False, COL[p["sides"][j]["type"]], 2, cv2.LINE_AA, shift=3)
    for j, c in enumerate(C):
        q = tuple(((c - off) * 2).astype(int))
        cv2.circle(big, q, 7, (0, 255, 255) if j else (255, 0, 255), -1)
    # N marker: middle of side N
    mN = ((C[0] + C[1]) / 2 - off) * 2
    cv2.putText(big, "N", tuple(mN.astype(int) + [-10, -12]), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 0, 255), 3)
    out = cv2.resize(big, (size, size))
    cv2.rectangle(out, (0, 0), (size, 34), (255, 255, 255), -1)
    cv2.putText(out, f"{p['id']} {p['types']}", (6, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    return out


def montage(ids, name, cols=8):
    data = load_json(os.path.join(DATA, "pieces.json"))
    P = {p["id"]: p for p in data["pieces"]}
    raw = load_raw()
    imgs = {}
    tiles = []
    for pid in ids:
        p = P[pid]
        f = p["photo"]["file"]           # best view of the piece
        if f not in imgs:
            imgs[f] = load_photo(f)
        tiles.append(thumb(imgs[f], p, raw[pid]))
    while len(tiles) % cols:
        tiles.append(np.full_like(tiles[0], 255))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    os.makedirs(REPORTS, exist_ok=True)
    out = os.path.join(REPORTS, name)
    cv2.imwrite(out, np.vstack(rows))
    return out


if __name__ == "__main__":
    data = load_json(os.path.join(DATA, "pieces.json"))
    sel = sys.argv[1]
    if sel == "flags":
        ids = [p["id"] for p in data["pieces"] if p["flags"]]
        flag = sys.argv[2] if len(sys.argv) > 2 else None
        if flag:
            ids = [p["id"] for p in data["pieces"] if flag in p["flags"]]
        ids = ids[: int(sys.argv[3])] if len(sys.argv) > 3 else ids
    else:
        ids = sel.split(",")
    print(montage(ids, sys.argv[-1] if sys.argv[-1].endswith(".jpg") else "montage.jpg"))
