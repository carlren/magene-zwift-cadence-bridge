from __future__ import annotations


class CadenceEstimator:
    """Convert crank RPM to an estimated bilateral running step cadence.

    The multiplier is deliberately user-configurable. A value of 2.0 models
    one sensor revolution as one full left/right gait cycle. Smoothing is an
    exponential moving average and can be tuned after live calibration.
    """

    def __init__(self, multiplier: float = 2.0, smoothing: float = 0.35) -> None:
        self.multiplier = multiplier
        self.smoothing = smoothing
        self._smoothed: float | None = None

    def reset(self) -> None:
        self._smoothed = None

    def estimate(self, raw_rpm: float) -> float:
        target = max(0.0, raw_rpm) * self.multiplier
        if self._smoothed is None:
            self._smoothed = target
        else:
            alpha = min(1.0, max(0.0, self.smoothing))
            self._smoothed = alpha * target + (1.0 - alpha) * self._smoothed
        return self._smoothed
