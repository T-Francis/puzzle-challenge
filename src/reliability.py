"""Reliability by stability: a proposal is reliable if it stays first when the conditions of the computation change.

The percentages of the engine (solve.py) are too optimistic (docs/SIMULATION.md: placements announced ≥ 80 % are
only stable 7 % of the time in the simulation). The engine is therefore run under several independent conditions:
  - catalog: all photos, only those of 2026-11-18, only those of 2026-11-19 (different measurement noise on each
    piece);
  - shadow threshold of the board d_board: 0, 0.25, 0.5 u (uncertainty on the exact edge of the holes).
Stability of a candidate (piece + rotation) in a cell = share of the conditions where it comes first (a condition
where the piece does not exist, e.g. E009–E012 absent from the photos of the 18th, does not count).

Writes "stability" in each candidate of data/proposals.json (read by wave.py).
"""
import os, sys, re, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, SHEET_PHOTOS, load_json, save_json
import fusion, match, solve

D_BOARD = (0.0, 0.25, 0.5)


def photo_dates():
    return sorted({re.search(r"\d{4}-\d{2}-\d{2}", p["file"]).group(0) for p in SHEET_PHOTOS})


def conditions(tmp):
    """(name, json path, npz path) of the catalogs: complete + one per photo date."""
    out = [("all photos", None, None)]
    for d in photo_dates():
        o = os.path.join(tmp, d)
        os.makedirs(o)
        fusion.main(out_dir=o, photo_filter=d)
        out.append((f"photos of {d}", os.path.join(o, "pieces.json"), os.path.join(o, "pieces_raw.npz")))
    return out


def main():
    props = load_json(os.path.join(DATA, "proposals.json"))
    votes = {}                                   # cell -> list of (piece, rotation) ranked first, one per condition
    present = []                                 # pieces available in each condition
    with tempfile.TemporaryDirectory() as tmp:
        cats = conditions(tmp)
        load = solve.load_model
        for name, js, npz in cats:
            solve.load_model = lambda js=js, npz=npz: match.load_model(js, npz)
            ids = {p["id"] for p in solve.load_model()[1]}
            for d in D_BOARD:
                r = solve.run(d_board=d, verbose=False)
                present.append(ids)
                for p in r["proposals"]:
                    if p["candidates"]:
                        c = p["candidates"][0]
                        votes.setdefault(p["cell"], []).append((len(present) - 1, c["piece"], c["rotation"]))
                print(f"  {name}, d_board {d:+.2f}: σ = {r['sigma']:.2f} u", flush=True)
        solve.load_model = load
    n = len(present)
    for p in props["proposals"]:
        v = votes.get(p["cell"], [])
        for c in p["candidates"]:
            eligible = [k for k in range(n) if c["piece"] in present[k]]
            win = sum(1 for k, pid, rot in v if pid == c["piece"] and rot == c["rotation"] and k in eligible)
            c["stability"] = round(win / len(eligible), 2) if eligible else 0.0
    props["reliability"] = dict(method="stability over %d conditions (catalog × d_board)" % n,
                                conditions=[f"{name}, d_board {d}" for name, _, _ in cats for d in D_BOARD])
    save_json(os.path.join(DATA, "proposals.json"), props)
    firsts = [p["candidates"][0]["stability"] for p in props["proposals"] if p["candidates"]]
    print(f"{n} conditions; first choices stable everywhere: {sum(s == 1.0 for s in firsts)}, "
          f"≥ 8/9: {sum(s >= 0.85 for s in firsts)}, ≥ 2/3: {sum(s >= 0.66 for s in firsts)} / {len(firsts)}")


if __name__ == "__main__":
    main()
