"""Piece geometry: corners, sides, profiles, canonical frame, numbering by position.

Conventions (docs/CONVENTIONS.md): contours clockwise on screen (y pointing down); side profile =
128 points, local frame x = corner -> corner chord, y = outwards; types T (tab), B (blank), F (flat).
"""
import itertools
import numpy as np
from scipy.ndimage import gaussian_filter1d

NPROF = 128


# ---------------------------------------------------------------- corners
def local_angle(pts, k):
    n = len(pts)
    prev = pts[(np.arange(n) - k) % n] - pts
    nxt = pts[(np.arange(n) + k) % n] - pts
    cosang = (prev * nxt).sum(1) / (np.linalg.norm(prev, axis=1) * np.linalg.norm(nxt, axis=1) + 1e-9)
    ang = np.degrees(np.arccos(np.clip(cosang, -1, 1)))
    cross = prev[:, 0] * nxt[:, 1] - prev[:, 1] * nxt[:, 0]
    return ang, cross


def find_corners(pts, expected=None):
    """4 corner indices: convex sharp points forming the best rectangle.
    expected = (long side, short side) in px: penalises quadrilaterals of the wrong size."""
    n = len(pts)
    sp = np.stack([gaussian_filter1d(pts[:, 0], 2, mode="wrap"), gaussian_filter1d(pts[:, 1], 2, mode="wrap")], 1)
    ang, cross = local_angle(sp, 14)
    convex = cross < 0          # clockwise contour (y down): convex corner <=> cross product < 0
    cand, taken = [], np.zeros(n, bool)
    for i in np.argsort(ang):
        if ang[i] > 150 or len(cand) >= 18:
            break
        if not convex[i] or taken[i]:
            continue
        cand.append(i)
        taken[(i + np.arange(-25, 26)) % n] = True
    cand = sorted(cand)
    if len(cand) < 4:
        return None
    combos = list(itertools.combinations(cand, 4))
    areas = []
    for q in combos:
        P = sp[list(q)]
        areas.append(abs(0.5 * np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1])))
    amax = max(areas)
    scored = []
    for q, A in zip(combos, areas):
        P = sp[list(q)]
        s = np.linalg.norm(P - np.roll(P, -1, 0), axis=1)
        if s.min() < 0.35 * s.max():
            continue
        v1, v2 = np.roll(P, 1, 0) - P, np.roll(P, -1, 0) - P
        qa = np.degrees(np.arccos(np.clip((v1 * v2).sum(1) / (np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1)), -1, 1)))
        cost = (np.abs(qa - 90).sum() / 90 + (abs(s[0] - s[2]) + abs(s[1] - s[3])) / s.mean()
                + 0.5 * np.abs(ang[list(q)] - 90).sum() / 90 - 1.5 * A / amax)
        if expected is not None:
            lo, sh = expected
            e1 = (abs(s[0] - lo) + abs(s[2] - lo) + abs(s[1] - sh) + abs(s[3] - sh)) / (lo + sh)
            e2 = (abs(s[0] - sh) + abs(s[2] - sh) + abs(s[1] - lo) + abs(s[3] - lo)) / (lo + sh)
            cost += 4.0 * min(e1, e2)
        scored.append((cost, list(q)))
    if not scored:
        return None
    # tie-break between the best candidates: a good set of corners gives sides that are "flat" at their ends
    scored.sort(key=lambda t: t[0])
    best, bestc = None, 1e18
    for cost, q in scored[:30]:
        ed = 0.0
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            seg = sp[a:b + 1] if b > a else np.vstack([sp[a:], sp[:b + 1]])
            p0, p1 = seg[0], seg[-1]
            L = np.linalg.norm(p1 - p0)
            ux = (p1 - p0) / L
            uy = np.array([ux[1], -ux[0]])
            m = len(seg)
            ends = np.r_[0:max(2, int(0.15 * m)), int(0.85 * m):m]
            ed += np.abs((seg[ends] - p0) @ uy).max() / L
        total = cost + 8.0 * ed / 4
        if total < bestc:
            bestc, best = total, q
    return best


def fit_line(P):
    m = P.mean(0)
    _, _, vt = np.linalg.svd(P - m)
    return m, vt[0]


def refine_corners(pts, idx):
    """Corner = intersection of the lines fitted on the neighbouring straight portions (corrects rounding)."""
    n = len(pts)
    out, ok = [], []
    for k, i in enumerate(idx):
        i_prev, i_next = idx[k - 1], idx[(k + 1) % 4]
        len_next = (i_next - i) % n
        len_prev = (i - i_prev) % n
        a0, a1 = 6, max(12, int(0.2 * len_next))
        b0, b1 = 6, max(12, int(0.2 * len_prev))
        after = pts[(i + np.arange(a0, a1)) % n]
        before = pts[(i - np.arange(b0, b1)) % n]
        m1, d1 = fit_line(after)
        m2, d2 = fit_line(before)
        A = np.array([d1, -d2]).T
        if abs(np.linalg.det(A)) < 1e-6:
            out.append(pts[i]); ok.append(False); continue
        t = np.linalg.solve(A, m2 - m1)
        X = m1 + t[0] * d1
        if np.linalg.norm(X - pts[i]) > 10:
            out.append(pts[i]); ok.append(False)
        else:
            out.append(X); ok.append(True)
    return np.array(out), ok


# ---------------------------------------------------------------- sides
def side_points(pts, idx, k):
    a, b = idx[k], idx[(k + 1) % 4]
    return pts[a:b + 1] if b > a else np.vstack([pts[a:], pts[:b + 1]])


def side_descriptor(seg, p0, p1):
    """Profile of the side in its own frame: x along the chord p0->p1, y outwards."""
    L = np.linalg.norm(p1 - p0)
    ux = (p1 - p0) / L
    uy = np.array([ux[1], -ux[0]])            # outwards for a clockwise contour (y down)
    seg = np.vstack([p0, seg, p1])
    loc = np.stack([(seg - p0) @ ux, (seg - p0) @ uy], 1)
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(loc, axis=0), axis=1))]
    t = np.linspace(0, d[-1], NPROF)
    prof = np.stack([np.interp(t, d, loc[:, 0]), np.interp(t, d, loc[:, 1])], 1)
    yv = loc[:, 1]
    hmax, hmin = yv.max(), yv.min()
    if hmax > 0.15 * L and hmax >= -hmin:
        kind, sgn = "T", 1.0
    elif -hmin > 0.15 * L:
        kind, sgn = "B", -1.0
    else:
        kind, sgn = "F", 1.0
    desc = dict(type=kind, L=float(L), arc=float(d[-1]), profile=prof)
    if kind != "F":
        yy = sgn * loc[:, 1]
        h = yy.max()
        pos = loc[np.argmax(yy), 0] / L
        widths = []
        for f in np.linspace(0.1, 0.95, 18):
            lv = f * h
            s = np.sign(yy - lv)
            cr = np.where(s[:-1] * s[1:] < 0)[0]
            if len(cr) >= 2:
                xs = [loc[j, 0] + (lv - yy[j]) * (loc[j + 1, 0] - loc[j, 0]) / (yy[j + 1] - yy[j]) for j in cr]
                widths.append((f, max(xs) - min(xs)))
        wl = np.array(widths) if widths else np.zeros((0, 2))
        neck = wl[wl[:, 0] <= 0.45, 1].min() if np.any(wl[:, 0] <= 0.45) else np.nan
        head = wl[wl[:, 0] >= 0.4, 1].max() if np.any(wl[:, 0] >= 0.4) else np.nan
        desc.update(height=float(h), pos=float(pos), neck=float(neck), head=float(head))
    return desc


# ---------------------------------------------------------------- numbering by position
def kmeans1d(x, k, iters=60):
    """1-D k-means; two starts (quantiles, and a cut at the k−1 largest gaps — right when the groups are
    unevenly filled, e.g. a row of 8 pieces then a row of 4), the partition of lowest inertia is kept."""
    x = np.asarray(x, float)
    xs = np.sort(x)
    cuts = np.sort(np.argsort(np.diff(xs))[::-1][:k - 1]) + 1
    starts = [np.quantile(x, (np.arange(k) + 0.5) / k)]
    if len(x) >= k:
        starts.append(np.array([g.mean() for g in np.split(xs, cuts)]))
    best = None
    for c in starts:
        for _ in range(iters):
            lab = np.argmin(np.abs(x[:, None] - c[None]), 1)
            c = np.array([x[lab == j].mean() if np.any(lab == j) else c[j] for j in range(k)])
        sse = float(((x - c[lab]) ** 2).sum())
        if best is None or sse < best[0] - 1e-9:
            best = (sse, c, lab)
    _, c, lab = best
    order = np.argsort(c)
    rank = np.empty(k, int)
    rank[order] = np.arange(k)
    return rank[lab]


def rotate(P, theta):
    c, s = np.cos(theta), np.sin(theta)
    return P @ np.array([[c, s], [-s, c]])


def canonical(pts, idx, C, scale):
    """Canonical "standing piece" frame (short side on top = N, minimal rotation from the photo).
    pts: contour (px), idx: corner indices, C: refined corners (px), scale: u per px.
    Returns dict(rotation_deg, contour_u, corners_u, sides[...], order); order[j] = photo corner of side j."""
    s = [float(np.linalg.norm(C[(k + 1) % 4] - C[k])) for k in range(4)]
    short_pair = 0 if (s[0] + s[2]) < (s[1] + s[3]) else 1
    best = None
    for k in (short_pair, short_pair + 2):
        v = C[(k + 1) % 4] - C[k]
        th = -np.arctan2(v[1], v[0])
        th = (th + np.pi) % (2 * np.pi) - np.pi
        if best is None or abs(th) < abs(best[1]):
            best = (k, th)
    kN, th = best
    center = C.mean(0)
    can = rotate(pts - center, th) * scale
    Cc = rotate(C - center, th) * scale
    order = [(kN + j) % 4 for j in range(4)]
    sides = []
    for j, k in enumerate(order):
        seg = rotate(side_points(pts, idx, k) - center, th) * scale
        d = side_descriptor(seg[1:-1] if len(seg) > 2 else seg, Cc[k], Cc[(k + 1) % 4])
        d["name"] = "NESW"[j]
        sides.append(d)
    Q = Cc[order]
    v1, v2 = np.roll(Q, 1, 0) - Q, np.roll(Q, -1, 0) - Q
    qa = np.degrees(np.arccos(np.clip((v1 * v2).sum(1) / (np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1)), -1, 1)))
    return dict(rotation_deg=float(np.degrees(th)), contour_u=can, corners_u=Q, sides=sides, order=order,
                core_angles=qa.round(2).tolist())
