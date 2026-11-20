"""Recalibration photo of the workshop, processed automatically on the server (docs/ATELIER.md L6).

    recalibrate.py <photo.jpg> <model dir> <work dir> <expected empty cells .json> [--no-scores]

Runs in its own process (launched by server/workshop.py): the memory of the photo is freed at the end, the workshop
keeps answering meanwhile. Progress and result in <work dir>/status.json:
  {"state": "running" | "ok" | "rejected" | "error", "stage": ..., "reason": code, "detail": {...}, "model": dir}
Checks, in order (a failed check rejects the photo; the page tells why):
  frame    the puzzle frame is found (photo turned automatically: the orientation whose empty cells agree with the
           journal wins)
  blur     edge width of the paper / piece boundary ≤ BLUR_MAX_U in both directions (rejects a shaken photo)
  cells    the empty cells on the photo = the cells expected empty from the journal (params.json empty_cells minus
           the placements recorded before the photo); the cells that differ are reported
  sockets  the sockets are not read worse than with the current model (median best score ≤ SOCKETS_MAX_RATIO × current)
On success, <work dir>/model/ is a complete model directory for live.LiveEngine (catalog files copied from the
current model, new board.json / board_sockets.npz, score cache for every condition, version.json).
"""
import os, sys, json, shutil, hashlib, time, traceback
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import board_model as bm

MAX_SIDE = 5000              # px: the photo is reduced to this (memory of the server; the board needs ≈ 1.3–2.5 px/u)
BLUR_MAX_U = 1.95            # u: reference photo 1.46 / 1.64, a shaken 64 Mpx photo 2.0
SOCKETS_MAX_RATIO = 1.15
MODEL_FILES = ["pieces.json", "pieces_raw.npz", "params.json"]


class Rejected(Exception):
    def __init__(self, reason, **detail):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


def status(work, **kw):
    p = os.path.join(work, "status.json")
    s = {}
    if os.path.exists(p):
        with open(p) as f:
            s = json.load(f)
    s.update(kw, updated=time.time())
    with open(p + ".tmp", "w") as f:
        json.dump(s, f)
    os.replace(p + ".tmp", p)


def load_reduced(path):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    k = MAX_SIDE / max(im.size)
    if k < 1:
        im = im.resize((round(im.size[0] * k), round(im.size[1] * k)), Image.LANCZOS)
    return cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR)


def edge_width(img, rimg, white, Hm):
    """Median 10–90 % edge width (u) of the paper / piece boundary, for board-horizontal and board-vertical edges,
    measured on the photo pixels along the normals of the empty-paper outline."""
    from scipy.ndimage import map_coordinates, gaussian_filter
    cnts, _ = cv2.findContours(white, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    P = np.vstack([x.reshape(-1, 2) for x in cnts if len(x) > 400]).astype(np.float64)[::5]
    sm = gaussian_filter(white.astype(np.float32), 3)
    gy, gx = np.gradient(sm)
    nx, ny = gx[P[:, 1].astype(int), P[:, 0].astype(int)], gy[P[:, 1].astype(int), P[:, 0].astype(int)]
    nn = np.hypot(nx, ny) + 1e-9
    nx, ny = nx / nn, ny / nn
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    ts = np.linspace(-8, 8, 65)
    pts = P[None] + (ts[:, None, None] * bm.PX_PER_U) * np.stack([nx, ny], 1)[None]
    flat = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), np.linalg.inv(Hm)).reshape(len(ts), -1, 2)
    prof = map_coordinates(g, [flat[..., 1], flat[..., 0]], order=1)
    contrast = np.percentile(prof[-12:], 50, axis=0) - np.percentile(prof[:12], 50, axis=0)
    slope = np.max(np.abs(np.diff(prof, axis=0)), axis=0) / (ts[1] - ts[0])
    width = 0.8 * np.abs(contrast) / np.maximum(slope, 1e-6)
    ok = np.abs(contrast) > 60
    hor, ver = ok & (np.abs(ny) > 0.85), ok & (np.abs(nx) > 0.85)
    if hor.sum() < 50 or ver.sum() < 50:
        return None
    return float(np.median(width[hor])), float(np.median(width[ver]))


def board_from_photo(img, expected, work):
    """Board model of the photo in the orientation that agrees with the journal. Returns (summary, rotation)."""
    try:
        corners, _ = bm.frame_corners(img)
    except Exception as e:                                    # noqa: BLE001 — any failure = frame not found
        raise Rejected("frame", error=repr(e))

    w = np.linalg.norm(corners[1] - corners[0]); h = np.linalg.norm(corners[3] - corners[0])
    turns = (0, 2) if h >= w else (1, 3)                      # the board stands (28 × 36 cells, portrait)
    best, errors = None, []
    for k in turns:
        im = np.ascontiguousarray(np.rot90(img, -k))          # k clockwise quarter turns
        out = os.path.join(work, f"board_rot{k}")
        os.makedirs(out, exist_ok=True)
        try:
            s = bm.main(photo=None, out=out, img=im, expected=expected)
        except Exception as e:                                # noqa: BLE001 — that orientation failed: try the other
            errors.append(repr(e))
            continue
        if best is None or len(s["disagreements"]) < len(best[0]["disagreements"]):
            best = (s, k, out, im)
    if best is None:
        raise Rejected("frame", errors=errors)
    return best


def best_scores(model_dir):
    """Median best score of the board sockets (main catalog, d_board 0.25), as board_audit."""
    import solve
    from match import load_model, offset, D_PIECE
    _, pieces, prof = load_model(os.path.join(model_dir, "pieces.json"), os.path.join(model_dir, "pieces_raw.npz"))
    b = json.load(open(os.path.join(model_dir, "board.json")))
    z = np.load(os.path.join(model_dir, "board_sockets.npz"))
    types = np.array([[s["type"] for s in p["sides"]] for p in pieces])
    Pp = offset(prof, D_PIECE)
    key = hashlib.sha1(Pp.tobytes()).hexdigest()
    out = []
    for (cid, k), pts in solve.board_sockets(b, z).items():
        sp, req = solve.local_profile(pts)
        if req != "F":
            out.append(float(solve.side_scores(offset(sp[None, None], -0.25)[0, 0], req, Pp, types, key)[types == req].min()))
    return float(np.median(out))


def version(model_dir):
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(model_dir)):
        for f in sorted(files):
            p = os.path.join(root, f)
            rel = os.path.relpath(p, model_dir)
            if rel in ("model/version.json", "model/score_cache.npz"):
                continue
            h.update(rel.encode())
            with open(p, "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()[:12]


def process(photo, model_dir, work, expected, placements=None, scores=True):
    """Whole processing; raises Rejected on a failed check. Returns the summary written to status.json."""
    os.makedirs(work, exist_ok=True)
    if os.path.exists(os.path.join(work, "status.json")):
        os.remove(os.path.join(work, "status.json"))
    status(work, state="running", stage="photo")
    img = load_reduced(photo)
    status(work, stage="board")
    s, k, out, im = board_from_photo(img, expected, work)
    status(work, stage="sharpness")
    geom = json.load(open(os.path.join(out, "board_geom.json")))
    white = cv2.imread(os.path.join(out, "board_empty_mask.png"), cv2.IMREAD_GRAYSCALE)
    ew = edge_width(im, s["rimg"], (white > 0).astype(np.uint8), np.array(geom["H"]))
    if ew is None or max(ew) > BLUR_MAX_U:
        raise Rejected("blur", edge_width_u=None if ew is None else [round(x, 2) for x in ew])
    if s["disagreements"]:
        dis = s["disagreements"]
        raise Rejected("cells", rotation=k,
                       empty_on_photo=sorted(d["cell"] for d in dis if d["expected"] == "filled"),
                       filled_on_photo=sorted(d["cell"] for d in dis if d["expected"] == "empty"))
    # the new model directory: catalog files of the current model + the new board
    status(work, stage="model")
    new = os.path.join(work, "model")
    if os.path.exists(new):
        shutil.rmtree(new)
    os.makedirs(os.path.join(new, "model"))
    for f in MODEL_FILES:
        shutil.copyfile(os.path.join(model_dir, f), os.path.join(new, f))
    cat = os.path.join(model_dir, "model", "catalogs")
    if os.path.isdir(cat):
        shutil.copytree(cat, os.path.join(new, "model", "catalogs"))
    for f in ("board.json", "board_sockets.npz"):
        shutil.copyfile(os.path.join(out, f), os.path.join(new, f))
    status(work, stage="sockets")
    import solve
    old = os.path.join(model_dir, "model", "score_cache.npz")
    if os.path.exists(old):
        solve.load_score_cache(old)                            # the current sockets: already scored
    cur, now = best_scores(model_dir), best_scores(new)
    if now > SOCKETS_MAX_RATIO * cur:
        raise Rejected("sockets", best_median_u=round(now, 3), current_u=round(cur, 3))
    v = version(new)
    with open(os.path.join(new, "model", "version.json"), "w") as f:
        json.dump(dict(version=v, recalibration_photo=os.path.basename(photo)), f)
    if scores:
        status(work, stage="scores")
        import live
        e = live.LiveEngine(new)
        e.apply(placements or dict(placed=[], excluded=[]))
        e.proposals()
        solve.save_score_cache(os.path.join(new, "model", "score_cache.npz"))
    shutil.rmtree(out, ignore_errors=True)
    for d in os.listdir(work):
        if d.startswith("board_rot"):
            shutil.rmtree(os.path.join(work, d), ignore_errors=True)
    return dict(rotation=k, n_empty=s["n_empty"], edge_width_u=[round(x, 2) for x in ew],
                best_median_u=round(now, 3), current_u=round(cur, 3), version=v)


def main(photo, model_dir, work, expected_path, scores=True):
    t = time.time()
    exp = json.load(open(expected_path))
    try:
        res = process(photo, model_dir, work, exp["expected"], exp.get("placements"), scores)
        status(work, state="ok", stage="done", detail=res, model=os.path.join(work, "model"), seconds=round(time.time() - t))
    except Rejected as r:
        status(work, state="rejected", stage="done", reason=r.reason, detail=r.detail, seconds=round(time.time() - t))
    except Exception as e:                                    # noqa: BLE001 — reported to the page and the log
        status(work, state="error", stage="done", reason="error", detail=dict(error=repr(e), trace=traceback.format_exc()[-2000:]))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2], a[3], scores="--no-scores" not in a)
