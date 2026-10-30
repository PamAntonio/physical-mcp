"""Tests for calibrate.py: the homography math and the save flow. No browser, no camera.
Run from the repo root:
    python -m unittest backend/test_calibrate.py -v
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import calibrate  # noqa: E402
from calibrate import check_corners, homography, map_box, map_point  # noqa: E402

SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]  # top-left, top-right, bottom-right, bottom-left


class HomographyTests(unittest.TestCase):
    def assertNear(self, got, wanted, places=6):
        self.assertAlmostEqual(got[0], wanted[0], places=places)
        self.assertAlmostEqual(got[1], wanted[1], places=places)

    def test_corners_land_exactly(self):
        # a tilted, foreshortened panel, like a camera at an angle
        target = [[312, 479], [528, 455], [541, 727], [300, 702]]
        matrix = homography(SQUARE, target)
        for index in range(4):
            self.assertNear(map_point(matrix, *SQUARE[index]), target[index])

    def test_same_points_is_identity(self):
        matrix = homography(SQUARE, SQUARE)
        np.testing.assert_allclose(matrix, np.eye(3), atol=1e-9)
        self.assertNear(map_point(matrix, 37, 81), (37, 81))

    def test_scale_and_shift(self):
        # twice the size, moved by (10, 20): every point, not just corners
        target = []
        for x, y in SQUARE:
            target.append([2 * x + 10, 2 * y + 20])
        matrix = homography(SQUARE, target)
        self.assertNear(map_point(matrix, 50, 50), (110, 120))
        self.assertNear(map_point(matrix, 25, 75), (60, 170))
        self.assertEqual(map_box(matrix, [10, 20, 30, 40]), [30, 60, 60, 80])

    def test_recovers_a_known_perspective(self):
        # make target points from a real perspective matrix, then get that matrix back
        known = np.array([[1.2, 0.1, 40.0], [-0.05, 0.9, 25.0], [0.0008, -0.0004, 1.0]])
        target = []
        for x, y in SQUARE:
            target.append(list(map_point(known, x, y)))
        matrix = homography(SQUARE, target)
        np.testing.assert_allclose(matrix, known, rtol=1e-6, atol=1e-9)
        # a point that wasn't one of the 4 lands where the known matrix puts it
        self.assertNear(map_point(matrix, 63, 18), map_point(known, 63, 18))

    def test_straight_lines_stay_straight(self):
        # the middle of an edge maps onto the line between the mapped corners
        target = [[312, 479], [528, 455], [541, 727], [300, 702]]
        matrix = homography(SQUARE, target)
        mid_x, mid_y = map_point(matrix, 50, 0)
        (ax, ay), (bx, by) = target[0], target[1]
        cross = (bx - ax) * (mid_y - ay) - (by - ay) * (mid_x - ax)
        self.assertAlmostEqual(cross, 0, places=6)

    def test_box_is_around_its_mapped_corners_and_kept_in_frame(self):
        matrix = homography(SQUARE, [[0, 0], [200, 0], [200, 200], [0, 200]])
        self.assertEqual(map_box(matrix, [90, 90, 20, 20]), [180, 180, 40, 40])
        self.assertEqual(map_box(matrix, [90, 90, 20, 20], frame_size=[200, 200]), [180, 180, 20, 20])

    def test_points_in_a_line_are_refused(self):
        with self.assertRaises(ValueError):
            homography([[0, 0], [50, 0], [100, 0], [0, 100]], SQUARE)

    def test_corner_order_is_checked(self):
        self.assertEqual(len(check_corners(SQUARE)), 4)
        with self.assertRaises(ValueError):
            check_corners([[0, 0], [100, 100], [100, 0], [0, 100]])  # top-right and bottom-right swapped
        with self.assertRaises(ValueError):
            check_corners([[0, 0], [0, 100], [100, 100], [100, 0]])  # went round the wrong way
        with self.assertRaises(ValueError):
            check_corners([[0, 0], [100, 0], [100, 100]])  # only 3
        with self.assertRaises(ValueError):
            check_corners([[0, 0], [5, 0], [5, 5], [0, 5]])  # too small to be a panel


class SaveFlowTests(unittest.TestCase):
    """Make the reference, then calibrate a new view, in a temp folder."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)
        boxes = {
            "_comment": "test boxes",
            "device_id": "panel-01",
            "frame_size": [400, 300],
            "boxes": {"btn_a": [120, 200, 40, 20], "screen_main": [120, 80, 160, 60]},
            "baselines": {"led_x": {"brightness": 0.2, "redness": 0.1, "greenness": 0.0}},
        }
        (self.path / "panel-01.boxes.json").write_text(json.dumps(boxes), encoding="utf-8")
        # frames as SVG so the test needs no image library
        self.reference_frame = self.path / "reference.svg"
        self.reference_frame.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"></svg>')
        self.new_frame = self.path / "new.svg"
        self.new_frame.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"></svg>')

    def tearDown(self):
        self.folder.cleanup()

    def test_reference_then_calibrate(self):
        panel = [[100, 50], [300, 50], [300, 250], [100, 250]]
        calibrate.Calibration(self.reference_frame, "panel-01", make_reference=True, folder=self.path).save(panel)
        reference = json.loads((self.path / "panel-01.reference.json").read_text(encoding="utf-8"))
        self.assertEqual(reference["panel"], panel)
        self.assertEqual(reference["boxes"]["btn_a"], [120, 200, 40, 20])

        # the same panel, on a frame twice the size and moved by (40, 20)
        moved = []
        for x, y in panel:
            moved.append([2 * x + 40, 2 * y + 20])
        run = calibrate.Calibration(self.new_frame, "panel-01", folder=self.path)
        self.assertEqual(run.preview(moved)["boxes"]["btn_a"], [280, 420, 80, 40])
        result = run.save(moved)

        saved = json.loads((self.path / "panel-01.boxes.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["boxes"], {"btn_a": [280, 420, 80, 40], "screen_main": [280, 180, 320, 120]})
        self.assertEqual(saved["frame_size"], [800, 600])
        self.assertEqual(saved["baselines"]["led_x"]["brightness"], 0.2, "everything else in the file stays")

        backup = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
        self.assertEqual(backup["boxes"]["btn_a"], [120, 200, 40, 20], "the old boxes are backed up")

        # calibrating again starts from the reference, not from the last result
        again = calibrate.Calibration(self.new_frame, "panel-01", folder=self.path)
        self.assertEqual(again.preview(moved)["boxes"]["btn_a"], [280, 420, 80, 40])

    def test_needs_a_reference_first(self):
        with self.assertRaises(SystemExit) as raised:
            calibrate.Calibration(self.new_frame, "panel-01", folder=self.path)
        self.assertIn("--reference", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
