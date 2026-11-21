"""Journal of the live workshop: pure functions (no I/O except load / append helpers).

The journal is the only truth about the real puzzle. One JSON event per line, append only:
  {"seq", "id", "ts", "type", "cell", "piece", "rotation", "proposal", "model", "app", "device"}
types: fits (piece placed with the proposed rotation), fits_other_rotation (placed, another rotation: "rotation" is
the real one), does_not_fit (the piece does not fit in the cell in any rotation), skip (cell put aside for now),
undo ("target" = seq of the answer undone; only the last effective answer can be undone), correction ("target" =
seq of an earlier answer, "answer" = the answer it should have been, "rotation"; written by hand when the user reports
a mistake the app can no longer undo, e.g. a ✗ on a piece that was in fact placed: it keeps its place in the order),
remove (an earlier placement taken out of the puzzle: "target" = seq of the placement, "cell", "piece", "reason" =
"wrong" — it had play / did not belong there: the pair is excluded and the trial counts as a failure — or "other" —
taken out for another reason: no exclusion, the trial no longer counts), manual (a piece the user placed herself:
"cell", "piece", "rotation"; lifts an earlier exclusion of that pair, and an earlier ✗ of that pair counts as a
success). remove and manual are actions like the answers: the last one can be undone.
"rotation" is always the canonical q in degrees (as in data/placements.json).
"""
import json, os

ANSWERS = ("fits", "fits_other_rotation", "does_not_fit", "skip")
PLACING = ("fits", "fits_other_rotation", "manual")
REMOVE_REASONS = ("wrong", "other")
SKIP_COOLDOWN = 10          # a skipped cell comes back after this many answers (or when a neighbour is placed)


def load(path):
    """Events of a journal file; a truncated last line (crash during a write) is ignored."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                break
    return out


def append(path, event):
    """Appends one event and forces it to disk before returning (the answer is only confirmed after this)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def in_force(eff):
    """Placements of an effective list still on the puzzle (not taken out by a remove), in order."""
    removed = {e["target"] for e in eff if e["type"] == "remove"}
    return [e for e in eff if e["type"] in PLACING and e["seq"] not in removed]


def effective(events):
    """Actions still in force, in order (undo removes the last action in force; a correction replaces an earlier
    answer in place, keeping its position and seq; a remove only counts if its target is a placement in force)."""
    stack = []
    for e in events:
        t = e["type"]
        if t in ANSWERS or t == "manual":
            stack.append(e)
        elif t == "remove":
            if e.get("reason") in REMOVE_REASONS and any(a["seq"] == e.get("target") for a in in_force(stack)):
                stack.append(e)
        elif t == "undo" and stack and stack[-1]["seq"] == e.get("target"):
            stack.pop()
        elif t == "correction" and e.get("answer") in ANSWERS:
            for i, a in enumerate(stack):
                if a["seq"] == e.get("target"):
                    stack[i] = dict(a, type=e["answer"], rotation=e.get("rotation", a.get("rotation")), corrected_by=e["seq"])
                    break
    return stack


def placements(events):
    """Same format as data/placements.json."""
    eff = effective(events)
    kept = {e["seq"] for e in in_force(eff)}
    placed, excluded = [], []

    def exclude(key):
        if key not in excluded:
            excluded.append(key)
    for e in eff:
        key = f"{e.get('cell')}:{e.get('piece')}"
        if e["type"] in PLACING and e["seq"] in kept:
            placed.append(dict(cell=e["cell"], piece=e["piece"], rotation=e["rotation"]))
            if e["type"] == "manual" and key in excluded:
                excluded.remove(key)
        elif e["type"] == "does_not_fit":
            exclude(key)
        elif e["type"] == "remove" and e["reason"] == "wrong":
            exclude(key)
    return dict(placed=placed, excluded=excluded)


def skipped_cells(events, neighbours):
    """Cells put aside: skipped less than SKIP_COOLDOWN answers ago and no neighbour placed since.
    neighbours(cell) -> iterable of neighbouring cell ids."""
    eff = effective(events)
    out = set()
    for i, e in enumerate(eff):
        if e["type"] != "skip":
            continue
        later = eff[i + 1:]
        if len(later) >= SKIP_COOLDOWN:
            continue
        nb = set(neighbours(e["cell"]))
        if any(x["type"] in PLACING and x["cell"] in nb for x in later):
            continue
        out.add(e["cell"])
    return out


def outcomes(events):
    """(bucket, success) of every answered proposal, for the calibration (skips do not count). A placement taken out
    as "wrong" is a failure, one taken out for another reason no longer counts; a ✗ on a pair the user later placed
    herself is a success."""
    eff = effective(events)
    removed = {e["target"]: e["reason"] for e in eff if e["type"] == "remove"}
    manual = {(e["cell"], e["piece"]) for e in in_force(eff) if e["type"] == "manual"}
    out = []
    for e in eff:
        b = (e.get("proposal") or {}).get("bucket")
        if not b or e["type"] not in ANSWERS or e["type"] == "skip":
            continue
        ok = e["type"] != "does_not_fit"
        if e["seq"] in removed:
            if removed[e["seq"]] != "wrong":
                continue
            ok = False
        elif not ok and (e["cell"], e["piece"]) in manual:
            ok = True
        out.append((b, ok))
    return out


def next_seq(events):
    return (events[-1]["seq"] + 1) if events else 1


def main(src, dst):
    """Derives data/placements.json from a journal fetched from the server."""
    ev = load(src)
    pl = placements(ev)
    out = dict(_help="Derived from the live journal (data/live/journal.jsonl) by src/journal.py: do not edit by hand. "
                     "placed: pieces confirmed physically, in order (cell, piece, rotation = canonical q in degrees). "
                     "excluded: failed trials as 'cell:piece'.", **pl)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    eff = effective(ev)
    print(f"{len(ev)} events, {len(eff)} answers in force: {len(pl['placed'])} placed, {len(pl['excluded'])} excluded -> {dst}")


if __name__ == "__main__":
    import sys
    main(*sys.argv[1:3])
