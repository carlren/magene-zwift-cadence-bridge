from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable


class SensorStatus(str, Enum):
    DISCONNECTED = "disconnected"
    SCANNING = "scanning"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class PublisherStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    ADVERTISING = "advertising"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class BridgeSnapshot:
    running: bool = False
    sensor_status: SensorStatus = SensorStatus.DISCONNECTED
    publisher_status: PublisherStatus = PublisherStatus.STOPPED
    sensor_name: str = "Magene S3+"
    sensor_detail: str = "Ready to scan"
    publisher_detail: str = "Virtual footpod is off"
    raw_cadence_rpm: float | None = None
    estimated_cadence_spm: float | None = None
    multiplier: float = 2.0
    calibration_target_spm: float = 180.0
    calibration_speed_mph: float = 6.0
    calibration_active: bool = False
    calibration_samples: int = 0
    calibration_required: int = 12
    calibration_result_factor: float | None = None
    packet_count: int = 0
    last_packet_age_s: float | None = None
    message: str = "Bridge is ready"


class BridgeStore:
    """Small framework-neutral observable state container."""

    def __init__(self, initial: BridgeSnapshot | None = None) -> None:
        self._snapshot = initial or BridgeSnapshot()
        self._listeners: list[Callable[[BridgeSnapshot], None]] = []

    @property
    def snapshot(self) -> BridgeSnapshot:
        return self._snapshot

    def subscribe(self, listener: Callable[[BridgeSnapshot], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        listener(self._snapshot)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def update(self, **changes: object) -> BridgeSnapshot:
        self._snapshot = replace(self._snapshot, **changes)
        for listener in tuple(self._listeners):
            listener(self._snapshot)
        return self._snapshot
