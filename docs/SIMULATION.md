# Simulation of the reconstruction — design note

Status: engine, animated demo (direct path, bilingual), constrained drawing with fit colours. The real reconstruction goes through the live workshop (`docs/ATELIER.md`).

## Two separate tracks

| | Simulation (showcase + help) | Real reconstruction |
|---|---|---|
| Who places | the engine, virtually | the user, physically |
| Truth | internal consistency only | fits / does not fit on the real puzzle |
| Data | `data/simulation/` | `data/placements.json` (then the workshop journal) |
| Extent | to the end (412 pieces) | trial by trial |

**Absolute rule: the simulation reads `placements.json` only as a starting point (confirmed placements, failed trials)
and never writes to it.** A hypothesis must never pass for a placement.

## Principle

Start from the real state (board + confirmed placements), then repeat:

1. **Frontier**: empty cells with at least one known side (edge of a placed piece, real or simulated).
2. **Score** of each remaining piece × rotation in each frontier cell: sum of squared shape deviations on the known
   sides (`solve.score_cells`), likelihood with the σ of the board, then exclusivity (a piece only goes to one place:
   `solve.sinkhorn` over remaining pieces × frontier).
3. **Choice of the placement**: the cell whose best candidate is the safest (global probability), with sufficient
   absolute quality; on a tie, the most constrained cell (most known sides).
4. **Virtual placement**: the piece is fitted on the known sides (`fit_view.fit_piece_pts`); its free sides become
   known sides for its neighbours (like a real placement, `solve.effective_sockets`).
5. **Check**: if a neighbouring cell has no acceptable candidate left (dead end), backtrack (§ Self-correction).

Incremental: only the neighbouring cells of the last placement are rescored; σ is fixed once on the start state.

## Self-correction

Without fits / does not fit answers, the only available evidence is **consistency**: every piece must fit with all
its neighbours, and the remaining pieces must go into the remaining cells.

1. **Backtracking** (during placement): dead end in a cell → the least safe neighbouring simulated placement is
   removed, (cell, piece, rotation) is forbidden in this branch, and the search resumes. Limited budget.
2. **Relaxation** (after the full placement): energy = sum over all edges between neighbouring cells of the squared
   fit deviation. Local search: swap of two pieces, change of rotation, as long as the energy decreases.

## Output: `data/simulation/simulation.json`

```
{ "warning", "parameters": {...}, "start": {"confirmed_placements", "empty_cells"},
  "steps": [ {"n", "type": "place" | "remove" | "swap" | "rotate", "cell", "piece",
              "rotation" (canonical q, degrees), "plate_turns" (quarter turns from the sheet),
              "confidence", "pct_cell", "pct_global", "rms_sides", "n_known", "alternatives": [...], "reason"} ],
  "final": { "placements": {cell: {piece, rotation, plate_turns, confidence}}, "measures": {...} } }
```

## Results (2026-11-19)

The engine reconstructs 367 pieces out of 412 in ≈ 2 min before forced placements (967 steps including 300
removals, budget exhausted).

**Accurate measurement, but error propagation.** Over the first 60 placements, 84 % of the sides facing the real
board and 95 % of the sides between simulated pieces fit well (≤ 1.5 σ). At the end, only 34 % (19 % > 3 σ); by
fifth of the placement order: 53, 43, 33, 24, 14 % of good sides. Choosing the most constrained cell first changes
nothing (29.5 %; 50 → 10 %).

**Credibility horizon ≈ 25 placements.** Four variants (choice rule; σ × 0.9 / 1.0 / 1.1): placements identical in
the 4 variants: 60 % of placements 1–25, 4 % of placements 26–50, ≈ 0 % beyond. The simulation is chaotic: a small
variation changes the whole sequel. "Confidences" ≥ 80 % are only stable 7 % of the time: **the probabilities of
the engine are too optimistic** (also true for the waves; to be calibrated on real trials, check C9).

**Cause**: σ ≈ 1.6 u per side, while many pieces have a similar side within 1.5 σ; a cell with 1 known side
discriminates poorly, and each error corrupts the known sides of its neighbours.

**Useful by-product**: stress test of wave 1 (12 proposals out of 16 identical in the 4 variants).

## Follow-ups (2026-11-19)

**Refining the holes on the quarter photos of the board: negative.** Reference contours carried over to the most
central quarter (SIFT homography, error ≈ 1.2 u), then paper edge searched along the normal (median level, ±6 u).
163 sides out of 227 refined, median shift 1 u. Result: σ 1.56 → 1.72–1.87 u and fewer clear proposals, whatever
the shadow threshold and the corner handling. Probable cause: close and oblique photos (lens distortion, relief of
the pieces) that the homography does not correct. Prototype not integrated.

**Reliability by stability: integrated** (`src/reliability.py`, `wave` stage of the pipeline). 9 conditions (catalog:
all photos / 18th / 19th × d_board 0 / 0.25 / 0.5); stability of a candidate = share of the conditions where it comes
first. First choices stable everywhere: 49 cells out of 166; ≥ 8/9: 58. Wave rule (`wave.py`): stability ≥ 8/9,
≥ 50 % in the cell, ≥ 20 % global, quality not poor, unique piece. Wave 1: 12 proposals, all 9/9.

**Calibration on real trials: ready.** Each wave keeps its proposals (`reports/wave_N.json`); check C9 crosses them
with `placements.json` and gives the success rate by reliability and by engine %.

## Animated demo

Intention: the **showcase** assumes the engine corrects itself to the end (complete
reconstruction); the **real** reconstruction is corrected by trials. The two never mix.

**Engine completed** (`simulation.py`): guaranteed end ("forced" placement of the least bad one when nothing is
acceptable any more, incompatible types penalised by 25 σ²) then **relaxation** (swaps of two non-neighbouring pieces
and rotation changes, as long as the sum of the squared edge deviations decreases). Result: 412 / 412 pieces, 1,265
steps (412 placements including 45 forced, 300 removals, 253 corrections), fit energy −61 % through relaxation.
Well-fitting sides at the end: 22.5 % — the demo is not a reliable reconstruction, in line with the ≈ 25 placement
horizon measured above.

**Page**: `demo/index.html` (HTML + SVG + JS without dependency, works from file://) reads `demo/demo_data.js`
(`demo_export.py`). Pipeline: `sh scripts/pipeline.sh demo` (simulation ≈ 4.5 min + export).

## Demo v2: the direct path

- The demo shows **only the direct path**: each piece flies once to its final cell (state after self-correction),
  in the order where each cell was first filled. The search timeline (removals, swaps) is not shown.
  Clean background, pieces at their real position, bilingual EN / FR. Details: `docs/ATELIER.md` §2.
- The step-by-step real mode became the **live workshop** (server + phone): `docs/ATELIER.md`.

## What the demo misfits really are (2026-11-20)

- The demo drawing is one global least-squares adjustment (`src/demo_fit.py`): every piece on its real sockets and
  on its drawn neighbours, tilt free within ±8° (a right piece can need 5°: catalog frame), robust loss. The first
  placements fit their real holes at 1.1–2.1 u per side; at 412 / 412, most pieces still do not fit their neighbours.
  **The misfits seen in the demo are wrong pieces, not drawing**: the prediction must improve, not the picture.
- Button ◐ of the demo (off by default): colours the pieces by this fit (fits well / approximate / does not fit).
- Sharper board models (lighting normalisation, expected empty cells, corners on the seam lines) and new 64 Mpx photos
  changed the first ≈ 25 placements very little and the sequel chaotically: the horizon stayed at ≈ 25 placements.
  The photos were not the limit.

## Curve-distance scores: the simulation loses its meaning beyond the frontier

With the curve distance (`match.curve_dist`), a board socket identifies its piece far better, but a socket made from
a simulated piece (catalog against catalog) separates the true mate from impossible partners for about a third of the
tabs only. The simulation, which builds everything from simulated pieces after the first placements, now hits dead
ends early: 210 forced placements of 412, median confidence 0.18, demo ◐ 11 / 32 / 369. The horizon of a reconstruction
from the catalog alone is the frontier of board-measured sockets (≈ 167 cells); beyond it, progress needs a
recalibration photo (placed pieces become board sockets). The animated demo still flies all 412 pieces, as a
showcase: the ◐ button shows what fits.
