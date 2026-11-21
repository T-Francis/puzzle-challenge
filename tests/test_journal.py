"""Journal of the workshop: replay, undo, placements, skipped cells, calibration outcomes, damaged last line."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import journal


def ev(seq, typ, cell=None, piece=None, rotation=0, **kw):
    return dict(seq=seq, id=f"e{seq}", type=typ, cell=cell, piece=piece, rotation=rotation, **kw)


class JournalTest(unittest.TestCase):
    def test_undo_removes_last_answer_only(self):
        events = [ev(1, "fits", "L1-C1", "A001"), ev(2, "does_not_fit", "L1-C2", "A002"),
                  dict(seq=3, id="e3", type="undo", target=2), dict(seq=4, id="e4", type="undo", target=2)]
        self.assertEqual([e["seq"] for e in journal.effective(events)], [1])

    def test_chained_undo(self):
        events = [ev(1, "fits", "L1-C1", "A001"), ev(2, "fits", "L1-C2", "A002"),
                  dict(seq=3, id="e3", type="undo", target=2), dict(seq=4, id="e4", type="undo", target=1)]
        self.assertEqual(journal.effective(events), [])

    def test_undo_of_an_older_answer_is_ignored(self):
        events = [ev(1, "fits", "L1-C1", "A001"), ev(2, "fits", "L1-C2", "A002"), dict(seq=3, id="e3", type="undo", target=1)]
        self.assertEqual(len(journal.effective(events)), 2)

    def test_correction_replaces_an_older_answer_in_place(self):
        """A ✗ later found placed: the correction turns it into a placement at its own position in the order."""
        events = [ev(1, "fits", "L1-C1", "A001"), ev(2, "does_not_fit", "L1-C2", "A050", 180, proposal=dict(bucket="B")),
                  ev(3, "fits", "L1-C3", "A003"),
                  dict(seq=4, id="e4", type="correction", target=2, answer="fits", rotation=180, cell="L1-C2", piece="A050")]
        pl = journal.placements(events)
        self.assertEqual([p["piece"] for p in pl["placed"]], ["A001", "A050", "A003"])
        self.assertEqual(pl["placed"][1]["rotation"], 180)
        self.assertEqual(pl["excluded"], [])
        self.assertEqual(journal.outcomes(events), [("B", True)])          # the calibration counts it as a success
        # the last answer can still be undone after a correction of an older one
        events.append(dict(seq=5, id="e5", type="undo", target=3))
        self.assertEqual([p["piece"] for p in journal.placements(events)["placed"]], ["A001", "A050"])

    def test_remove_wrong_excludes_and_counts_as_failure(self):
        """A placed piece found to have play: taken out, the pair excluded, the trial a failure; the others stay in order."""
        events = [ev(1, "fits", "L1-C1", "A001", 90, proposal=dict(bucket="A")), ev(2, "fits", "L1-C2", "A002", proposal=dict(bucket="A")),
                  ev(3, "fits", "L1-C3", "A003"),
                  dict(seq=4, id="e4", type="remove", target=1, cell="L1-C1", piece="A001", rotation=90, reason="wrong")]
        pl = journal.placements(events)
        self.assertEqual([p["piece"] for p in pl["placed"]], ["A002", "A003"])
        self.assertEqual(pl["excluded"], ["L1-C1:A001"])
        self.assertEqual(journal.outcomes(events), [("A", False), ("A", True)])
        # the removal is the last action: undo puts the piece back, at its own place in the order
        events.append(dict(seq=5, id="e5", type="undo", target=4))
        pl = journal.placements(events)
        self.assertEqual([p["piece"] for p in pl["placed"]], ["A001", "A002", "A003"])
        self.assertEqual(pl["excluded"], [])

    def test_remove_other_neither_excludes_nor_counts(self):
        events = [ev(1, "fits", "L1-C1", "A001", proposal=dict(bucket="B")),
                  dict(seq=2, id="e2", type="remove", target=1, cell="L1-C1", piece="A001", rotation=0, reason="other")]
        self.assertEqual(journal.placements(events), dict(placed=[], excluded=[]))
        self.assertEqual(journal.outcomes(events), [])

    def test_remove_must_target_a_placement_in_force(self):
        events = [ev(1, "does_not_fit", "L1-C1", "A001"), ev(2, "fits", "L1-C2", "A002"),
                  dict(seq=3, id="e3", type="remove", target=1, cell="L1-C1", piece="A001", reason="wrong"),     # a ✗
                  dict(seq=4, id="e4", type="remove", target=2, cell="L1-C2", piece="A002", reason="bogus"),    # bad reason
                  dict(seq=5, id="e5", type="remove", target=2, cell="L1-C2", piece="A002", reason="other"),
                  dict(seq=6, id="e6", type="remove", target=2, cell="L1-C2", piece="A002", reason="wrong")]    # already out
        self.assertEqual([e["seq"] for e in journal.effective(events)], [1, 2, 5])
        self.assertEqual(journal.placements(events), dict(placed=[], excluded=["L1-C1:A001"]))

    def test_manual_placement_lifts_the_exclusion(self):
        """A ✗ later placed by the user herself: placed, no longer excluded, the ✗ counts as a success; undoable."""
        events = [ev(1, "does_not_fit", "L5-C17", "A050", 180, proposal=dict(bucket="B")), ev(2, "fits", "L1-C2", "A002"),
                  ev(3, "manual", "L5-C17", "A050", 180)]
        pl = journal.placements(events)
        self.assertEqual(pl["placed"], [dict(cell="L1-C2", piece="A002", rotation=0), dict(cell="L5-C17", piece="A050", rotation=180)])
        self.assertEqual(pl["excluded"], [])
        self.assertEqual(journal.outcomes(events), [("B", True)])
        self.assertEqual(journal.skipped_cells([ev(1, "skip", "L5-C16"), ev(2, "manual", "L5-C17", "A050")],
                                               lambda c: ["L5-C17"]), set())        # a manual placement wakes neighbours
        events.append(dict(seq=4, id="e4", type="undo", target=3))
        self.assertEqual(journal.placements(events)["excluded"], ["L5-C17:A050"])
        # a manual placement can itself be taken out
        events = [ev(1, "manual", "L5-C17", "A050", 180),
                  dict(seq=2, id="e2", type="remove", target=1, cell="L5-C17", piece="A050", reason="wrong")]
        self.assertEqual(journal.placements(events), dict(placed=[], excluded=["L5-C17:A050"]))

    def test_placements_format(self):
        events = [ev(1, "fits", "L1-C1", "A001", 90), ev(2, "fits_other_rotation", "L1-C2", "A002", 270),
                  ev(3, "does_not_fit", "L1-C3", "A003"), ev(4, "does_not_fit", "L1-C3", "A003"), ev(5, "skip", "L1-C4")]
        pl = journal.placements(events)
        self.assertEqual(pl["placed"], [dict(cell="L1-C1", piece="A001", rotation=90), dict(cell="L1-C2", piece="A002", rotation=270)])
        self.assertEqual(pl["excluded"], ["L1-C3:A003"])

    def test_skip_comes_back(self):
        nb = lambda c: {"L5-C5": ["L4-C5", "L5-C6"]}.get(c, [])
        events = [ev(1, "skip", "L5-C5")]
        self.assertEqual(journal.skipped_cells(events, nb), {"L5-C5"})
        events.append(ev(2, "fits", "L5-C6", "A001"))               # a neighbour is placed: the cell comes back
        self.assertEqual(journal.skipped_cells(events, nb), set())
        events = [ev(1, "skip", "L5-C5")] + [ev(i, "does_not_fit", "L9-C9", f"B{i:03d}") for i in range(2, 2 + journal.SKIP_COOLDOWN)]
        self.assertEqual(journal.skipped_cells(events, nb), set())   # cooldown elapsed

    def test_outcomes(self):
        events = [ev(1, "fits", "L1-C1", "A001", proposal=dict(bucket="A")), ev(2, "does_not_fit", "L1-C2", "A002", proposal=dict(bucket="B")),
                  ev(3, "skip", "L1-C3", proposal=dict(bucket="A"))]
        self.assertEqual(journal.outcomes(events), [("A", True), ("B", False)])

    def test_file_append_and_truncated_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "live", "journal.jsonl")
            journal.append(p, ev(1, "fits", "L1-C1", "A001"))
            journal.append(p, ev(2, "skip", "L1-C2"))
            with open(p, "a", encoding="utf-8") as f:
                f.write('{"seq": 3, "type": "fi')                      # crash in the middle of a write
            self.assertEqual([e["seq"] for e in journal.load(p)], [1, 2])
            self.assertEqual(journal.next_seq(journal.load(p)), 3)


if __name__ == "__main__":
    unittest.main()
