"""Live engine: same results as the batch pipeline, correct incremental cache, wave 1 at start, peeling of a cell.
Needs the model (sh scripts/pipeline.sh model) and proposals.json with stability (pipeline 'wave')."""
import os, sys, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from common import DATA, REPORTS, load_json
import live, journal, solve

ENGINE = None


def engine():
    global ENGINE
    if ENGINE is None:
        t = time.time()
        ENGINE = live.LiveEngine()
        print(f"\n  engine loaded in {time.time() - t:.1f} s ({len(ENGINE.conditions)} conditions, model {ENGINE.model_version})")
    return ENGINE


def strip(props):
    return [(p["cell"], [(c["piece"], c["rotation"], c["pct_cell"], c["pct_global"], c["rms_sides"], c.get("stability"))
                         for c in p["candidates"]]) for p in props]


class LiveTest(unittest.TestCase):
    def test_1_start_equals_batch_with_stability(self):
        """At start (empty journal), candidates and stability = data/proposals.json (solve + reliability)."""
        e = engine()
        e.apply(dict(placed=[], excluded=[]))
        t = time.time()
        props, sigma = e.proposals()
        print(f"  first proposals in {time.time() - t:.1f} s")
        ref = load_json(os.path.join(DATA, "proposals.json"))
        self.assertAlmostEqual(sigma, ref["sigma"], places=12)
        self.assertEqual(strip(props), strip(ref["proposals"]))

    def test_2_wave1_is_proposed_first(self):
        """The first proposals of the workshop are those of wave 1 (bucket A = the wave rule)."""
        e = engine()
        e.apply(dict(placed=[], excluded=[]))
        props, sigma = e.proposals()
        wave = load_json(os.path.join(REPORTS, "wave_1.json"))["proposals"]
        first = {(p["cell"], p["candidates"][0]["piece"]) for p in wave}
        a = {(p["cell"], p["candidates"][0]["piece"]) for p in props if p["candidates"] and live.bucket(p, p["candidates"][0], sigma)[0] == "A"}
        self.assertTrue(first <= a, f"wave 1 not all in bucket A: {first - a}")
        prop, _ = live.choose(e, props, sigma, [])
        self.assertIn((prop["cell"], prop["piece"]), first)

    def test_3_incremental_equals_batch(self):
        """After placements and exclusions, the incremental engine (warm cache) = solve.run on the same placements."""
        e = engine()
        e.apply(dict(placed=[], excluded=[]))
        props, _ = e.proposals()
        top = [p for p in props if p["candidates"] and p["n_known"] >= 2][:4]
        pl = dict(placed=[dict(cell=p["cell"], piece=p["candidates"][0]["piece"], rotation=p["candidates"][0]["rotation"]) for p in top[:3]],
                  excluded=[f"{top[3]['cell']}:{top[3]['candidates'][0]['piece']}"])
        steps = [dict(placed=pl["placed"][:k], excluded=[]) for k in range(1, 4)] + [pl]
        t = time.time()
        for s in steps:
            e.apply(s)
            props_inc, sigma_inc = e.solve(e.conditions[e.main])[:2]
        print(f"  4 incremental updates (main condition) in {time.time() - t:.1f} s")
        old = solve.load_board
        solve.load_board = lambda: (*old()[:2], pl)
        try:
            ref = solve.run(d_board=live.MAIN_D_BOARD, verbose=False)
        finally:
            solve.load_board = old
        self.assertAlmostEqual(sigma_inc, ref["sigma"], places=12)
        self.assertEqual(strip(props_inc), strip(ref["proposals"]))

    def test_4_answer_time(self):
        """Time of a full update (9 conditions) after one placement: the workshop needs ≤ 2 s."""
        e = engine()
        e.apply(dict(placed=[], excluded=[]))
        props, sigma = e.proposals()
        p = next(p for p in props if p["candidates"] and p["n_known"] >= 2)
        t = time.time()
        e.apply(dict(placed=[dict(cell=p["cell"], piece=p["candidates"][0]["piece"], rotation=p["candidates"][0]["rotation"])], excluded=[]))
        e.proposals()
        dt = time.time() - t
        print(f"  update after one placement (9 conditions): {dt:.2f} s")
        self.assertLess(dt, 10.0)

    def test_5_peel_cell_after_does_not_fit(self):
        """After ✗, the next proposal stays on the same cell with another piece (if it has a credible candidate)."""
        e = engine()
        e.apply(dict(placed=[], excluded=[]))
        props, sigma = e.proposals()
        prop, _ = live.choose(e, props, sigma, [])
        events = [dict(seq=1, id="x", type="does_not_fit", cell=prop["cell"], piece=prop["piece"], rotation=prop["rotation"],
                       proposal=dict(bucket=prop["bucket"]))]
        e.apply(journal.placements(events))
        props2, sigma2 = e.proposals()
        nxt, _ = live.choose(e, props2, sigma2, events)
        self.assertNotEqual(nxt["piece"], prop["piece"])
        cell_props = next(p for p in props2 if p["cell"] == prop["cell"])
        if cell_props["candidates"]:
            b, q = live.bucket(cell_props, cell_props["candidates"][0], sigma2)
            cal = live.calibration(journal.outcomes(events))
            if cal[b]["chance"] >= live.MIN_CHANCE and q != "poor":
                self.assertEqual(nxt["cell"], prop["cell"])
                self.assertEqual(nxt["rank"], 2)

    def test_6_deterministic(self):
        e = engine()
        e.apply(dict(placed=[], excluded=[]))
        a = live.choose(e, *e.proposals(), [])[0]
        e.apply(dict(placed=[], excluded=[]))
        b = live.choose(e, *e.proposals(), [])[0]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
