from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    multiplier: float = 2.0
    target_spm: float = 180.0
    speed_mph: float = 6.0
    raw_median_rpm: float = 0.0
    sample_count: int = 0


def settings_path() -> Path:
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_root / "magene-zwift-bridge" / "calibration.json"


def load_calibration(path: Path | None = None) -> CalibrationProfile:
    target = path or settings_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return CalibrationProfile(
            multiplier=float(data["multiplier"]),
            target_spm=float(data["target_spm"]),
            speed_mph=float(data["speed_mph"]),
            raw_median_rpm=float(data.get("raw_median_rpm", 0.0)),
            sample_count=int(data.get("sample_count", 0)),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return CalibrationProfile()


def save_calibration(profile: CalibrationProfile, path: Path | None = None) -> None:
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(profile), indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
