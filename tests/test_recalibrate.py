"""Recalibration photo processed automatically (src/recalibrate.py), on the reference board photo.
Pipeline image (≈ 4 min). The score cache stage is skipped (scores=False): it is the live engine, tested elsewhere."""
import json, os, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
import numpy as np
import cv2
import recalibrate as rc
from common import DATA, load_photo, load_json

REF = os.path.join(ROOT, "photos", "board", "board_2026-11-18.jpg")


class RecalibrateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.expected = load_json(os.path.join(DATA, "params.json"))["empty_cells"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_photo(self, photo, name, expected=None):
        # nothing may be written outside the work directory: in the server image /app is read only
        with mock.patch.object(rc.bm, "REPORTS", "/proc/no-reports"), mock.patch.object(rc.bm, "BOARD_DIR", "/proc/no-board"):
            return rc.process(photo, DATA, os.path.join(self.tmp.name, name), expected or self.expected, scores=False)

    def test_1_reference_photo_gives_the_current_board(self):
        """The reference photo, nothing placed: accepted, upright, and the same sockets as data/ (same photo, same code)."""
        res = self.run_photo(REF, "ref")
        self.assertEqual((res["rotation"], res["n_empty"]), (0, 412))
        new = np.load(os.path.join(self.tmp.name, "ref", "model", "board_sockets.npz"))
        cur = np.load(os.path.join(DATA, "board_sockets.npz"))
        keys = [k for k in cur.files if k.endswith("__points")]
        self.assertEqual(sorted(keys), sorted(k for k in new.files if k.endswith("__points")))
        self.assertTrue(all(np.array_equal(cur[k], new[k]) for k in keys))
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "ref", "model", "model", "version.json")))

    def test_2_turned_photo_is_put_upright(self):
        p = os.path.join(self.tmp.name, "turned.jpg")
        cv2.imwrite(p, np.ascontiguousarray(np.rot90(load_photo("board/board_2026-11-18.jpg"), 1)), [cv2.IMWRITE_JPEG_QUALITY, 95])
        res = self.run_photo(p, "turned")
        self.assertEqual((res["rotation"], res["n_empty"]), (1, 412))

    def test_3_a_recorded_placement_must_be_on_the_photo(self):
        """D051 recorded in L18-C23 (journal) while the photo shows the hole: rejected, the cell named."""
        with self.assertRaises(rc.Rejected) as cm:
            self.run_photo(REF, "d051", [c for c in self.expected if c != "L18-C23"])
        self.assertEqual(cm.exception.reason, "cells")
        self.assertEqual(cm.exception.detail["empty_on_photo"], ["L18-C23"])
        self.assertEqual(cm.exception.detail["filled_on_photo"], [])

    def test_4_blurred_photo_is_rejected_as_blurred(self):
        p = os.path.join(self.tmp.name, "blur.jpg")
        cv2.imwrite(p, cv2.GaussianBlur(load_photo("board/board_2026-11-18.jpg"), (0, 0), 2.5), [cv2.IMWRITE_JPEG_QUALITY, 92])
        with self.assertRaises(rc.Rejected) as cm:
            self.run_photo(p, "blur")
        self.assertEqual(cm.exception.reason, "blur")


if __name__ == "__main__":
    unittest.main()
