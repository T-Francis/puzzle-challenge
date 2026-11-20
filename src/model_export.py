"""Model of the live workshop: everything the server needs, frozen, with a content version.

data/model/ (regenerated, not versioned):
  catalogs/<date>/pieces.json + pieces_raw.npz   catalog built from the photos of one date only (reliability conditions)
  version.json                                   content hash of every model file (the "model version" of the journal)
  score_cache.npz                                side scores of the board sockets, every condition (solve.save_score_cache)
The main catalog, the board and the parameters stay in data/ (pieces.json, pieces_raw.npz, board.json,
board_sockets.npz, params.json); the server image copies them with data/model/.
"""
import os, sys, hashlib, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, save_json
from reliability import photo_dates
import fusion

OUT = os.path.join(DATA, "model")
MODEL_FILES = ["pieces.json", "pieces_raw.npz", "board.json", "board_sockets.npz", "params.json"]


def files():
    out = [os.path.join(DATA, f) for f in MODEL_FILES]
    for d in photo_dates():
        out += [os.path.join(OUT, "catalogs", d, "pieces.json"), os.path.join(OUT, "catalogs", d, "pieces_raw.npz")]
    return out


def version():
    h = hashlib.sha256()
    for f in files():
        h.update(os.path.relpath(f, DATA).encode())
        with open(f, "rb") as fh:
            h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()[:12]


def main():
    for d in photo_dates():
        o = os.path.join(OUT, "catalogs", d)
        os.makedirs(o, exist_ok=True)
        fusion.main(out_dir=o, photo_filter=d)
    v = version()
    save_json(os.path.join(OUT, "version.json"), dict(version=v, files=[os.path.relpath(f, DATA) for f in files()]))
    print("model version", v)
    # side scores of the board sockets under every condition (curve distances: minutes to compute), frozen for the
    # workshop start-up; derived from the files above, so not part of the version
    import solve
    from live import LiveEngine
    solve._CACHE.clear()
    LiveEngine(DATA).proposals()
    solve.save_score_cache(os.path.join(OUT, "score_cache.npz"))
    print("score cache:", len(solve._CACHE), "sockets")


if __name__ == "__main__":
    main()
