"""Compatibility between sides (tab <-> blank) and estimate of the shadow bias.

Units: u (100 u = median long side of the core). Profiles: 128 points, side frame
(x along the corner->corner chord, y outwards of the piece).
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, load_json

# Outline bias of the catalog: the measured outlines are ≈ 0.85 u too wide on each edge (dark piece on
# light paper: blur, cardboard edge). Evidence independent of any match: in the catalog every tab has its blank, yet
# tab necks / heads measure 3.5 / 3.3 u wider than blank necks / heads (4 × 0.85). Board sockets: d_board (solve).
D_PIECE = 0.85
CURVE_ITERS = 5


def load_model(json_path=None, npz_path=None):
    """Unified catalog (data/pieces.json) by default; or another model (e.g. a catalog built in a temp dir)."""
    data = load_json(json_path or os.path.join(DATA, "pieces.json"))
    z = np.load(npz_path or os.path.join(DATA, "pieces_raw.npz"))
    pieces = [p for p in data["pieces"] if "sides" in p]
    prof = np.stack([z[f"{p['id']}__profiles"] for p in pieces])        # (P,4,128,2)
    return data, pieces, prof


def offset(prof, d):
    """Moves each profile d u towards the inside of the piece (d > 0: removes the halo)."""
    t = np.gradient(prof, axis=-2)
    t /= np.linalg.norm(t, axis=-1, keepdims=True) + 1e-9
    n = np.stack([-t[..., 1], t[..., 0]], -1)          # outward normal
    return prof - d * n


def mate_view(prof):
    """A side seen from the neighbouring piece: reversed direction, x -> L - x, y -> -y."""
    q = prof[..., ::-1, :].copy()
    L = prof[..., -1:, 0]
    q[..., 0] = L - q[..., 0]
    q[..., 1] = -q[..., 1]
    return q


def procrustes_rms(T, B):
    """Minimal RMS (rotation + translation) between T (N,2) and each B (m,N,2), paired points."""
    Tc = T - T.mean(0)
    Bc = B - B.mean(1, keepdims=True)
    a = np.einsum("nk,mnk->m", Tc, Bc)
    b = np.einsum("n,mn->m", Tc[:, 0], Bc[..., 1]) - np.einsum("n,mn->m", Tc[:, 1], Bc[..., 0])
    st = (Tc ** 2).sum()
    sb = (Bc ** 2).sum((1, 2))
    return np.sqrt(np.maximum(st + sb - 2 * np.sqrt(a ** 2 + b ** 2), 0) / T.shape[0])


def _rigid(A, B):
    """Batch rotation angle + translation taking A (m,n,2) onto B (m,n,2) or (n,2) (paired least squares)."""
    ma, mb = A.mean(-2, keepdims=True), B.mean(-2, keepdims=True)
    Ac, Bc = A - ma, B - mb
    a = (Ac * Bc).sum((-1, -2))
    b = (Ac[..., 0] * Bc[..., 1]).sum(-1) - (Ac[..., 1] * Bc[..., 0]).sum(-1)
    th = np.arctan2(b, a)
    c, s = np.cos(th), np.sin(th)
    R = np.stack([np.stack([c, -s], -1), np.stack([s, c], -1)], -2)
    return R, ma, mb


def curve_dist(S, X, iters=CURVE_ITERS, sub=2):
    """Distance between a socket S (N,2) and candidate sides X (m,N,2) as CURVES: paired Procrustes as a
    start, then rigid ICP on the nearest points of a densified S; result = symmetric mean nearest distance (u).
    Unlike procrustes_rms it does not count a slip of the points ALONG the outline (two outlines smoothed differently
    are parametrised differently by arc length): on the consistent cells, wrong pieces scoring as well as the true one
    per socket 4.7 -> 0.1."""
    Sd = np.concatenate([np.linspace(S[i], S[i + 1], 2, endpoint=False) for i in range(len(S) - 1)] + [S[-1:]])
    R, mx, ms = _rigid(X, S[None])
    A = np.einsum("mij,mnj->mni", R, X - mx) + ms
    for _ in range(iters):
        Ai = A[:, ::sub]
        nn = Sd[((Ai[:, :, None, :] - Sd[None, None]) ** 2).sum(-1).argmin(-1)]
        R, ma, mn = _rigid(Ai, nn)
        A = np.einsum("mij,mnj->mni", R, A - ma) + mn
    d2 = ((A[:, ::sub, None, :] - Sd[None, None]) ** 2).sum(-1)               # (m, N/sub, Nd)
    return 0.5 * (np.sqrt(d2.min(2)).mean(1) + np.sqrt(d2.min(1)).mean(1))


def side_table(pieces):
    """List of (piece index, side index, type, L)."""
    rows = []
    for i, p in enumerate(pieces):
        for j, s in enumerate(p["sides"]):
            rows.append((i, j, s["type"], s["L"]))
    return rows


def cost_matrix(pieces, prof, d=0.0, len_tol=0.10):
    """Cost (RMS in u) for each tab x blank of different pieces; inf if incompatible."""
    P = offset(prof, d)
    rows = side_table(pieces)
    tabs = [r for r in rows if r[2] == "T"]
    blanks = [r for r in rows if r[2] == "B"]
    Bm = np.stack([mate_view(P[i, j]) for i, j, _, _ in blanks])
    BL = np.array([r[3] for r in blanks])
    Bpi = np.array([r[0] for r in blanks])
    C = np.full((len(tabs), len(blanks)), np.inf)
    for ti, (i, j, _, L) in enumerate(tabs):
        ok = (Bpi != i) & (np.abs(BL - L) <= len_tol * L)
        if ok.any():
            C[ti, ok] = procrustes_rms(P[i, j], Bm[ok])
    return tabs, blanks, C


def summary(C):
    s = np.sort(C, 1)
    best, second = s[:, 0], s[:, 1]
    ratio = best / second
    bb = np.argmin(C, 0)
    bt = np.argmin(C, 1)
    mutual = np.array([bb[bt[t]] == t for t in range(len(bt))])
    return dict(best_med=float(np.median(best)), second_med=float(np.median(second)),
                r07=int((ratio < 0.7).sum()), r05=int((ratio < 0.5).sum()), mutual=int(mutual.sum()),
                mutual_r07=int((mutual & (ratio < 0.7)).sum()))


if __name__ == "__main__":
    data, pieces, prof = load_model()
    res = {}
    for d in [-1.0, 0.0, 0.5, 1.0, 1.5, 2.0, 3.0]:
        tabs, blanks, C = cost_matrix(pieces, prof, d)
        res[d] = summary(C)
        print(f"d={d:+.1f} u:", res[d], flush=True)
