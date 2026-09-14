import tempfile
import unittest
from pathlib import Path

from magene_bridge.settings import CalibrationProfile, load_calibration, save_calibration


class SettingsTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            expected = CalibrationProfile(2.04, 180.0, 6.0, 88.2, 18)
            save_calibration(expected, path)
            self.assertEqual(load_calibration(path), expected)

    def test_missing_file_uses_live_calibration_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = load_calibration(Path(directory) / "missing.json")
            self.assertAlmostEqual(profile.multiplier, 2.0)


if __name__ == "__main__":
    unittest.main()
