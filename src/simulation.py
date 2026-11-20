"""Simulation of the reconstruction, piece by piece, with self-correction (see docs/SIMULATION.md).

Starts from the real state (board + confirmed placements of data/placements.json, READ ONLY) and goes to the end:
  1. placement: the safest piece; its free sides become known sides for its neighbours; dead end (a cell has no
     acceptable candidate left) → backtrack on the least safe neighbouring placement;
  2. guaranteed end: when no placement is acceptable any more, "forced" placement of the least bad one
     (incompatible side types tolerated with a strong penalty);
  3. final self-correction (relaxation): swaps of pieces and changes of rotation as long as the global fit
     (sum of squared deviations over all edges) decreases.
Output: data/simulation/simulation.json (replayable timeline). NEVER writes to data/placements.json.
"""
import os, sys, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, save_json, load_raw
from match import load_model
from solve import (load_board, effective_sockets, local_profile, score_cells, sinkhorn, placed_socket, LYING_PENALTY, NB,
                   QUALITY_GOOD, DEAD_END)
from fit_view import fit_piece_pts, piece_side_points, rot_q
from wave import plate_turns

OUT = os.path.join(DATA, "simulation")
QUALITY_OK = QUALITY_GOOD  # mean deviation per side (in σ) of a "good" placement (solve.py)
# DEAD_END (solve.py): beyond this (in σ) for the best candidate, the cell is a dead end
MAX_REMOVALS = 300         # backtracking budget
TYPE_PENALTY = 25.0        # forced placement: penalty (in σ²) per side of incompatible type (tab against tab…)
RELAX_K = 15               # relaxation: candidate pieces examined per badly fitting cell
RELAX_MAX = 3000           # relaxation: maximum number of swaps / rotations
STRATEGY = "confidence"    # "constraint": most constrained cell first; "confidence": safest first


class Simulation:
    def __init__(self, sigma=None):
        self.data, self.pieces_all, self.prof_all = load_model()
        self.P = {p["id"]: p for p in self.pieces_all}
        self.b, z, self.placements = load_board()
        self.raw = load_raw()
        sockets, filled, fitted = effective_sockets(self.b, z, self.placements)
        self.base = dict(sockets)                      # known sides of the real state
        self.sockets = dict(sockets)
        self.src = {}                                  # (cell, k) -> simulated cell that created this side
        self.C = {c["id"]: c for c in self.b["cells"]}
        self.pos = {(c["r"], c["c"]): c["id"] for c in self.b["cells"]}
        self.real = set(filled)
        self.empty = {c["id"] for c in self.b["cells"] if c["empty"] and c["id"] not in filled}
        placed = {p["piece"] for p in self.placements["placed"]}
        self.idx = [i for i, p in enumerate(self.pieces_all) if p["id"] not in placed]
        self.pieces = [self.pieces_all[i] for i in self.idx]
        self.prof = self.prof_all[self.idx]
        self.alive = np.ones(len(self.pieces), bool)  # pieces still available
        self.excl = set(self.placements.get("excluded", []))
        self.ban = set()                               # (cell, i, q) forbidden by the self-correction
        self.sim = {}                                  # cell -> simulated placement
        self.cache = {}                                # cell -> score (cost P×4 + known sides)
        aspect = np.array([p["core_w"] / p["core_h"] for p in self.pieces])
        self.pen = np.zeros((len(self.pieces), 4))
        self.pen[:, [1, 3]] = np.where(aspect > 0.95, 0.0, LYING_PENALTY)[:, None]
        self.steps, self.n_removals, self.n_forced, self.force = [], 0, 0, False
        self.sigma = sigma or self._sigma0()

    # ------------------------------------------------------------ known sides and scores
    def known(self, cid):
        out = []
        for k in range(4):
            if (cid, k) in self.sockets:
                prof, req = local_profile(self.sockets[(cid, k)])
                if req != "F":
                    out.append((k, req, prof))
        return out

    def score(self, cid):
        if cid not in self.cache:
            kn = self.known(cid)
            if not kn:
                self.cache[cid] = None
            else:
                s = score_cells(self.pieces, self.prof, [dict(id=cid, known=kn)], d_board=0.25)[0]
                s["n"] = len(kn)
                self.cache[cid] = s
        return self.cache[cid]

    def _sigma0(self):
        best = []
        for cid in self.empty:
            s = self.score(cid)
            if s and s["n"] == 1 and np.isfinite(s["cost"]).any():
                best.append(np.sqrt(np.min(s["cost"])))
        return float(np.median(best))

    def relaxed(self, s):
        """Cost without elimination by types: incompatible sides penalised (forced placement, relaxation)."""
        return (s["per_side"] ** 2).sum(-1) + (~s["alive"]) * TYPE_PENALTY * self.sigma ** 2

    def loglik(self, cid):
        s = self.score(cid)
        cost = self.relaxed(s) if self.force else s["cost"]
        L = -cost / (2 * self.sigma ** 2) - self.pen
        L[~self.alive] = -np.inf
        for (c, i, q) in self.ban:
            if c == cid:
                L[i, q] = -np.inf
        for i, p in enumerate(self.pieces):
            if f"{cid}:{p['id']}" in self.excl:
                L[i] = -np.inf
        return L

    def frontier(self):
        return sorted(c for c in self.empty if c not in self.sim and self.score(c) is not None)

    # ------------------------------------------------------------ choice of the next placement
    def best_moves(self):
        F = self.frontier()
        if not F:
            return []
        Ls = [self.loglik(c) for c in F]
        LL = np.stack([L.max(1) for L in Ls], 1)[self.alive]            # available pieces × frontier
        ok = np.isfinite(LL)
        m = LL[ok].max() if ok.any() else 0.0
        A = np.exp(np.where(ok, LL - m, -np.inf))
        n_left = int(self.alive.sum())
        elsewhere = np.array([np.median(A[i][ok[i]]) if ok[i].any() else 1e-12 for i in range(len(A))])
        cap = np.r_[np.ones(len(F)), max(1.0, n_left - len(F))]
        S = sinkhorn(np.concatenate([A, elsewhere[:, None]], 1), cap)
        alive_idx = np.flatnonzero(self.alive)
        moves = []
        for f, (cid, L) in enumerate(zip(F, Ls)):
            flat = L.ravel()
            fin = np.isfinite(flat)
            if not fin.any():
                moves.append(dict(cell=cid, dead_end=True, n=self.score(cid)["n"])); continue
            w = np.exp(flat[fin] - flat[fin].max())
            pc = np.zeros_like(flat); pc[fin] = w / w.sum()
            order = np.argsort(-pc)
            i, q = divmod(int(order[0]), 4)
            rms = self.score(cid)["per_side"][i, q]
            g = float(S[np.searchsorted(alive_idx, i), f])
            share = float(pc[order[0]] / max(pc[4 * i:4 * i + 4].sum(), 1e-12))
            alts = []
            for o in order[1:4]:
                if pc[o] <= 0:
                    break
                ia, qa = divmod(int(o), 4)
                alts.append(dict(piece=self.pieces[ia]["id"], rotation=qa * 90, pct_cell=round(100 * float(pc[o]), 1)))
            moves.append(dict(cell=cid, i=i, q=q, pct_cell=float(pc[order[0]]), pct_global=g, confidence=g * share,
                              rms=rms.tolist(), rms_sigma=float(np.mean(rms)) / self.sigma, n=self.score(cid)["n"],
                              dead_end=float(np.mean(rms)) / self.sigma > DEAD_END, alternatives=alts))
        return moves

    # ------------------------------------------------------------ place / remove
    def neighbours(self, cid):
        r, c = self.C[cid]["r"], self.C[cid]["c"]
        for k, (dr, dc) in enumerate(NB):
            nid = self.pos.get((r + dr, c + dc))
            if nid is not None:
                yield k, nid

    def place(self, mv, reason):
        cid, i, q = mv["cell"], mv["i"], mv["q"]
        pid = self.pieces[i]["id"]
        kn = {k: self.sockets[(cid, k)] for k in range(4) if (cid, k) in self.sockets}
        full, R, ma, mb, rms_fit = fit_piece_pts(self.raw[pid], q, kn)
        self.sim[cid] = dict(i=i, q=q, piece=pid, confidence=mv["confidence"], contour=full, R=R, ma=ma, mb=mb)
        self.alive[i] = False
        for k, nid in self.neighbours(cid):
            if nid in self.empty and nid not in self.sim:
                j = (k - q) % 4
                side = (R @ (rot_q(piece_side_points(self.raw[pid], j), q) - ma).T).T + mb
                self.sockets[(nid, (k + 2) % 4)] = placed_socket(side[::-1].copy())
                self.src[(nid, (k + 2) % 4)] = cid
                self.cache.pop(nid, None)
        self.cache.pop(cid, None)
        self.log("place", cid, pid, q, mv, reason, full)

    def remove(self, cid, reason):
        s = self.sim.pop(cid)
        self.alive[s["i"]] = True
        self.ban.add((cid, s["i"], s["q"]))
        for key, c in list(self.src.items()):
            if c == cid:
                del self.src[key]
                if key in self.base:
                    self.sockets[key] = self.base[key]
                else:
                    self.sockets.pop(key, None)
                self.cache.pop(key[0], None)
        self.cache.pop(cid, None)
        self.n_removals += 1
        self.log("remove", cid, s["piece"], s["q"], dict(confidence=s["confidence"]), reason, None)

    def log(self, typ, cid, pid, q, mv, reason, contour=None, **extra):
        st = dict(n=len(self.steps) + 1, type=typ, cell=cid, piece=pid, rotation=q * 90,
                  plate_turns=plate_turns(self.P[pid], q * 90), reason=reason,
                  simulated_placements=len(self.sim), **extra)
        for k in ("confidence", "pct_cell", "pct_global", "rms_sigma", "n", "alternatives"):
            if k in mv:
                st[k if k != "n" else "n_known"] = (round(mv[k], 4) if isinstance(mv[k], float) else mv[k])
        if "rms" in mv:
            st["rms_sides"] = [round(x, 2) for x in mv["rms"]]
        self.steps.append(st)

    def choose(self, live):
        """Rule for the next placement (STRATEGY)."""
        good = [m for m in live if m["rms_sigma"] <= QUALITY_OK] or live
        if STRATEGY == "constraint":                      # most known sides first, then the safest
            nmax = max(m["n"] for m in good)
            good = [m for m in good if m["n"] == nmax]
        return max(good, key=lambda m: (m["confidence"], m["n"]))

    # ------------------------------------------------------------ loop
    def run(self, verbose=True):
        t0 = time.time()
        while True:
            moves = self.best_moves()
            if not moves:
                break
            dead = [m for m in moves if m["dead_end"]]
            if dead and self.n_removals < MAX_REMOVALS:
                d = max(dead, key=lambda m: m["n"])                 # the most constrained dead end
                culprits = [nid for _, nid in self.neighbours(d["cell"]) if nid in self.sim]
                if culprits:
                    worst = min(culprits, key=lambda c: self.sim[c]["confidence"])
                    self.remove(worst, f"dead end in {d['cell']}: no remaining piece fits")
                    continue
            live = [m for m in moves if not m["dead_end"]]
            if not live:
                # guaranteed end: no acceptable placement left → the least bad one, incompatible types tolerated
                self.force = True
                self.cache.clear()
                cand = [m for m in self.best_moves() if "i" in m]
                self.force = False
                self.cache.clear()
                if not cand:
                    print("  stop: no piece can be placed any more", flush=True); break
                mv = min(cand, key=lambda m: m["rms_sigma"])
                self.n_forced += 1
                self.place(mv, f"forced placement: no acceptable placement left, the least bad one (deviation {mv['rms_sigma']:.1f} σ)")
                continue
            mv = self.choose(live)
            reason = (f"safest of the frontier ({len(moves)} cells): {100 * mv['confidence']:.0f} %, "
                      f"{mv['n']} known side(s), deviation {mv['rms_sigma']:.1f} σ")
            self.place(mv, reason)
            if verbose and len(self.sim) % 50 == 0 and self.steps[-1]["type"] == "place":
                print(f"  {len(self.sim):3d} simulated placements, {self.n_removals} removals, {time.time() - t0:.0f} s", flush=True)
        self.duration = time.time() - t0

    # ------------------------------------------------------------ final self-correction (relaxation)
    def known_nb(self, cid):
        """Known sides of a cell from its current neighbours: simulated piece (shape of its side) or real board."""
        out = []
        for k, nid in self.neighbours(cid):
            if nid in self.sim:
                o = self.sim[nid]
                pts = placed_socket(piece_side_points(self.raw[o["piece"]], ((k + 2) % 4 - o["q"]) % 4)[::-1])
            elif (cid, k) in self.base:
                pts = self.base[(cid, k)]
            else:
                continue
            prof, req = local_profile(pts)
            if req != "F":
                out.append((k, req, prof))
        return out

    def cell_cost(self, cid):
        """(relaxed cost P×4, number of sides) of each piece in the cell, from the current neighbours."""
        kn = self.known_nb(cid)
        if not kn:
            return np.zeros((len(self.pieces), 4)), 0
        sc = score_cells(self.pieces, self.prof, [dict(id=cid, known=kn)], d_board=0.25)[0]
        return self.relaxed(sc), len(kn)

    def relax(self, verbose=True):
        """Swaps of pieces / rotations as long as the global fit improves (energy = Σ squared deviations of the edges)."""
        t0 = time.time()
        owner = {s["i"]: c for c, s in self.sim.items()}
        CC = {c: self.cell_cost(c) for c in self.sim}
        cur = lambda c: CC[c][0][self.sim[c]["i"], self.sim[c]["q"]]
        adj = lambda a, b: any(n == b for _, n in self.neighbours(a))
        e0 = sum(cur(c) for c in self.sim)
        moves, improved = 0, True
        while improved and moves < RELAX_MAX:
            improved = False
            for X in sorted(self.sim, key=lambda c: -cur(c) / max(CC[c][1], 1)):
                if cur(X) / max(CC[X][1], 1) <= (QUALITY_OK * self.sigma) ** 2:
                    break                                           # the remaining cells fit well
                SX = CC[X][0]
                bx, qx = SX.min(1), SX.argmin(1)
                iX = self.sim[X]["i"]
                best = None
                if bx[iX] < cur(X) - 1e-6:                          # same piece, better rotation
                    best = ("rotate", bx[iX] - cur(X), None, int(qx[iX]), None)
                for i in np.argsort(bx)[:RELAX_K]:
                    i = int(i)
                    if i == iX or bx[i] >= cur(X):
                        continue
                    Y = owner[i]
                    if adj(X, Y):
                        continue
                    SY = CC[Y][0]
                    qy = int(SY[iX].argmin())
                    d = (bx[i] + SY[iX, qy]) - (cur(X) + cur(Y))
                    if best is None or d < best[1]:
                        best = ("swap", d, Y, int(qx[i]), qy)
                if best is None or best[1] >= -1e-6:
                    continue
                kind, d, Y, q1, q2 = best
                if kind == "rotate":
                    old = self.sim[X]["q"]
                    self.sim[X]["q"] = q1
                    self.log("rotate", X, self.sim[X]["piece"], q1, {}, f"self-correction: rotation {old * 90}° → {q1 * 90}°, "
                             f"fit improved by {-d / self.sigma ** 2:.1f} σ²", gain_sigma2=round(-d / self.sigma ** 2, 2))
                    touched = [X]
                else:
                    sx, sy = self.sim[X], self.sim[Y]
                    self.sim[X] = dict(sy, q=q1, confidence=sy["confidence"])
                    self.sim[Y] = dict(sx, q=q2, confidence=sx["confidence"])
                    owner[self.sim[X]["i"]], owner[self.sim[Y]["i"]] = X, Y
                    self.log("swap", X, self.sim[X]["piece"], q1, {}, f"self-correction: {self.sim[X]['piece']} and "
                             f"{self.sim[Y]['piece']} swapped between {X} and {Y}, fit improved by {-d / self.sigma ** 2:.1f} σ²",
                             other_cell=Y, other_piece=self.sim[Y]["piece"], other_rotation=q2 * 90,
                             gain_sigma2=round(-d / self.sigma ** 2, 2))
                    touched = [X, Y]
                for c in touched + [n for t in touched for _, n in self.neighbours(t) if n in self.sim]:
                    CC[c] = self.cell_cost(c)
                moves += 1
                improved = True
                if verbose and moves % 100 == 0:
                    print(f"  relaxation: {moves} corrections, energy {sum(cur(c) for c in self.sim) / e0:.3f} × start, "
                          f"{time.time() - t0:.0f} s", flush=True)
                break                                               # restart from the worst fitting cell
        self.n_relax = moves
        self.relax_gain = 1 - sum(cur(c) for c in self.sim) / e0 if e0 else 0.0

    # ------------------------------------------------------------ final measures
    def measures(self):
        sides, per_cell = [], {}
        for cid, s in self.sim.items():
            kn = self.known_nb(cid)
            if not kn:
                continue
            sc = score_cells(self.pieces, self.prof, [dict(id=cid, known=kn)], d_board=0.25)[0]
            r = sc["per_side"][s["i"], s["q"]] / self.sigma
            per_cell[cid] = r
            sides += r.tolist()
        sides = np.array(sides)
        conf = np.array([s["confidence"] for s in self.sim.values()])
        return dict(sigma_u=round(self.sigma, 3),
                    empty_cells_start=len(self.empty), simulated_placements=len(self.sim),
                    unfilled_cells=sorted(self.empty - set(self.sim)),
                    pieces_left=[self.pieces[i]["id"] for i in np.flatnonzero(self.alive)],
                    removals=self.n_removals, forced_placements=self.n_forced,
                    final_corrections=getattr(self, "n_relax", 0),
                    relaxation_gain_pct=round(100 * getattr(self, "relax_gain", 0.0), 1),
                    steps=len(self.steps), duration_s=round(self.duration, 1),
                    sides_evaluated=int(len(sides)),
                    good_sides_pct=round(100 * float((sides <= QUALITY_OK).mean()), 1) if len(sides) else None,
                    bad_sides_pct=round(100 * float((sides > DEAD_END).mean()), 1) if len(sides) else None,
                    median_confidence=round(float(np.median(conf)), 3) if len(conf) else None,
                    placements_confidence_80=int((conf >= 0.8).sum()), placements_confidence_50=int((conf >= 0.5).sum())), per_cell

    def save(self, mes):
        os.makedirs(OUT, exist_ok=True)
        final = {cid: dict(piece=s["piece"], rotation=s["q"] * 90,
                           plate_turns=plate_turns(self.P[s["piece"]], s["q"] * 90),
                           confidence=round(s["confidence"], 4)) for cid, s in sorted(self.sim.items())}
        save_json(os.path.join(OUT, "simulation.json"), dict(
            warning="SIMULATION: hypotheses of the engine, not placements. The real placements are in data/placements.json.",
            parameters=dict(quality_ok_sigma=QUALITY_OK, dead_end_sigma=DEAD_END, max_removals=MAX_REMOVALS),
            start=dict(confirmed_placements=len(self.placements["placed"]), empty_cells=len(self.empty)),
            steps=self.steps, final=dict(placements=final, measures=mes)))


def main():
    s = Simulation()
    print(f"start: {len(s.empty)} empty cells, {int(s.alive.sum())} pieces, σ = {s.sigma:.2f} u", flush=True)
    s.run()
    before, _ = s.measures()
    print(f"  placement done: {len(s.sim)} pieces, {s.n_removals} removals, {s.n_forced} forced placements; "
          f"well fitting sides {before['good_sides_pct']} %", flush=True)
    t = time.time()
    s.relax()
    s.duration += time.time() - t
    mes, _ = s.measures()
    s.save(mes)
    for k, v in mes.items():
        if not isinstance(v, list) or len(v) < 12:
            print(f"  {k}: {v}")
        else:
            print(f"  {k}: {len(v)} → {v[:12]}…")


if __name__ == "__main__":
    main()
