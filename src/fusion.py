"""Stage 2 — reference catalog: fusion of all the views of each piece.

Input: data/measurements/ (one view per piece and per photo of ITS sheet; "out_of_zone" views, pieces of
another sheet entering the frame, are ignored: lying at another height, their scale would be wrong).
Same process for every sheet: all the declared photos of a sheet are fused.

Piece frame = canonical frame of its most recent view by position (current state of the sheets):
"standing" piece, minimal rotation from the sheet laid the right way up. rotation_deg = clockwise rotation
that brings the piece, as it lies on its sheet, to this frame.

View weight = (px/u)² / (1 + (d / 0.7)⁴) × alerts, d = distance to the photo centre (0 at the centre, 1 at
the corner): the cardboard edge, visible towards the borders, degrades the contour (deviation to the other views
≈ 1.3 u at the centre, ≈ 2 u at the border). Alerts: corner not refined × 0.6; split from a neighbour × 0.7;
glare filled × 0.5; manual corners × 3; contour failure or corners not found: 0. Core plausibility:
× exp(−((angle deviation − 10°) / 4°)²) beyond 10° from the right angle, × exp(−((relative size deviation − 10 %)
/ 5 %)²) beyond 10 % from the median width or height (90 × 100 u): settles views that disagree when one has
wrong corners.
Scale: each photo is recalibrated on the pieces of its sheet seen on other photos (calibrate_scales: log-linear
field in x, y, which also corrects a photo taken at an angle).
Side by side: "median" view (closest to the others, weighted); views within SIDE_TOL u of it are averaged, the
others dropped. Core (4 corners): weighted mean after rigid alignment, same rules.

Outputs: data/pieces.json, data/pieces_raw.npz (catalog read by everything downstream), data/fusion.json
(decisions), contact sheets reports/measurements/<photo>.jpg (green: view agreeing with the fused piece; orange:
fair deviation; red: dropped; grey: out of zone, piece of another sheet; magenta: blob rejected at segmentation).
"""
import os, sys
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, REPORTS, SIDE_NAMES, load_json, save_json, load_photo
from match import procrustes_rms
from geometry import side_descriptor

SIDE_TOL = 3.0             # u: maximum deviation between two views of the same side to average them
QUAD_TOL = 3.0             # u: maximum deviation between two aligned cores
STEP_U = 0.5               # step of the rebuilt contour
MEAS = os.path.join(DATA, "measurements")


# ---------------------------------------------------------------- tools
def rot90(P, r):
    """r clockwise quarter turns on screen (y down)."""
    for _ in range(r % 4):
        P = np.stack([-P[..., 1], P[..., 0]], -1)
    return P


def rigid(A, B, w=None):
    w = np.ones(len(A)) if w is None else w
    ma, mb = (A * w[:, None]).sum(0) / w.sum(), (B * w[:, None]).sum(0) / w.sum()
    U, _, Vt = np.linalg.svd(((A - ma) * w[:, None]).T @ (B - mb))
    D = np.diag([1, np.sign(np.linalg.det(Vt.T @ U.T))])
    return Vt.T @ D @ U.T, ma, mb


def chord_frame(prof):
    p0, p1 = prof[0], prof[-1]
    ux = (p1 - p0) / np.linalg.norm(p1 - p0)
    uy = np.array([ux[1], -ux[0]])
    return np.stack([(prof - p0) @ ux, (prof - p0) @ uy], 1)


def shape_scores(Q, prof):
    """Mean RMS (u) over 4 sides between Q (4,128,2) and each piece of prof (M,4,128,2), for r = 0..3:
    side k of Q <-> side (k + r) % 4 of the piece."""
    S = np.full((prof.shape[0], 4), np.inf)
    for r in range(4):
        S[:, r] = np.sqrt(sum(procrustes_rms(Q[k], prof[:, (k + r) % 4]) ** 2 for k in range(4)) / 4)
    return S


def weight(v, med_w=90.0, med_h=100.0):
    d = np.hypot(v["cx"] - v["W"] / 2, v["cy"] - v["H"] / 2) / np.hypot(v["W"] / 2, v["H"] / 2)
    f = v["flags"]
    if "subpixel_contour_failed" in f or "corners_not_found" in f:
        return 0.0, d
    w = v["ppu"] ** 2 / (1 + (d / 0.7) ** 4)
    for flag, m in (("corner_not_refined", 0.6), ("touching_neighbour", 0.7), ("glare_filled", 0.5),
                    ("manual_corners", 3.0)):
        if flag in f:
            w *= m
    if "core_angles" in v:
        da = float(np.abs(np.array(v["core_angles"]) - 90).max())
        w *= np.exp(-(max(0.0, da - 10) / 4) ** 2)
        for x, m in ((v["core_w"], med_w), (v["core_h"], med_h)):
            w *= np.exp(-(max(0.0, abs(x / m - 1) - 0.10) / 0.05) ** 2)
    return float(w), float(d)


MIN_SLOPE_PIECES = 5       # minimum shared pieces to estimate a scale slope in one direction
MIN_SLOPE_SPAN = 0.25      # minimum span (fraction of the image) of the shared pieces in that direction


def calibrate_scales(by_id, n_iter=40, damping=0.7):
    """Scale of each photo: log s(x, y) = a + bx·(x − ½) + by·(y − ½), x, y as fractions of the image.
    measure_sheets gives a provisional scale (median size of the pieces of the photo): exact to ±0.5 % on A–D
    (50 to 100 pieces), biased on a photo with few pieces, and blind to a photo taken at an angle (apparent size
    varying from one border to the other). Each photo is recalibrated on the pieces it shares with the other
    photos of the sheet (size = √(core width × height)): robust least squares on the deviation to the median of
    the other views, iterated. Slope in x (in y) only if ≥ MIN_SLOPE_PIECES shared pieces spanning ≥ MIN_SLOPE_SPAN
    of the image in that direction, otherwise a constant factor. At each iteration, fix_gauge fixes what the
    shared pieces cannot determine (level of the sheet, trend with the place on the sheet).
    Returns {photo: (a, bx, by)}."""
    V = [v for lst in by_id.values() for v in lst if v["w"] > 0 and "core_w" in v]
    ls0 = {id(v): 0.5 * float(np.log(v["core_w"] * v["core_h"])) for v in V}
    pos = {id(v): (v["cx"] / v["W"] - 0.5, v["cy"] / v["H"] - 0.5) for v in V}
    coef = {v["photo"]: np.zeros(3) for v in V}
    groups = [[v for v in lst if id(v) in ls0] for lst in by_id.values()]

    def ls(v):
        x, y = pos[id(v)]
        return ls0[id(v)] + coef[v["photo"]] @ [1.0, x, y]

    for _ in range(n_iter):
        rows = {}
        for g in groups:
            for v in g:
                others = [ls(o) for o in g if o["photo"] != v["photo"]]
                if others:
                    rows.setdefault(v["photo"], []).append((*pos[id(v)], float(np.median(others)) - ls(v)))
        for ph, R in rows.items():
            R = np.array(R)
            cols = [0] + [k for k in (1, 2) if len(R) >= MIN_SLOPE_PIECES and np.ptp(R[:, k - 1]) >= MIN_SLOPE_SPAN]
            A = np.column_stack([np.ones(len(R)), R[:, 0], R[:, 1]])[:, cols]
            keep = np.ones(len(R), bool)
            for _ in range(2):
                d, *_ = np.linalg.lstsq(A[keep], R[keep, 2], rcond=None)
                res = R[:, 2] - A @ d
                keep = np.abs(res) <= max(0.01, 3 * np.median(np.abs(res[keep])))
            delta = np.zeros(3)
            delta[cols] = d
            coef[ph] = coef[ph] + damping * delta
        fix_gauge(V, coef, pos)
    return {ph: tuple(float(x) for x in c) for ph, c in coef.items()}


def fix_gauge(V, coef, pos):
    """Two corrections cannot be observed from the shared pieces, because the photos of a sheet only share
    pieces among themselves and all list the pieces in the same order:
    1. the overall level of the sheet (it would drift: −3 % on E without this lock);
    2. a size trend with the row or column of the piece on the sheet (a tilt common to all the photos looks
       like pieces of different sizes: +6 % from E001 to E010 without this lock).
    Assumptions, the same for every sheet: the size of a piece does not depend on its place on the sheet
    (zero slope by row and by column, if the sheet spans ≥ 3 rows / columns); the level stays that of
    measure_sheets (median long side of the pieces of each photo = 100 u), averaged over the photos of the sheet."""
    for sheet in {v["sheet"] for v in V}:
        Vp = [v for v in V if v["sheet"] == sheet]
        rc = {id(v): divmod(int(v["id"][1:]) - 1, 10) for v in Vp}           # (row, column) on the sheet
        corr = {id(v): float(coef[v["photo"]] @ [1.0, *pos[id(v)]]) for v in Vp}
        by_piece = {}
        for v in Vp:
            by_piece.setdefault(v["id"], []).append(corr[id(v)])
        pid = sorted(by_piece)
        RC = np.array([divmod(int(p[1:]) - 1, 10) for p in pid], float)
        c = np.array([np.mean(by_piece[p]) for p in pid])
        terms = [k for k in (0, 1) if len(np.unique(RC[:, k])) >= 3]
        beta = np.zeros(2)
        if terms:
            A = np.column_stack([np.ones(len(c))] + [RC[:, k] for k in terms])
            sol, *_ = np.linalg.lstsq(A, c, rcond=None)
            beta[terms] = sol[1:]
        for ph in {v["photo"] for v in Vp}:
            Vh = [v for v in Vp if v["photo"] == ph]
            P = np.array([pos[id(v)] for v in Vh])
            t = np.array([beta @ rc[id(v)] for v in Vh])
            cols = [0] + [k for k in (1, 2) if np.ptp(P[:, k - 1]) >= MIN_SLOPE_SPAN]
            A = np.column_stack([np.ones(len(Vh)), P[:, 0], P[:, 1]])[:, cols]
            d, *_ = np.linalg.lstsq(A, t, rcond=None)
            coef[ph][cols] -= d
        m = float(np.median([coef[v["photo"]] @ [1.0, *pos[id(v)]] for v in Vp]))
        for ph in {v["photo"] for v in Vp}:
            coef[ph][0] -= m


def fuse_profiles(profs, w):
    n = len(profs)
    D = np.stack([procrustes_rms(profs[i], profs) for i in range(n)])
    ref = int(np.argmin((D * w[None]).sum(1) - 1e-6 * w))
    keep = [i for i in range(n) if D[ref, i] <= SIDE_TOL and w[i] > 0] or [ref]
    acc, ws = np.zeros_like(profs[0]), 0.0
    for i in keep:
        R, ma, mb = rigid(profs[i], profs[ref])
        wi = w[i] if w[i] > 0 else 1.0
        acc += wi * ((profs[i] - ma) @ R.T + mb); ws += wi
    return chord_frame(acc / ws), keep, D[ref]


def fuse_quads(quads, w):
    ref = int(np.argmax(w))
    acc, ws, keep, dev = np.zeros((4, 2)), 0.0, [], []
    for i, Qi in enumerate(quads):
        R, ma, mb = rigid(Qi, quads[ref])
        Q = (Qi - ma) @ R.T + mb
        d = float(np.sqrt(((Q - quads[ref]) ** 2).sum(1).mean()))
        dev.append(d)
        if d <= QUAD_TOL and (w[i] > 0 or i == ref):
            wi = w[i] if w[i] > 0 else 1.0
            acc += wi * Q; ws += wi; keep.append(i)
    C = acc / ws
    return C - C.mean(0), keep, dev


def build_contour(C, profs):
    """Closed contour (clockwise) from the corners C (N, E, S, W) and the 4 profiles (chord frame)."""
    segs = []
    for j in range(4):
        p0, p1 = C[j], C[(j + 1) % 4]
        L = np.linalg.norm(p1 - p0)
        ux = (p1 - p0) / L
        uy = np.array([ux[1], -ux[0]])
        pr = profs[j]
        segs.append(p0 + (pr[:, 0] * (L / pr[-1, 0]))[:, None] * ux + pr[:, 1:2] * uy)
    P = np.vstack([s[:-1] for s in segs])
    # junctions of the sides: remove back-and-forth (points where the contour turns back)
    for _ in range(20):
        a, b = P - np.roll(P, 1, 0), np.roll(P, -1, 0) - P
        back = (a * b).sum(1) < 0
        if not back.any():
            break
        P = P[~back]
    closed = np.vstack([P, P[:1]])
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))]
    t = np.arange(0, d[-1], STEP_U)
    dense = np.stack([np.interp(t, d, closed[:, 0]), np.interp(t, d, closed[:, 1])], 1)
    # start at the first third of side N rather than on a corner (SVG simplification keeps a sharp start badly)
    return np.roll(dense, -int(len(segs[0]) * 0.33 * len(dense) / len(P)), 0), segs


# ---------------------------------------------------------------- fusion
def main(out_dir=DATA, photo_filter=None, photo_exclude=()):
    """photo_filter: substring of the photo name (e.g. "2026-11-18") to use only part of the views (evaluation);
    photo_exclude: substrings of photo names to leave out (leave-one-out evaluation of a photo)."""
    M = load_json(os.path.join(MEAS, "measurements.json"))
    keep = lambda name: (not photo_filter or photo_filter in name) and not any(x in name for x in photo_exclude)
    M["photos"] = [p for p in M["photos"] if keep(p["photo"])]
    M["views"] = [v for v in M["views"] if keep(v["photo"])]
    z = np.load(os.path.join(MEAS, "measurements_raw.npz"))
    photos = {p["photo"]: p for p in M["photos"]}
    order_photo = {photo_key: i for i, photo_key in enumerate(photos)}           # manifest order = chronological
    n_out_of_zone = sum(1 for v in M["views"] if v["method"] == "out_of_zone")
    views = [v for v in M["views"] if f"{v['view']}__profiles" in z and v["id"]]
    med_w = float(np.median([v["core_w"] for v in views])); med_h = float(np.median([v["core_h"] for v in views]))
    for v in views:
        v["w"], v["d"] = weight(v, med_w, med_h)
        v["prof"] = z[f"{v['view']}__profiles"]

    # 1) views by identifier; duplicates on the same photo: keep the one closest to the other views
    by_id = {}
    for v in views:
        if v["id"]:
            by_id.setdefault(v["id"], []).append(v)
    for pid, lst in by_id.items():
        photos_seen = {}
        for v in lst:
            photos_seen.setdefault(v["photo"], []).append(v)
        for ph, same in photos_seen.items():
            if len(same) > 1:
                others = [o for o in lst if o["photo"] != ph]
                def dist(v):
                    return min((shape_scores(v["prof"], o["prof"][None]).min() for o in others), default=0)
                same.sort(key=dist)
                for v in same[1:]:
                    v["id"], v["method"] = None, "duplicate_dropped"
        by_id[pid] = [v for v in lst if v["id"] == pid]

    # 2) reference view (frame): the most recent by position, ties broken by weight
    ref_of = {pid: max(lst, key=lambda v: (order_photo[v["photo"]], v["w"])) for pid, lst in by_id.items()}
    ids = sorted(ref_of)

    # 3) scale calibration (field per photo), then weights recomputed on the corrected sizes
    scales = calibrate_scales(by_id)
    for v in views:
        if not v["id"]:
            continue
        a, bx, by = scales.get(v["photo"], (0.0, 0.0, 0.0))
        s = float(np.exp(a + bx * (v["cx"] / v["W"] - 0.5) + by * (v["cy"] / v["H"] - 0.5)))
        v["s"] = s
        v["prof"] = v["prof"] * s
        v["core_w"] *= s; v["core_h"] *= s; v["ppu"] /= s
    views = [v for v in views if v["id"]]
    med_w = float(np.median([v["core_w"] for v in views])); med_h = float(np.median([v["core_h"] for v in views]))
    for v in views:
        v["w"], v["d"] = weight(v, med_w, med_h)
    print("scale calibration (factor at the centre; variation from one border to the other in x and y):")
    for ph, (a, bx, by) in scales.items():
        print(f"   {ph:<26} × {np.exp(a):.4f}   x {bx * 100:+.1f} %   y {by * 100:+.1f} %")

    pieces, raw, report = [], {}, []
    for pid in ids:
        ref = ref_of[pid]
        V = by_id[pid]
        for v in V:            # rotation of the view to the reference: side k (view) <-> side (k + r) % 4 (ref.)
            S = shape_scores(v["prof"], ref["prof"][None])[0]
            v["r"] = int(np.argmin(S)); v["rms_ref"] = float(S.min())
        w = np.array([v["w"] for v in V])
        if w.sum() == 0:
            w = np.ones(len(V))
        aligned = []
        for v in V:
            order = [(j - v["r"]) % 4 for j in range(4)]           # side j (ref.) <- side order[j] (view)
            aligned.append(dict(prof=v["prof"][order], quad=rot90(z[f"{v['view']}__corners_u"][order] * v["s"], v["r"])))
        C, qkeep, qdev = fuse_quads(np.stack([a["quad"] for a in aligned]), w)
        fprof, side_rep, kept_any = [], [], set(qkeep)
        for j in range(4):
            fp, keep, dev = fuse_profiles(np.stack([chord_frame(a["prof"][j]) for a in aligned]), w)
            fprof.append(fp); kept_any |= set(keep)
            side_rep.append(dict(side=SIDE_NAMES[j], kept=[V[i]["view"] for i in keep],
                                 deviations=[round(float(x), 2) for x in dev]))
        contour, segs = build_contour(C, fprof)
        sides = []
        for j in range(4):
            dsc = side_descriptor(segs[j][1:-1], C[j], C[(j + 1) % 4])
            dsc["name"] = SIDE_NAMES[j]
            sides.append(dsc)
        types = "".join(s["type"] for s in sides)
        Q = C
        v1, v2 = np.roll(Q, 1, 0) - Q, np.roll(Q, -1, 0) - Q
        angles = np.degrees(np.arccos(np.clip((v1 * v2).sum(1) / (np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1)), -1, 1)))
        # agreement of each view with the fused piece
        fused_prof = np.stack([s["profile"] for s in sides])
        for v, a in zip(V, aligned):
            v["rms_fusion"] = float(np.sqrt(np.mean([procrustes_rms(a["prof"][j], fused_prof[j][None])[0] ** 2 for j in range(4)])))
        disp = V[int(np.argmax(w))]
        flags = []
        if len([x for x in w if x > 0]) <= 1:
            flags.append("single_view")
        if any(len(s["kept"]) == 1 and len(V) > 1 for s in side_rep):
            flags.append("side_unconfirmed")
        if any("".join(v["types"][(j - v["r"]) % 4] for j in range(4)) != types
               for v in V if v["w"] > 0 and v.get("types")):
            flags.append("types_differ_between_views")
        if np.any(np.abs(angles - 90) > 12):
            flags.append("core_angle_out_of_tolerance")
        if "F" in types:
            flags.append("flat_side_detected")
        sheet, number = pid[0], int(pid[1:])
        row, col = (number - 1) // 10 + 1, (number - 1) % 10 + 1      # every sheet in rows of 10 (E: 10 + 2)
        pieces.append(dict(
            id=pid, sheet=sheet, number=number, row=row, col=col,
            rotation_deg=float(ref["rotation_deg"]), rotation_source=ref["view"],
            types=types, core_w=float((sides[0]["L"] + sides[2]["L"]) / 2),
            core_h=float((sides[1]["L"] + sides[3]["L"]) / 2), core_angles=angles.round(2).tolist(),
            sides=[{k: v for k, v in s.items() if k != "profile"} for s in sides], flags=flags,
            photo=dict(file=disp["file"], view=disp["view"]),
            views=[dict(view=v["view"], file=v["file"], method=v["method"], weight=round(v["w"], 2),
                        center_distance=round(v["d"], 2), scale=round(v["s"], 4), fusion_deviation_u=round(v["rms_fusion"], 2),
                        kept=i in kept_any, alerts=v["flags"]) for i, v in enumerate(V)]))
        order_disp = [(j - disp["r"]) % 4 for j in range(4)]
        raw[pid] = dict(contour_u=contour, corners_u=C, profiles=fused_prof,
                        contour_px=z[f"{disp['view']}__contour_px"], corners_px=z[f"{disp['view']}__corners_px"][order_disp])
        report.append(dict(id=pid, views=[v["view"] for v in V], weights=[round(float(x), 2) for x in w],
                           core=dict(kept=[V[i]["view"] for i in qkeep], deviations=[round(x, 2) for x in qdev]),
                           sides=side_rep))

    save_json(os.path.join(out_dir, "pieces.json"), dict(
        units="u (100 u = median long side of a piece core)", source="src/fusion.py (all views)",
        photos={k: dict(file=p["file"], px_per_u=round(p["px_per_u"] / float(np.exp(scales.get(k, (0, 0, 0))[0])), 3),
                        n=p["n"], scale=dict(center_factor=round(float(np.exp(scales.get(k, (0, 0, 0))[0])), 4),
                                             variation_x=round(scales.get(k, (0, 0, 0))[1], 4),
                                             variation_y=round(scales.get(k, (0, 0, 0))[2], 4)))
                for k, p in photos.items()},
        pieces=pieces))
    np.savez_compressed(os.path.join(out_dir, "pieces_raw.npz"),
                        **{f"{pid}__{k}": v for pid, d in raw.items() for k, v in d.items()})
    save_json(os.path.join(out_dir, "fusion.json"), report)
    if out_dir == DATA:
        contact_sheets(M, M["views"])          # every view, out-of-zone ones included (grey)

    # summary
    nv = np.array([len(r["views"]) for r in report])
    sd = [s for r in report for s in r["sides"]]
    multi = sum(1 for s in sd if len(s["kept"]) >= 2)
    ec = np.array([v["rms_fusion"] for v in views if v.get("id") and v["w"] > 0])
    print(f"pieces: {len(pieces)}; views per piece: {dict(zip(*[x.tolist() for x in np.unique(nv, return_counts=True)]))}")
    print(f"sides: {len(sd)}; averaged over ≥ 2 views: {multi}; single view: {len(sd) - multi}")
    print(f"view / fused piece deviation: median {np.median(ec):.2f} u, p90 {np.percentile(ec, 90):.2f} u")
    print("views: " + ", ".join(f"{m} {sum(1 for v in views if v['method'] == m)}"
                                for m in ["position", "duplicate_dropped"]) + f", out of zone (ignored) {n_out_of_zone}")
    print("alerts:", {f: sum(f in q["flags"] for q in pieces) for f in
                      ["single_view", "side_unconfirmed", "types_differ_between_views", "core_angle_out_of_tolerance",
                       "flat_side_detected"]})
    missing = [f"{s}{n:03d}" for s in "ABCD" for n in range(1, 101) if f"{s}{n:03d}" not in ref_of]
    missing += [f"E{n:03d}" for n in range(1, 13) if f"E{n:03d}" not in ref_of]
    print("pieces without any view:", missing)


def contact_sheets(M, views):
    out = os.path.join(REPORTS, "measurements")
    os.makedirs(out, exist_ok=True)
    by_photo = {}
    for v in views:
        by_photo.setdefault(v["photo"], []).append(v)
    for p in M["photos"]:
        img = load_photo(p["file"])
        z = np.load(os.path.join(MEAS, "measurements_raw.npz"))
        for v in by_photo.get(p["photo"], []):
            pts = z[f"{v['view']}__contour_px"]
            if not v.get("id"):
                col = (150, 150, 150)
            elif v["w"] == 0 or v.get("rms_fusion", 0) > 4.0:
                col = (0, 0, 230)
            elif v.get("rms_fusion", 0) > 2.5:
                col = (0, 140, 255)
            else:
                col = (0, 170, 0)
            cv2.polylines(img, [np.round(pts * 8).astype(np.int32).reshape(-1, 1, 2)], True, col, 5, cv2.LINE_AA, shift=3)
            lab = v.get("id") or "?"
            org = (int(v["cx"]) - 70, int(v["cy"]) + 10)
            for th, c in ((9, (255, 255, 255)), (4, col)):
                cv2.putText(img, lab, org, cv2.FONT_HERSHEY_SIMPLEX, 1.5, c, th)
            if v.get("id") and "rms_fusion" in v:
                t = f"{v['rms_fusion']:.1f}u"
                for th, c in ((8, (255, 255, 255)), (3, col)):
                    cv2.putText(img, t, (org[0], org[1] + 45), cv2.FONT_HERSHEY_SIMPLEX, 1.1, c, th)
        for rj in p["rejects"]:
            cv2.polylines(img, [np.array(rj["contour"], np.int32).reshape(-1, 1, 2)], True, (255, 0, 255), 8)
        cv2.imwrite(os.path.join(out, f"{p['photo']}.jpg"),
                    cv2.resize(img, None, fx=0.3, fy=0.3, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 80])


if __name__ == "__main__":
    main()
