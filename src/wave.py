"""Wave of proposals to try physically -> reports/wave_N.md + virtual fitting sheet.

Selection rule (reliability): first choice stable in ≥ 8 conditions out of 9 (reliability.py), ≥ 50 % in its cell,
≥ 20 % at the scale of the puzzle (the piece would not fit better elsewhere), quality not poor, cell not doubtful,
piece proposed only once (on a duplicate, the most reliable cell keeps it).
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, REPORTS, load_json, save_json
import fit_view

TURN_TEXT = {0: "no turn", 1: "¼ turn clockwise", 2: "half turn", 3: "¼ turn anticlockwise"}
MIN_STABILITY, MIN_PCT_CELL, MIN_PCT_GLOBAL = 0.85, 50.0, 20.0


def plate_turns(piece, q_deg):
    """Clockwise quarter turns to apply to the piece as it lies on its sheet (0..3)."""
    tot = (piece["rotation_deg"] + q_deg) % 360
    return int(round(tot / 90)) % 4


def turn_text(piece, q_deg):
    return TURN_TEXT[plate_turns(piece, q_deg)]


def reliable(p):
    c = p["candidates"][0]
    return (not p.get("doubtful") and p["quality"] != "poor" and c.get("stability", 0) >= MIN_STABILITY
            and c["pct_cell"] >= MIN_PCT_CELL and c["pct_global"] >= MIN_PCT_GLOBAL)


def main(n=1):
    P = {p["id"]: p for p in load_json(os.path.join(DATA, "pieces.json"))["pieces"]}
    r = load_json(os.path.join(DATA, "proposals.json"))
    n_cond = len(r.get("reliability", {}).get("conditions", [])) or 9
    cands = [p for p in r["proposals"] if p["candidates"] and reliable(p)]
    cands.sort(key=lambda p: (-p["candidates"][0]["stability"], -p["n_known"], -p["candidates"][0]["pct_cell"]))
    wave, used = [], set()
    for p in cands:
        if p["candidates"][0]["piece"] not in used:
            wave.append(p); used.add(p["candidates"][0]["piece"])
    lines = [f"# Wave {n} — proposals to try", "",
             f"Placements already confirmed: {r['n_placed']} — empty cells left: {r['n_empty_cells_left']}", "",
             "Cell = L(line)-C(column), counted from the top-left corner of the puzzle seen in reading direction (text \"Marcella Giulia Pace\" at the bottom), frame included (L1 and C1 = frame).",
             "Rotation: to apply to the piece **as it lies on its sheet** (sheet the right way up, as in the catalog), the puzzle being seen in reading direction.",
             f"**Reliability** = number of computation conditions (out of {n_cond}: catalog of the photos of the 18th, of the 19th or of all × 3 settings of the hole edges) where this piece comes first. "
             "The engine % is indicative: it is too optimistic until calibrated on real trials.", "",
             "| # | Cell | Known sides | Proposed piece | Rotation | Reliability | Engine % | 2nd choice | 3rd choice |",
             "|---|---|---|---|---|---|---|---|---|"]
    for i, p in enumerate(wave, 1):
        c = p["candidates"]
        alt = [f"{x['piece']} ({turn_text(P[x['piece']], x['rotation'])}, {x['pct_cell']:.0f} %)" for x in c[1:3]]
        alt += [""] * (2 - len(alt))
        lines.append(f"| {i} | {p['cell']} | {p['n_known']} | **{c[0]['piece']}** | {turn_text(P[c[0]['piece']], c[0]['rotation'])} | "
                     f"{round(c[0]['stability'] * n_cond)}/{n_cond} | {c[0]['pct_cell']:.0f} % | {alt[0]} | {alt[1]} |")
    lines += ["", f"Virtual fitting: `reports/wave_{n}_fitting.jpg` (green = 1st choice, orange = 2nd choice).", "",
              "For each line: try the piece; if it fits, place it and note \"OK\", otherwise try the 2nd then the 3rd choice.",
              "Send back the results quoting the cell and the piece (e.g. \"L18-C23 D051 OK, L18-C27 KO\"): they are recorded in `data/placements.json`."]
    # exact trace of what was proposed: check C9 crosses it with the real trials (calibration)
    save_json(os.path.join(REPORTS, f"wave_{n}.json"), dict(wave=n, placed_before=r["n_placed"], proposals=[
        dict(rank=i, cell=p["cell"], n_known=p["n_known"], quality=p["quality"],
             candidates=[dict(piece=c["piece"], rotation=c["rotation"], pct_cell=c["pct_cell"], pct_global=c["pct_global"],
                              stability=c.get("stability")) for c in p["candidates"][:3]])
        for i, p in enumerate(wave, 1)]))
    with open(os.path.join(REPORTS, f"wave_{n}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    fit_view.main([p["cell"] for p in wave], f"wave_{n}_fitting.jpg")
    print("\n".join(lines))


if __name__ == "__main__":
    main(*(int(sys.argv[1]),) if len(sys.argv) > 1 else ())
