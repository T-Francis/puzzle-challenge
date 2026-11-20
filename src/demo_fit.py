"""Constrained drawing of the demo: one global least-squares adjustment of every drawn piece.

Unknowns: position (tx, ty) and tilt θ of each piece; a piece of canonical rotation q is drawn at x = t + R(θ)·rot_q(p).
Residuals, sampled on NS points per side:
  - board (weight W_BOARD): a side facing a socket of the real board lies on that socket — anchors the drawing to the
    photo in the background;
  - pair (weight 1): two neighbouring drawn pieces share their common side (side of one = reversed side of the other);
  - centre: weak pull of the piece towards the centre of its cell; tilt: weak pull towards 0.
Tilt: up to ±MAX_TILT. Measured, the best tilt of a right piece in its hole is sharp and reaches 5°
(D051: 1.9 u at −4°, 4.8 u at 0°) while its neighbours sit at 0°: the catalog frame of a piece can be a few degrees
off, so a tight bound would shift right pieces off their holes.
Robust loss (soft L1): a wrong piece does not drag its neighbours. Fit of a piece = MEDIAN of the rms of its sides
(board + pairs): one wrong neighbour does not condemn a right piece; a piece that fits none of its neighbours stands out.
"""
import os, sys
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import coo_matrix
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fit_view import resample, piece_side_points, rot_q, fit_piece_pts

NS = 24                 # points per side
MAX_TILT = 8.0          # degrees
TILT_SCALE = 6.0        # degrees: weak pull of the tilt towards 0
CENTRE_SCALE = 15.0     # u: weak pull to the cell centre
W_BOARD = 2.0           # weight of a side on a real board socket, relative to a pair of drawn pieces
LOSS_SCALE = 1.5        # u: residuals beyond are down-weighted (soft L1)
NB = [(-1, 0), (0, 1), (1, 0), (0, -1)]


def _R(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s], [s, c]])


def _dR(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[-s, -c], [c, -s]])


def side_local(raw_p, q, j):
    """Side j of a piece rotated by q quarter turns, NS points, clockwise around the piece."""
    return resample(rot_q(piece_side_points(raw_p, j), q), NS)


def initial(raw_p, q, known, centre, max_shift=20.0):
    """Start: rigid fit on the known board sockets, otherwise the cell centre."""
    if known:
        _, R, ma, mb, _ = fit_piece_pts(raw_p, q, known)
        th = float(np.arctan2(R[1, 0], R[0, 0]))
        t = mb - R @ ma
        if abs(np.degrees(th)) <= MAX_TILT and np.linalg.norm(t - centre) <= max_shift:
            return t, th
    return np.array(centre, float), 0.0


def adjust(items, raw, board_sockets, cells, verbose=True):
    """items: [(cell, piece, q)]; board_sockets: {(cell, k): points}; cells: {cell: dict(r, c, center_u)}.
    Returns ({cell: dict(x, y, angle_deg (θ + 90 q), tilt_deg, fit_u, start_fit_u, n_sides)}, summary)."""
    n = len(items)
    idx = {cell: i for i, (cell, _, _) in enumerate(items)}
    pos = {(cells[c]["r"], cells[c]["c"]): c for c, _, _ in items}
    blocks = []        # (kind, weight, i, A local (NS,2), i2 or None, B: target points or local side of i2)
    for i, (cell, pid, q) in enumerate(items):
        r, c = cells[cell]["r"], cells[cell]["c"]
        for k in range(4):
            j = (k - q) % 4
            if (cell, k) in board_sockets:
                blocks.append(("board", W_BOARD, i, side_local(raw[pid], q, j), None, resample(board_sockets[(cell, k)], NS)))
            elif k in (1, 2):                      # pairs counted once: towards E and S
                nb = pos.get((r + NB[k][0], c + NB[k][1]))
                if nb is not None:
                    i2 = idx[nb]
                    _, pid2, q2 = items[i2]
                    j2 = ((k + 2) - q2) % 4
                    blocks.append(("pair", 1.0, i, side_local(raw[pid], q, j), i2, side_local(raw[pid2], q2, j2)[::-1]))
    nb_ = len(blocks)
    centres = np.array([cells[c]["center_u"] for c, _, _ in items], float)
    x0 = np.zeros(3 * n)
    for i, (cell, pid, q) in enumerate(items):
        known = {k: board_sockets[(cell, k)] for k in range(4) if (cell, k) in board_sockets}
        t, th = initial(raw[pid], q, known, centres[i])
        x0[3 * i:3 * i + 3] = [t[0], t[1], th]
    nres = nb_ * NS * 2 + 3 * n
    tscale = np.radians(TILT_SCALE)

    def raw_block(x, b):
        kind, w, i, A, i2, B = blocks[b]
        P = A @ _R(x[3 * i + 2]).T + x[3 * i:3 * i + 2]
        return P - (B if kind == "board" else B @ _R(x[3 * i2 + 2]).T + x[3 * i2:3 * i2 + 2])

    def residuals(x):
        out = np.empty(nres)
        for b in range(nb_):
            out[b * 2 * NS:(b + 1) * 2 * NS] = blocks[b][1] * raw_block(x, b).ravel()
        o = nb_ * 2 * NS
        X = x.reshape(n, 3)
        out[o:o + 2 * n] = ((X[:, :2] - centres) / CENTRE_SCALE).ravel()
        out[o + 2 * n:] = X[:, 2] / tscale
        return out

    # sparse jacobian: fixed structure (COO), θ columns recomputed
    R_, C_, V_, slots = [], [], [], []
    s = 0
    for b, (kind, w, i, A, i2, B) in enumerate(blocks):
        rows = b * 2 * NS + np.arange(2 * NS)
        for ii, sgn, M in ((i, w, A), (i2, -w, B)) if kind == "pair" else ((i, w, A),):
            R_ += [rows[0::2], rows[1::2], rows]
            C_ += [np.full(NS, 3 * ii), np.full(NS, 3 * ii + 1), np.full(2 * NS, 3 * ii + 2)]
            V_ += [np.full(NS, sgn), np.full(NS, sgn), np.zeros(2 * NS)]
            slots.append((slice(s + 2 * NS, s + 4 * NS), M, sgn, ii))
            s += 4 * NS
    o = nb_ * 2 * NS
    R_ += [o + 2 * np.arange(n), o + 2 * np.arange(n) + 1, o + 2 * n + np.arange(n)]
    C_ += [3 * np.arange(n), 3 * np.arange(n) + 1, 3 * np.arange(n) + 2]
    V_ += [np.full(n, 1 / CENTRE_SCALE), np.full(n, 1 / CENTRE_SCALE), np.full(n, 1 / tscale)]
    JR, JC, JV0 = np.concatenate(R_), np.concatenate(C_), np.concatenate(V_)

    def jac(x):
        V = JV0.copy()
        for sl, M, sgn, ii in slots:
            V[sl] = sgn * (M @ _dR(x[3 * ii + 2]).T).ravel()
        return coo_matrix((V, (JR, JC)), shape=(nres, 3 * n)).tocsr()

    lim = np.radians(MAX_TILT)
    lo, hi = np.tile([-np.inf, -np.inf, -lim], n), np.tile([np.inf, np.inf, lim], n)
    x0[2::3] = np.clip(x0[2::3], -lim * 0.999, lim * 0.999)
    sol = least_squares(residuals, x0, jac=jac, bounds=(lo, hi), loss="soft_l1", f_scale=LOSS_SCALE,
                        x_scale=np.tile([1.0, 1.0, 0.02], n), max_nfev=300)

    def block_rms(x):
        return np.array([np.sqrt((raw_block(x, b) ** 2).sum(1).mean()) for b in range(nb_)])

    def per_piece(e):
        per = [[] for _ in range(n)]
        for b, (_, _, i, _, i2, _) in enumerate(blocks):
            per[i].append(e[b])
            if i2 is not None:
                per[i2].append(e[b])
        return per
    eb, ea = block_rms(x0), block_rms(sol.x)
    perb, pera = per_piece(eb), per_piece(ea)
    out = {}
    for i, (cell, pid, q) in enumerate(items):
        tx, ty, th = sol.x[3 * i:3 * i + 3]
        out[cell] = dict(x=float(tx), y=float(ty), angle_deg=float(np.degrees(th) + 90 * q), tilt_deg=float(np.degrees(th)),
                         fit_u=float(np.median(pera[i])) if pera[i] else None,
                         start_fit_u=float(np.median(perb[i])) if perb[i] else None, n_sides=len(pera[i]))
    kinds = np.array([bl[0] for bl in blocks])
    summ = dict(n_pieces=n, n_board_sides=int((kinds == "board").sum()), n_pair_sides=int((kinds == "pair").sum()),
                status=int(sol.status), nfev=int(sol.nfev))
    for name, e in (("start", eb), ("after", ea)):
        for kind in ("board", "pair"):
            v = e[kinds == kind]
            summ[f"{name}_{kind}_u"] = dict(median=round(float(np.median(v)), 2), p90=round(float(np.quantile(v, .9)), 2),
                                            within_2_5u=int((v <= 2.5).sum()), n=int(len(v)))
    tilts = np.abs(np.degrees(sol.x[2::3]))
    summ["tilt_deg"] = dict(median=round(float(np.median(tilts)), 2), p90=round(float(np.quantile(tilts, .9)), 2),
                            at_bound=int((tilts > MAX_TILT - 0.01).sum()))
    if verbose:
        print("demo fit:", summ, flush=True)
    return out, summ
