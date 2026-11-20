"""Virtual fitting: the candidate piece is aligned on the known sockets and drawn on the board photo."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, BOARD_DIR, REPORTS, load_json, load_raw

N = 128


def resample(P, n=N):
    d = np.r_[0, np.cumsum(np.linalg.norm(np.diff(P, axis=0), axis=1))]
    t = np.linspace(0, d[-1], n)
    return np.stack([np.interp(t, d, P[:, 0]), np.interp(t, d, P[:, 1])], 1)


def piece_side_points(r, j):
    P, C = r["contour_u"], r["corners_u"]
    idx = [int(np.argmin(np.linalg.norm(P - c, axis=1))) for c in C]
    a, b = idx[j], idx[(j + 1) % 4]
    n = len(P)
    seg = P[np.arange(a, a + ((b - a) % n) + 1) % n]
    return np.vstack([C[j], seg, C[(j + 1) % 4]])


def rot_q(P, q):
    """q clockwise quarter turns on screen (y down): (x, y) -> (-y, x)."""
    for _ in range(q % 4):
        P = np.stack([-P[:, 1], P[:, 0]], 1)
    return P


def fit_piece_pts(r, q, known):
    """known = {board side k: socket points}. Rigid alignment of the piece (rotation q) on these sockets."""
    src, dst = [], []
    for k, pts in known.items():
        j = (k - q) % 4
        src.append(resample(rot_q(piece_side_points(r, j), q)))
        dst.append(resample(pts))
    A, B = np.vstack(src), np.vstack(dst)
    ma, mb = A.mean(0), B.mean(0)
    H = (A - ma).T @ (B - mb)
    U, _, Vt = np.linalg.svd(H)
    D = np.diag([1, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    full = (R @ (rot_q(r["contour_u"], q) - ma).T).T + mb
    res = (R @ (A - ma).T).T + mb - B
    return full, R, ma, mb, float(np.sqrt((res ** 2).sum(1).mean()))


def fit_piece(r, q, cell, sockets):
    known = {}
    for k, s in enumerate(cell["sides"]):
        key = f"{cell['id']}__{s['name']}__points"
        if s.get("extracted") and key in sockets.files:
            known[k] = sockets[key]
    full, _, _, _, rms = fit_piece_pts(r, q, known)
    return full, rms


def main(cell_ids, name, ncand=2):
    import cv2
    b = load_json(os.path.join(DATA, "board.json"))
    props = {p["cell"]: p for p in load_json(os.path.join(DATA, "proposals.json"))["proposals"]}
    geom = load_json(os.path.join(DATA, "board_geom.json"))
    ppu = geom["px_per_u"]
    im = cv2.imread(os.path.join(BOARD_DIR, "board_rectified.jpg"))
    sockets = np.load(os.path.join(DATA, "board_sockets.npz"))
    raw = load_raw()
    C = {c["id"]: c for c in b["cells"]}
    cols = [(0, 200, 0), (0, 140, 255), (255, 0, 200)]
    tiles = []
    for cid in cell_ids:
        cell = C[cid]
        cx, cy = cell["center_u"]
        half = 115
        x0, y0 = int((cx - half) * ppu), int((cy - half) * ppu)
        t = np.full((int(2 * half * ppu), int(2 * half * ppu), 3), 255, np.uint8)
        sx0, sy0 = max(0, x0), max(0, y0)
        part = im[sy0:y0 + t.shape[0], sx0:x0 + t.shape[1]]
        t[sy0 - y0:sy0 - y0 + part.shape[0], sx0 - x0:sx0 - x0 + part.shape[1]] = part
        lines = [cid]
        for n, cand in enumerate(props[cid]["candidates"][:ncand]):
            full, rms = fit_piece(raw[cand["piece"]], cand["rotation"] // 90, cell, sockets)
            P = ((full - [cx - half, cy - half]) * ppu * 8).astype(np.int32)
            cv2.polylines(t, [P.reshape(-1, 1, 2)], True, cols[n], 5 if n == 0 else 3, cv2.LINE_AA, 3)
            lines.append(f"{cand['piece']} {cand['rotation']}d {cand['pct_cell']:.0f}%")
        tt = cv2.resize(t, (320, 320))
        cv2.rectangle(tt, (0, 0), (320, 22 * len(lines) + 6), (255, 255, 255), -1)
        for i, s in enumerate(lines):
            cv2.putText(tt, s, (6, 20 + 22 * i), 0, 0.6, cols[i - 1] if i else (0, 0, 0), 2)
        tiles.append(tt)
    while len(tiles) % 6:
        tiles.append(np.full_like(tiles[0], 255))
    rows = [np.hstack(tiles[i:i + 6]) for i in range(0, len(tiles), 6)]
    out = os.path.join(REPORTS, name)
    cv2.imwrite(out, np.vstack(rows))
    print(out)


if __name__ == "__main__":
    props = load_json(os.path.join(DATA, "proposals.json"))["proposals"]
    if sys.argv[1] == "multi":
        ids = [p["cell"] for p in sorted(props, key=lambda p: -(p["candidates"][0]["pct_cell"] if p["candidates"] else 0))
               if p["n_known"] >= int(sys.argv[2]) and p["candidates"]]
    else:
        ids = sys.argv[1].split(",")
    main(ids, sys.argv[-1])
