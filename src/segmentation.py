"""Segmentation of the black pieces on white paper and sub-pixel contour.

Two methods, chosen per photo in photos/manifest.json (field "contour"):
- "mid_level" (whole-sheet photos, ≈ 2.2 px/u): contour at the median level piece/paper, thick glare filled
  (sheet B had glare);
- "top_surface" (close-up photos, ≈ 3 px/u): the grey-blue cardboard edge shows towards the photo borders;
  no filling (it swallowed the edge), level vin + 0.6·(vout − vin) (minimises the deviation to the "mid_level"
  views, measured on 150 pieces), touching pieces separated.
Each segmentation function returns (norm, contours, rejected [(reason, contour)], contact [bool]).
"""
import os
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter, binary_fill_holes, label
from skimage.measure import find_contours

TOP_SURFACE_FRAC = float(os.environ.get("TOP_SURFACE_FRAC", "0.6"))


def _elongated(c):
    """Very elongated object (ruler edge, stroke): minimal rectangle ratio > 2.2."""
    (_, _), (w, h), _ = cv2.minAreaRect(c.reshape(-1, 1, 2).astype(np.float32))
    return max(w, h) > 2.2 * max(1.0, min(w, h))


def _closed_largest(cs):
    best, ba = None, 0
    for cc in cs:
        if len(cc) < 100 or np.linalg.norm(cc[0] - cc[-1]) > 1.5:
            continue
        xy = cc[:, ::-1]
        a = abs(0.5 * np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1]))
        if a > ba:
            best, ba = xy, a
    return best


def _resample_clockwise(best):
    """~1 point per px, clockwise on screen (shoelace sum > 0 with y pointing down)."""
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(np.vstack([best, best[:1]]), axis=0), axis=1))]
    t = np.linspace(0, d[-1], int(d[-1]), endpoint=False)
    closed = np.vstack([best, best[:1]])
    res = np.stack([np.interp(t, d, closed[:, 0]), np.interp(t, d, closed[:, 1])], 1)
    if np.sum(res[:, 0] * np.roll(res[:, 1], -1) - np.roll(res[:, 0], -1) * res[:, 1]) < 0:
        res = res[::-1].copy()
    return res


def _crop(norm, c, pad):
    """Crop around the raw contour c (padded with white at the photo border) + mask of the contour."""
    x, y, w, h = cv2.boundingRect(c.reshape(-1, 1, 2))
    H, W = norm.shape
    x0, y0, x1, y1 = x - pad, y - pad, x + w + pad, y + h + pad
    crop = np.full((y1 - y0, x1 - x0), 255, np.float32)
    sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    crop[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = norm[sy0:sy1, sx0:sx1]
    m = np.zeros(crop.shape, np.uint8)
    cv2.drawContours(m, [(c - [x0, y0]).reshape(-1, 1, 2).astype(np.int32)], -1, 1, -1)
    return crop, m, x0, y0


# ================================================================ "mid_level" method
def normalise_mid_level(gray):
    """Corrects lighting: divides by an estimate of the background (wide closing)."""
    gray = gray.astype(np.float32)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (151, 151))
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, k)
    bg = cv2.GaussianBlur(bg, (0, 0), 25)
    return np.clip(gray / (bg + 1) * 255, 0, 255).astype(np.float32)


def segment_mid_level(img):
    H, W = img.shape[:2]
    norm = normalise_mid_level(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    _, th = cv2.threshold(norm.astype(np.uint8), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    big = [c for c in cnts if cv2.contourArea(c) >= 5000]
    med = np.median([cv2.contourArea(c) for c in big])
    keep, rejected = [], []
    for c in big:
        x, y, w, h = cv2.boundingRect(c)
        a = cv2.contourArea(c)
        if y + h >= H - 3 and a < 0.9 * med:      # cut piece (next sheet visible at the bottom)
            rejected.append(("photo_edge", c)); continue
        if not (0.5 * med < a < 1.6 * med) or _elongated(c):
            rejected.append(("area_or_shape", c)); continue
        keep.append(c.reshape(-1, 2))
    return norm, keep, rejected, [False] * len(keep)


def contour_mid_level(norm, c):
    """Sub-pixel contour at the median inside/paper level; thick glare filled, shadow ring ignored."""
    crop, m, x0, y0 = _crop(norm, c, 30)
    inner = cv2.erode(m, np.ones((17, 17), np.uint8))
    ring = cv2.dilate(m, np.ones((51, 51), np.uint8)) - cv2.dilate(m, np.ones((21, 21), np.uint8))
    vin, vout = np.median(crop[inner > 0]), np.median(crop[ring > 0])
    level = 0.5 * (vin + vout)
    m2 = gaussian_filter(crop, 1.5) < (vout - 0.2 * (vout - vin))
    m2 = binary_fill_holes(m2 | (m > 0))
    lab, _ = label(m2)
    ids = np.unique(lab[inner > 0]); ids = ids[ids > 0]
    m2 = np.isin(lab, ids).astype(np.uint8)
    g = (m2 > 0) & (m == 0)                    # shadow halo (thin) + glare (thick)
    g = cv2.morphologyEx(g.astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    glare = g.sum() > 0.01 * m.sum()
    m = np.maximum(m, g)
    sm = gaussian_filter(crop, 1.0)
    core = cv2.erode(m, np.ones((7, 7), np.uint8))
    outside = 1 - cv2.dilate(m, np.ones((7, 7), np.uint8))
    sm = np.where(core > 0, np.minimum(sm, vin), sm)
    sm = np.where(outside > 0, np.maximum(sm, vout), sm)
    best = _closed_largest(find_contours(sm, level))
    info = dict(level=float(level), vin=float(vin), vout=float(vout), fallback=best is None, glare=bool(glare))
    if best is None:
        return c.astype(np.float64), info
    return _resample_clockwise(best[:-1] + [x0, y0]), info


# ================================================================ "top_surface" method
def normalise_fast(gray, k):
    """Divides by the background (wide closing computed at 1/4 resolution)."""
    g = gray.astype(np.float32)
    small = cv2.resize(g, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
    ks = max(15, int(k / 4) | 1)
    bg = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks)))
    bg = cv2.GaussianBlur(bg, (0, 0), 6)
    bg = cv2.resize(bg, (g.shape[1], g.shape[0]), interpolation=cv2.INTER_LINEAR)
    return np.clip(g / (bg + 1) * 255, 0, 255).astype(np.float32)


def split_blob(c, med):
    """Splits 2–3 touching pieces: growing erosion until distinct seeds appear,
    then watershed on the distance to the border."""
    x, y, w, h = cv2.boundingRect(c)
    pad = 5
    m = np.zeros((h + 2 * pad, w + 2 * pad), np.uint8)
    cv2.drawContours(m, [(c.reshape(-1, 2) - [x - pad, y - pad]).reshape(-1, 1, 2)], -1, 255, -1)
    for k in range(11, 92, 8):
        er = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        n, lab, st, _ = cv2.connectedComponentsWithStats(er)
        big = [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] > 0.08 * med]
        if len(big) >= 2:
            break
    else:
        return None
    markers = np.zeros(m.shape, np.int32)
    for j, i in enumerate(big):
        markers[lab == i] = j + 2
    markers[m == 0] = 1
    dist = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    rgb = cv2.cvtColor(255 - np.clip(dist * 4, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    cv2.watershed(rgb, markers)
    out = []
    for j in range(len(big)):
        part = ((markers == j + 2) & (m > 0)).astype(np.uint8)
        cs, _ = cv2.findContours(part, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cc = max(cs, key=cv2.contourArea)
        if not (0.6 * med < cv2.contourArea(cc) < 1.5 * med):
            return None
        out.append(cc.reshape(-1, 2) + [x - pad, y - pad])
    return out


def segment_top_surface(img):
    H, W = img.shape[:2]
    norm = normalise_fast(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 351)
    _, th = cv2.threshold(norm.astype(np.uint8), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    big = [c for c in cnts if cv2.contourArea(c) >= 20000]
    med = np.median([cv2.contourArea(c) for c in big if not _elongated(c)])
    keep, rejected, contact = [], [], []
    for c in big:
        x, y, w, h = cv2.boundingRect(c)
        a = cv2.contourArea(c)
        if x <= 2 or y <= 2 or x + w >= W - 3 or y + h >= H - 3:
            rejected.append(("photo_edge", c)); continue
        if _elongated(c):
            rejected.append(("area_or_shape", c)); continue
        if 1.5 * med <= a < 3.2 * med:                 # touching pieces
            parts = split_blob(c, med)
            if parts:
                keep += parts; contact += [True] * len(parts)
                continue
        if not (0.6 * med < a < 1.5 * med):
            rejected.append(("area_or_shape", c)); continue
        keep.append(c.reshape(-1, 2)); contact.append(False)
    return norm, keep, rejected, contact


def contour_top_surface(norm, c, frac=TOP_SURFACE_FRAC):
    """Sub-pixel contour at level vin + frac·(vout − vin), no glare filling.
    Pixels more than 4 px away from the raw mask are forced to paper (possible neighbour after splitting)."""
    crop, m, x0, y0 = _crop(norm, c, 40)
    inner = cv2.erode(m, np.ones((41, 41), np.uint8))
    ring = cv2.dilate(m, np.ones((71, 71), np.uint8)) - cv2.dilate(m, np.ones((41, 41), np.uint8))
    vin, vout = float(np.median(crop[inner > 0])), float(np.median(crop[ring > 0]))
    level = vin + frac * (vout - vin)
    sm = gaussian_filter(crop, 1.2)
    core = cv2.erode(m, np.ones((31, 31), np.uint8))
    outside = 1 - cv2.dilate(m, np.ones((9, 9), np.uint8))
    sm = np.where(core > 0, np.minimum(sm, vin), sm)
    sm = np.where(outside > 0, np.maximum(sm, vout), sm)
    best = _closed_largest(find_contours(sm, level))
    info = dict(level=level, vin=vin, vout=vout, fallback=best is None, glare=False)
    if best is None:
        return c.astype(np.float64), info
    return _resample_clockwise(best[:-1] + [x0, y0]), info


METHODS = {"mid_level": (segment_mid_level, contour_mid_level),
           "top_surface": (segment_top_surface, contour_top_surface)}
