"""Whole processing chain, in order. Runs in the pipeline container (sh scripts/pipeline.sh <command>):

  all          photos -> catalog -> board -> proposals -> reliability -> wave 1 -> checks (≈ 5 min)
  catalog      after new sheet photos (measure, fuse, SVG, HTML)
  board        after a new board photo
  wave N       after trials recorded in data/placements.json (solve, reliability, wave)
  demo         full simulation + data of the demo (≈ 5 min)
  checks       evaluation + check report + board audit + accuracy report (error budget)
  model        model of the live workshop (catalogs per photo date + version), data/model/
  test         unit and non-regression tests (tests/)
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STAGES = {
    "catalog": ["measure_sheets", "fusion", "export_svg", "build_html"],
    "board": ["board_model"],
    "proposals": ["solve"],
    "checks": ["evaluation", "checks", "board_audit", "accuracy"],
    "demo": ["simulation", "demo_export"],
    "model": ["model_export"],
}


def run(mod, *args):
    t = time.time()
    print(f"\n=== {mod} {' '.join(map(str, args))}", flush=True)
    m = __import__(mod)
    if mod == "solve":
        from common import DATA, save_json
        save_json(os.path.join(DATA, "proposals.json"), m.run(d_board=0.25))
    elif mod == "wave":
        m.main(*args)
    else:
        m.main()
    print(f"    ({time.time() - t:.0f} s)", flush=True)


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what == "all":
        for k in ("catalog", "board", "proposals"):
            for mod in STAGES[k]:
                run(mod)
        run("reliability")
        run("wave", 1)
        for mod in STAGES["checks"]:
            run(mod)
    elif what == "wave":
        run("solve"); run("reliability"); run("wave", int(sys.argv[2]))
    elif what == "test":
        import unittest
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        suite = unittest.defaultTestLoader.discover(os.path.join(root, "tests"), top_level_dir=root)
        sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
    elif what in STAGES:
        for mod in STAGES[what]:
            run(mod)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
