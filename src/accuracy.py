"""Real accuracy of the model and error budget → reports/accuracy.md + reports/accuracy.json.

v0 = internal evidence only (no real answer yet); re-run after each batch of real answers (real part added then).
  Side score = curve distance (match.curve_dist), not the paired rms.
  A. Impostors: for every board socket, score of every piece side of the required type. All but at most one are wrong
     pieces: their lower tail says how many wrong sides come as close as the true one (what one side can tell).
  B. Held-out side: in the cells with ≥ 3 known sides, the piece is chosen without one side, then measured on it.
     A cell is CONSISTENT (redundant) when the same piece and rotation is chosen whichever side is held out: its
     held-out rms estimates the TRUE piece ↔ board error. A cell whose sides point to 3 different pieces contradicts
     itself (wrong socket or weak measurement: a self-validation signal); with 2 pieces it is only not redundant.
  C. Piece ↔ piece: best partner of random tabs among the real blanks vs among impossible partners (pp_signal).
     The spread between views suggested piece ↔ piece 1.8× more precise than piece ↔ board; measured against
     impossible partners (other tabs turned inside out), the signal is weak.
  D. Reading.
Usage: sh scripts/pipeline.sh --script src/accuracy.py
"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, REPORTS, SIDE_NAMES, load_json, save_json
from match import load_model, offset, mate_view, procrustes_rms, curve_dist, side_table, D_PIECE
from solve import load_board, board_sockets, frontier_cells, score_cells

D_BOARD = 0.25                         # as the engine (solve.run(d_board=0.25)); D_PIECE: match.D_PIECE
THR = [0.75, 1.0, 1.5, 2.0, 2.5, 3.0]
REL = [1.0, 1.25, 1.5]                 # A: thresholds in units of the true piece ↔ board error (B)
PP_THR = [0.5, 0.6, 0.7, 0.8]          # C: thresholds (u) on the best partner of a tab


def pp_signal(pieces, prof, n_tabs=200, K=30, seed=0):
    """Piece ↔ piece signal: best partner of random tabs among the real blanks vs among impossible partners (other
    tabs turned inside out), curve distance on the K best paired candidates. The excess of the real blanks below a
    threshold = the share of tabs whose true mate stands out."""
    P = offset(prof, D_PIECE)
    rows = side_table(pieces)
    tabs = [r for r in rows if r[2] == "T"]
    blanks = [r for r in rows if r[2] == "B"]
    rng = np.random.default_rng(seed)
    sel = rng.choice(len(tabs), min(n_tabs, len(tabs)), replace=False)

    def best(group, flip):
        Bm = np.stack([mate_view(P[i, j] * ([1, -1] if flip else [1, 1])) for i, j, _, _ in group])
        L = np.array([r[3] for r in group]); pi = np.array([r[0] for r in group])
        out = []
        for t in sel:
            i, j, _, Lt = tabs[t]
            ok = np.where((pi != i) & (np.abs(L - Lt) <= 0.1 * Lt))[0]
            top = ok[np.argsort(procrustes_rms(P[i, j], Bm[ok]))[:K]]
            out.append(float(curve_dist(P[i, j], Bm[top]).min()))
        return np.array(out)
    real, null = best(blanks, False), best(tabs, True)
    pct = lambda v, t: round(float(100 * (v <= t).mean()), 1)
    return dict(n_tabs=len(sel), candidates=K, real_best_p50=round(float(np.median(real)), 2),
                null_best_p50=round(float(np.median(null)), 2),
                real_below={f"{t}": pct(real, t) for t in PP_THR}, null_below={f"{t}": pct(null, t) for t in PP_THR})


def q(v, qs=(0.5, 0.75, 0.9)):
    v = np.asarray(v, float)
    return {f"p{int(100 * x)}": round(float(np.quantile(v, x)), 2) for x in qs} if len(v) else {}


def rms(v):
    v = np.asarray(v, float)
    return round(float(np.sqrt(np.mean(v ** 2))), 2) if len(v) else None


def faces_frame(b, C, cid, k):
    dr, dc = [(-1, 0), (0, 1), (1, 0), (0, -1)][k]
    r2, c2 = C[cid]["r"] + dr, C[cid]["c"] + dc
    return r2 in (0, b["rows"] - 1) or c2 in (0, b["cols"] - 1)


def main():
    data, pieces, prof = load_model()
    b, z, pl = load_board()
    Cb = {c["id"]: c for c in b["cells"]}
    cells = frontier_cells(b, board_sockets(b, z), set())
    scored = score_cells(pieces, prof, cells, D_PIECE, D_BOARD)
    types = np.array([[s["type"] for s in p["sides"]] for p in pieces])
    sigma_engine = load_json(os.path.join(DATA, "evaluation.json"))["fusion of all photos"]["sigma_u"]
    out = dict(version="v0 (internal evidence only, no real answer)", sigma_engine_u=sigma_engine)

    def type_mask(k, req):
        m = np.zeros((len(pieces), 4), bool)
        for qq in range(4):
            m[:, qq] = types[:, (k - qq) % 4] == req
        return m

    # ------------------------------------------------------------ A. impostors on every socket
    imp_all, best_all = [], []
    for cell, s in zip(cells, scored):
        for n, (k, req, _) in enumerate(cell["known"]):
            v = np.sort(s["per_side"][:, :, n][type_mask(k, req)])
            best_all.append(v[0])
            imp_all.append(v[1:])                    # at most one of them is the true piece
    imp = np.concatenate(imp_all)
    n_cand = float(np.median([len(v) + 1 for v in imp_all]))
    out["A_impostors"] = dict(n_sockets=len(best_all), candidates_per_socket=n_cand,
                              share_below={f"{t}": float((imp <= t).mean()) for t in THR},
                              per_socket_below={f"{t}": round(float((imp <= t).mean() * n_cand), 2) for t in THR},
                              best=q(best_all))

    # ------------------------------------------------------------ B. held-out side
    rows = []
    for cell, s in zip(cells, scored):
        nk = len(cell["known"])
        if nk < 2:
            continue
        ps = s["per_side"]
        full = (ps ** 2).sum(2)
        full[~s["alive"]] = np.inf
        fi = np.unravel_index(np.argmin(full), full.shape)
        for n, (k, req, _) in enumerate(cell["known"]):
            cost = (ps[:, :, [m for m in range(nk) if m != n]] ** 2).sum(2)
            cost[~s["alive"]] = np.inf
            o = np.argsort(cost, axis=None)
            i, qq = np.unravel_index(o[0], cost.shape)
            i2, q2 = np.unravel_index(o[1], cost.shape)
            held = float(ps[i, qq, n])
            vals = ps[:, :, n][type_mask(k, req)]
            rows.append(dict(cell=cell["id"], side=SIDE_NAMES[k], n_known=nk, piece=pieces[i]["id"], q=int(qq),
                             held_rms=round(held, 3), rank_on_side=int((vals < held).sum()),
                             margin_rms=round(float(np.sqrt(cost[i2, q2]) - np.sqrt(cost[i, qq])), 3),
                             same_as_full=bool((i, qq) == fi), frame=faces_frame(b, Cb, cell["id"], k)))
    by_cell = {}
    for r in rows:
        by_cell.setdefault(r["cell"], []).append(r)
    multi = {c: v for c, v in by_cell.items() if v[0]["n_known"] >= 3}
    consistent = {c: v for c, v in multi.items() if len({(r["piece"], r["q"]) for r in v}) == 1}
    inconsistent = sorted(set(multi) - set(consistent))
    held = np.array([r["held_rms"] for v in consistent.values() for r in v])
    held_frame = [r["held_rms"] for v in consistent.values() for r in v if r["frame"]]
    held_inner = [r["held_rms"] for v in consistent.values() for r in v if not r["frame"]]
    two = [r for r in rows if r["n_known"] == 2]
    out["B_held_out"] = dict(
        cells_3plus=len(multi), consistent_cells={c: v[0]["piece"] for c, v in sorted(consistent.items())},
        inconsistent_cells={c: sorted({r["piece"] for r in multi[c]}) for c in inconsistent},
        true_piece_board=dict(n_sides=int(len(held)), held_rms=q(held), rms_u=rms(held),
                              within_1_5_sigma=round(float((held <= 1.5 * sigma_engine).mean()), 3),
                              facing_frame=dict(n=len(held_frame), rms_u=rms(held_frame)),
                              facing_inner=dict(n=len(held_inner), rms_u=rms(held_inner))),
        choice_on_1_side=dict(n=len(two), held_rms=q([r["held_rms"] for r in two]),
                              same_as_with_both_sides=round(float(np.mean([r["same_as_full"] for r in two])), 3)),
        rows=rows)

    # A, in units of the true error found by B
    t_true = float(np.sqrt(np.mean(held ** 2)))
    out["A_impostors"]["per_socket_below_true_error"] = {
        f"{m}": round(float((imp <= m * t_true).mean() * n_cand), 2) for m in REL}

    # ------------------------------------------------------------ C. piece ↔ piece
    pp = pp_signal(pieces, prof)
    out["C_piece_piece"] = pp

    # ------------------------------------------------------------ D. summary
    out["D_budget"] = dict(sigma_piece_board_u=round(t_true, 2), piece_piece=pp)
    save_json(os.path.join(REPORTS, "accuracy.json"), out)

    # ------------------------------------------------------------ report
    A, B = out["A_impostors"], out["B_held_out"]
    T = B["true_piece_board"]
    At = A["per_socket_below_true_error"]
    tb = max(PP_THR, key=lambda t: pp["real_below"][str(t)] - pp["null_below"][str(t)])   # most telling threshold
    rep = ["# Accuracy of the model — error budget (v0)", "",
           "Generated by `src/accuracy.py`. **v0: internal evidence only** (no real answer recorded yet). Re-run after "
           "each batch of real answers: the real answers will replace B as the reference.", "",
           f"Engine σ (median of the best match, 1-side cells): **{sigma_engine} u** — a mix of true and wrong matches.", "",
           "## A. Impostors (wrong pieces) on the board sockets", "",
           f"- {A['n_sockets']} sockets, ≈ {A['candidates_per_socket']:.0f} candidate sides of the required type per socket.",
           "- Wrong sides per socket scoring within x × the true error (B): " + ", ".join(
               f"≤ {m}×: **{At[str(m)]}**" for m in REL),
           f"- Best match per socket: {A['best']}", "",
           "## B. Held-out side — true piece ↔ board error", "",
           f"- Cells with ≥ 3 known sides: {B['cells_3plus']}; consistent (same piece whichever side is held out): "
           f"{len(B['consistent_cells'])} — " + ", ".join(f"{c} {p}" for c, p in B["consistent_cells"].items()),
           "- Sides contradicting each other (3 different pieces): " + (", ".join(
               f"{c} ({' / '.join(v)})" for c, v in B["inconsistent_cells"].items() if len(v) >= 3) or "none")
           + "; not redundant (2 pieces, the choice needs every side): " + (", ".join(
               f"{c} ({' / '.join(v)})" for c, v in B["inconsistent_cells"].items() if len(v) == 2) or "none"),
           f"- Held-out side of the consistent cells ({T['n_sides']} sides): {T['held_rms']}, **rms {T['rms_u']} u**; "
           f"within 1.5 σ: {100 * T['within_1_5_sigma']:.0f} %; facing the frame rms {T['facing_frame']['rms_u']} u "
           f"(n={T['facing_frame']['n']}), facing an inner piece rms {T['facing_inner']['rms_u']} u (n={T['facing_inner']['n']}).",
           f"- Cells with 2 known sides, piece chosen on the other one ({B['choice_on_1_side']['n']} sides): held-out "
           f"{B['choice_on_1_side']['held_rms']}; same choice as with both sides only "
           f"{100 * B['choice_on_1_side']['same_as_with_both_sides']:.0f} % → one side does not identify a piece.", "",
           "## C. Piece ↔ piece (catalog only, no board)", "",
           f"- {pp['n_tabs']} tabs drawn at random; best partner (curve distance, {pp['candidates']} best paired "
           "candidates re-scored) among the real blanks, and among IMPOSSIBLE partners (other tabs turned inside out: "
           "never a true mate, same kind of shapes).",
           "- Best partner below x u — real blanks: " + ", ".join(f"≤ {t}: **{pp['real_below'][str(t)]} %**" for t in PP_THR)
           + "; impossible partners: " + ", ".join(f"≤ {t}: {pp['null_below'][str(t)]} %" for t in PP_THR),
           f"- Median best: real {pp['real_best_p50']} u, impossible {pp['null_best_p50']} u.", "",
           "## D. Reading", "",
           f"- Side score = curve distance (u, `match.curve_dist`); true piece ↔ board ≈ **{T['rms_u']} u** (B).",
           f"- One board side: about {At['1.0']} wrong sides per socket score as well as the true piece ({At['1.25']} "
           f"within 1.25×). Two sides: ≈ {At['1.0'] ** 2 / A['candidates_per_socket']:.2f} (independence assumed).",
           f"- Piece ↔ piece is much weaker than piece ↔ board: at best (≤ {tb} u) a real partner for "
           f"{pp['real_below'][str(tb)]} % of the tabs, an impossible one for {pp['null_below'][str(tb)]} % — the true "
           f"mate stands out for about {pp['real_below'][str(tb)] - pp['null_below'][str(tb)]:.0f} % of the tabs, while "
           f"a board side leaves ≈ {At['1.0']} wrong sides per socket. A placed piece gives its neighbours far less than "
           "a socket measured on the board: the lever is the recalibration photo (the placed pieces become board "
           "sockets); piece ↔ piece is a complement.", ""]
    with open(os.path.join(REPORTS, "accuracy.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(rep) + "\n")
    print("\n".join(rep))


if __name__ == "__main__":
    main()
