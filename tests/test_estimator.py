import unittest

from magene_bridge.estimator import CadenceEstimator


class CadenceEstimatorTests(unittest.TestCase):
    def test_initial_estimate_uses_multiplier(self) -> None:
        estimator = CadenceEstimator(multiplier=2.0)
        self.assertEqual(estimator.estimate(82.0), 164.0)

    def test_negative_input_is_clamped(self) -> None:
        estimator = CadenceEstimator(multiplier=2.0)
        self.assertEqual(estimator.estimate(-5.0), 0.0)

    def test_followup_values_are_smoothed(self) -> None:
        estimator = CadenceEstimator(multiplier=2.0, smoothing=0.25)
        estimator.estimate(80.0)
        self.assertEqual(estimator.estimate(100.0), 170.0)

    def test_reset_removes_smoothing_history(self) -> None:
        estimator = CadenceEstimator(multiplier=2.0, smoothing=0.1)
        estimator.estimate(80.0)
        estimator.reset()
        self.assertEqual(estimator.estimate(90.0), 180.0)


if __name__ == "__main__":
    unittest.main()
