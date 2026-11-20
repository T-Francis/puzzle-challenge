"""Journal of the live workshop: pure functions (no I/O except load / append helpers).

The journal is the only truth about the real puzzle. One JSON event per line, append only:
  {"seq", "id", "ts", "type", "cell", "piece", "rotation", "proposal", "model", "app", "device"}
types: fits (piece placed with the proposed rotation), fits_other_rotation (placed, another rotation: "rotation" is
the real one), does_not_fit (the piece does not fit in the cell in any rotation), skip (cell put aside for now),
undo ("target" = seq of the answer undone; only the last effective answer can be undone).
"rotation" is always the canonical q in degrees (as in data/placements.json).
"""
import json, os

ANSWERS = ("fits", "fits_other_rotation", "does_not_fit", "skip")
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


def effective(events):
    """Answers still in force, in order (undo removes the last answer in force)."""
    stack = []
    for e in events:
        if e["type"] in ANSWERS:
            stack.append(e)
        elif e["type"] == "undo" and stack and stack[-1]["seq"] == e.get("target"):
            stack.pop()
    return stack


def placements(events):
    """Same format as data/placements.json."""
    placed, excluded = [], []
    for e in effective(events):
        if e["type"] in ("fits", "fits_other_rotation"):
            placed.append(dict(cell=e["cell"], piece=e["piece"], rotation=e["rotation"]))
        elif e["type"] == "does_not_fit":
            key = f"{e['cell']}:{e['piece']}"
            if key not in excluded:
                excluded.append(key)
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
        if any(x["type"] in ("fits", "fits_other_rotation") and x["cell"] in nb for x in later):
            continue
        out.add(e["cell"])
    return out


def outcomes(events):
    """(bucket, success) of every answered proposal, for the calibration (skips do not count)."""
    out = []
    for e in effective(events):
        b = (e.get("proposal") or {}).get("bucket")
        if b and e["type"] != "skip":
            out.append((b, e["type"] != "does_not_fit"))
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
