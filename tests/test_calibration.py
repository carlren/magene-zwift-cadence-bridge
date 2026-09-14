import unittest

from magene_bridge.controller import AppController
from magene_bridge.model import BridgeStore


class CalibrationTests(unittest.TestCase):
    def test_twelve_samples_set_factor_from_median(self) -> None:
        store = BridgeStore()
        controller = AppController(store, demo=True)
        controller.begin_calibration()

        samples = [89.0, 90.0, 91.0, 90.0, 89.5, 90.5, 90.0, 89.0, 91.0, 90.0, 89.5, 90.5]
        for index, raw_rpm in enumerate(samples, start=1):
            controller.receive_cadence(raw_rpm, index)

        self.assertFalse(store.snapshot.calibration_active)
        self.assertAlmostEqual(store.snapshot.multiplier, 2.0, places=6)
        self.assertEqual(store.snapshot.calibration_samples, 12)

    def test_calibration_factor_is_bounded(self) -> None:
        store = BridgeStore()
        controller = AppController(store, demo=True)
        controller.begin_calibration()
        for index in range(12):
            controller.receive_cadence(5.0, index + 1)
        self.assertEqual(store.snapshot.multiplier, 3.0)


if __name__ == "__main__":
    unittest.main()
