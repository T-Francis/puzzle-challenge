"""Stage 1 — measure every piece on every sheet photo (one "view" per piece and per photo).

For each photo of photos/manifest.json (sheets.photos): segmentation (the photo's "contour" method),
sub-pixel contour, corners (2 passes: the 2nd knows the median size), provisional scale (log-linear field over
the pieces of the photo, constant below 30 pieces; fusion.py then recalibrates it on the other photos),
canonical "standing piece" frame, profiles of the 4 sides.

Numbering by POSITION, same rule for every sheet: the pieces of the useful zone ("zone_x" / "zone_y",
fractions of width / height) are arranged in "rows" × ("columns"[1] − "columns"[0] + 1) by
k-means (columns on x; rows by rank within the column, or k-means on y if a column is incomplete).
Number = "number_grid"[row][column] if the photo declares it (layout other than the grid of 10: former layout
of a sheet), otherwise row × 10 + sheet column. Out of the zone (pieces of another sheet in the frame):
view "out_of_zone", ignored by the fusion (other height, hence other scale).

Outputs: data/measurements/measurements.json (photos + views) and data/measurements/measurements_raw.npz
(keys "<photo>#<k>__<field>").
"""
import os, sys
import cv2
import numpy as np
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, SHEET_PHOTOS, save_json, load_json, load_photo, photo_key, piece_id
from geometry import find_corners, refine_corners, canonical, kmeans1d
from segmentation import METHODS

OUT = os.path.join(DATA, "measurements")
PLATE_COLS = 10           # every sheet is numbered in rows of 10 (E: 10 + 2)
# Working resolution. The segmentation parameters are in pixels, tuned on photos of 2.2 px/u (whole sheets, 16 Mpx)
# and 3.0–3.5 px/u (half sheets). A sharper-sampled photo (64 Mpx whole sheet ≈ 4.3 px/u, edge blur ≈ 1 u, i.e. no
# detail finer than the half sheets) is measured at WORK_PPU after an area resampling; coordinates are stored in
# pixels of the original photo. Photos up to WORK_PPU_MAX are measured as they are (outputs unchanged; the photos of
# the 18th and 19th are at most 3.55 px/u, the estimate below is good to ±5 %).
WORK_PPU, WORK_PPU_MAX = 3.2, 4.0
PPU_AREA_RATIO = 94.0     # √(median area of a piece, px) / (px per u): 90–99 on the photos of the 18th and 19th


def rough_ppu(img):
    """px per u to ±5 %, from the median area of the dark blobs on a 1/8 image (before any segmentation)."""
    H, W = img.shape[:2]
    g = cv2.cvtColor(cv2.resize(img, (W // 8, H // 8), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    a = cv2.connectedComponentsWithStats(th)[2][1:, 4].astype(float)
    a = a[(a > 30) & (a < 0.02 * g.size)]
    return float(np.sqrt(np.median(a[a > 0.3 * np.median(a)]) * 64) / PPU_AREA_RATIO)


def to_original(P, zoom):
    return P.tolist() if zoom == 1.0 else (P / zoom).round(1).tolist()


def working_image(img):
    """(image to measure, zoom = working px / original px)."""
    ppu = rough_ppu(img)
    if ppu <= WORK_PPU_MAX:
        return img, 1.0
    z = WORK_PPU / ppu
    return cv2.resize(img, None, fx=z, fy=z, interpolation=cv2.INTER_AREA), z


def number_by_position(cx, cy, entry, W, H):
    c0, c1 = entry["columns"]
    ncols, nrows = c1 - c0 + 1, entry["rows"]
    zx, zy = entry.get("zone_x", [0.0, 1.0]), entry.get("zone_y", [0.0, 1.0])
    inz = np.where((cx >= zx[0] * W) & (cx <= zx[1] * W) & (cy >= zy[0] * H) & (cy <= zy[1] * H))[0]
    ids = [None] * len(cx)
    if len(inz) == 0:
        return ids
    cols = kmeans1d(cx[inz], ncols) if ncols > 1 else np.zeros(len(inz), int)
    rows_km = kmeans1d(cy[inz], nrows) if nrows > 1 else np.zeros(len(inz), int)
    rows = np.zeros(len(inz), int)
    for c in range(ncols):
        ii = np.where(cols == c)[0]
        if len(ii) == nrows:
            rows[ii[np.argsort(cy[inz][ii])]] = np.arange(nrows)
        else:                                   # missing or extra piece: rely on the global rows
            rows[ii] = rows_km[ii]
    grid = entry.get("number_grid")
    for j, i in enumerate(inz):
        number = grid[rows[j]][cols[j]] if grid else rows[j] * PLATE_COLS + c0 + cols[j]
        if number:
            ids[i] = piece_id(entry["sheet"], int(number))
    return ids


def fit_scale(recs, constant):
    """log(side) = a_long | a_short + b·x + c·y (x, y in thousands of px)."""
    X, Y = [], []
    for r in recs:
        s = r["side_len_px"]
        a, b = (s[0] + s[2]) / 2, (s[1] + s[3]) / 2
        xn, yn = (0.0, 0.0) if constant else (r["cx"] / 1000, r["cy"] / 1000)
        X.append([1, 0, xn, yn]); Y.append(np.log(max(a, b)))
        X.append([0, 1, xn, yn]); Y.append(np.log(min(a, b)))
    coef, *_ = np.linalg.lstsq(np.array(X), np.array(Y), rcond=None)
    return coef


def measure(entry):
    key = photo_key(entry["file"])
    img, zoom = working_image(load_photo(entry["file"]))
    H, W = img.shape[:2]
    seg_fn, cont_fn = METHODS[entry["contour"]]
    norm, cnts, rejected, contact = seg_fn(img)
    contours = [cont_fn(norm, c) for c in cnts]
    longs, shorts = [], []
    for pts, _ in contours:
        idx = find_corners(pts)
        if idx is None:
            continue
        Q = pts[idx]
        s = np.linalg.norm(Q - np.roll(Q, -1, 0), axis=1)
        a, b = (s[0] + s[2]) / 2, (s[1] + s[3]) / 2
        longs.append(max(a, b)); shorts.append(min(a, b))
    expected = (float(np.median(longs)), float(np.median(shorts)))
    cx = np.array([cv2.moments(c.reshape(-1, 1, 2))["m10"] / cv2.moments(c.reshape(-1, 1, 2))["m00"] for c in cnts])
    cy = np.array([cv2.moments(c.reshape(-1, 1, 2))["m01"] / cv2.moments(c.reshape(-1, 1, 2))["m00"] for c in cnts])
    ids = number_by_position(cx, cy, entry, W, H)
    corr = load_json(os.path.join(DATA, "corrections.json")).get("corners_px", {}).get(key, {})
    recs = []
    for k, (c, (pts, sinfo), touch) in enumerate(zip(cnts, contours, contact)):
        r = dict(view=f"{key}#{k}", photo=key, file=entry["file"], sheet=entry["sheet"], k=k, id=ids[k],
                 method="position" if ids[k] else "out_of_zone", cx=float(cx[k]), cy=float(cy[k]), W=W, H=H,
                 contour=entry["contour"], seg=sinfo, flags=[], pts=pts)
        if sinfo["fallback"]:
            r["flags"].append("subpixel_contour_failed")
        if sinfo["glare"]:
            r["flags"].append("glare_filled")
        if touch:
            r["flags"].append("touching_neighbour")
        if ids[k] in corr:                     # corners entered by hand (px of the original photo)
            Q = np.array(corr[ids[k]], float) * zoom
            idx = sorted(int(np.argmin(np.linalg.norm(pts - q, axis=1))) for q in Q)
            r["flags"].append("manual_corners")
        else:
            idx = find_corners(pts, expected)
        if idx is None:
            r["flags"].append("corners_not_found"); recs.append(r); continue
        C, ok = refine_corners(pts, idx)
        if not all(ok):
            r["flags"].append("corner_not_refined")
        r.update(idx=idx, C=C, side_len_px=[float(np.linalg.norm(C[(j + 1) % 4] - C[j])) for j in range(4)])
        recs.append(r)
    good = [r for r in recs if "C" in r and r["id"]]          # pieces of this sheet only
    coef = fit_scale(good, constant=len(good) < 30)
    raw, out = {}, []
    for r in recs:
        pts = r.pop("pts")
        r["cx"], r["cy"], r["W"], r["H"] = r["cx"] / zoom, r["cy"] / zoom, int(round(W / zoom)), int(round(H / zoom))
        if "C" not in r:
            out.append(r); continue
        f = 100.0 / np.exp(coef[0] + coef[2] * r["cx"] * zoom / 1000 + coef[3] * r["cy"] * zoom / 1000)   # u per working px
        g = canonical(pts, r.pop("idx"), r["C"], f)
        C = r.pop("C") / zoom; r.pop("side_len_px")
        pts = pts / zoom
        r.update(ppu=float(1 / f / zoom), rotation_deg=g["rotation_deg"], core_angles=g["core_angles"],
                 types="".join(s["type"] for s in g["sides"]),
                 core_w=float((g["sides"][0]["L"] + g["sides"][2]["L"]) / 2),
                 core_h=float((g["sides"][1]["L"] + g["sides"][3]["L"]) / 2))
        if "F" in r["types"]:
            r["flags"].append("flat_side_detected")
        raw.update({f"{r['view']}__contour_px": pts, f"{r['view']}__corners_px": C[g["order"]],
                    f"{r['view']}__contour_u": g["contour_u"], f"{r['view']}__corners_u": g["corners_u"],
                    f"{r['view']}__profiles": np.stack([s["profile"] for s in g["sides"]])})
        out.append(r)
    info = dict(photo=key, file=entry["file"], sheet=entry["sheet"], W=int(round(W / zoom)), H=int(round(H / zoom)),
                n=len(cnts), px_per_u=float(np.exp(coef[0]) / 100 / zoom), working_zoom=round(zoom, 4),
                scale_field=[float(v) for v in coef],
                rejects=[dict(reason=w, contour=to_original(cv2.approxPolyDP(c, 3, True).reshape(-1, 2), zoom))
                         for w, c in rejected])
    return info, out, raw


def main():
    with Pool(6) as pool:
        results = pool.map(measure, SHEET_PHOTOS)
    os.makedirs(OUT, exist_ok=True)
    photos, views, raw = [], [], {}
    for info, out, rw in results:
        photos.append(info); views += out; raw.update(rw)
        ids = [r["id"] for r in out if r["id"]]
        dup = len(ids) - len(set(ids))
        print(f"{info['photo']:<26} {info['n']:3d} pieces, {info['px_per_u']:.2f} px/u, numbered {len(ids)}"
              f"{f' (duplicates {dup})' if dup else ''}, out of zone {sum(1 for r in out if not r['id'])}, "
              f"rejects {len(info['rejects'])}, alerts {sum(1 for r in out if r['flags'])}", flush=True)
    save_json(os.path.join(OUT, "measurements.json"), dict(photos=photos, views=views))
    np.savez_compressed(os.path.join(OUT, "measurements_raw.npz"), **raw)


if __name__ == "__main__":
    main()
