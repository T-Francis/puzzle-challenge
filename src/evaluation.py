"""Evaluation of the catalog against the board: does the fusion bring precision?

For each photo series alone, then for the fusion of all, a catalog is rebuilt (in a temporary directory) and the
typical piece ↔ socket deviation of the best match is measured on the real sockets of the board (σ = median over
the cells with one known side, as in solve.py). The lower σ, the more accurate the shapes.
Control: the fusion with a constant scale per photo (no correction of a photo taken at an angle).
Output: data/evaluation.json (read by checks.py).
"""
import os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, save_json
from reliability import photo_dates
import fusion, match, solve


def main():
    series = [(f"photos of {d}", d, True) for d in photo_dates()] + [("fusion of all photos", None, True),
                                                                    ("fusion, constant scale per photo (control)", None, False)]
    res = {}
    with tempfile.TemporaryDirectory() as tmp:
        for k, (name, filt, field) in enumerate(series):
            d = os.path.join(tmp, str(k)); os.makedirs(d)
            slope = fusion.MIN_SLOPE_PIECES
            fusion.MIN_SLOPE_PIECES = slope if field else 10 ** 9        # control: no scale variation within the photo
            fusion.main(out_dir=d, photo_filter=filt)
            fusion.MIN_SLOPE_PIECES = slope
            solve.load_model = lambda d=d: match.load_model(os.path.join(d, "pieces.json"), os.path.join(d, "pieces_raw.npz"))
            r = solve.run(d_board=0.25, verbose=False)
            multi = [p for p in r["proposals"] if p["candidates"] and p["n_known"] >= 2]
            res[name] = dict(sigma_u=round(r["sigma"], 3),
                             rms_median_cells_2_sides_u=round(float(np.median([p["rms_mean"] for p in multi])), 3),
                             cells_2_sides_above_80pct=int(sum(p["candidates"][0]["pct_cell"] > 80 for p in multi)),
                             cells_2_sides=len(multi))
            print(f"{name:42s} σ = {r['sigma']:.3f} u", flush=True)
    save_json(os.path.join(DATA, "evaluation.json"), res)


if __name__ == "__main__":
    main()
