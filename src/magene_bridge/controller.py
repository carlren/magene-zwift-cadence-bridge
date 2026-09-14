from __future__ import annotations

import math
import statistics
from typing import Protocol

from gi.repository import GLib

from .estimator import CadenceEstimator
from .model import BridgeStore, PublisherStatus, SensorStatus
from .settings import CalibrationProfile, load_calibration, save_calibration


class BridgeBackend(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...


class AppController:
    def __init__(self, store: BridgeStore, demo: bool = False) -> None:
        self.store = store
        self.persist_settings = not demo
        profile = CalibrationProfile() if demo else load_calibration()
        self.store.update(
            multiplier=profile.multiplier,
            calibration_target_spm=profile.target_spm,
            calibration_speed_mph=profile.speed_mph,
            calibration_result_factor=profile.multiplier,
        )
        self.estimator = CadenceEstimator(multiplier=profile.multiplier)
        self._calibration_samples: list[float] = []
        if demo:
            self.backend: BridgeBackend = DemoBackend(self)
        else:
            from .backends.bluez import BlueZBackend

            self.backend = BlueZBackend(self)

    def toggle_bridge(self) -> None:
        if self.store.snapshot.running:
            self.backend.stop()
        else:
            self.backend.start()

    def set_multiplier(self, multiplier: float) -> None:
        self.estimator.multiplier = multiplier
        snapshot = self.store.snapshot
        changes: dict[str, object] = {"multiplier": multiplier}
        if snapshot.raw_cadence_rpm is not None:
            self.estimator.reset()
            changes["estimated_cadence_spm"] = self.estimator.estimate(snapshot.raw_cadence_rpm)
        self.store.update(**changes)
        if self.persist_settings:
            save_calibration(
                CalibrationProfile(
                    multiplier=multiplier,
                    target_spm=snapshot.calibration_target_spm,
                    speed_mph=snapshot.calibration_speed_mph,
                    raw_median_rpm=0.0,
                    sample_count=0,
                )
            )

    def begin_calibration(self) -> None:
        self._calibration_samples.clear()
        self.store.update(
            calibration_active=True,
            calibration_samples=0,
            calibration_result_factor=None,
            message="Calibration armed · keep a steady pace",
        )

    def cancel_calibration(self) -> None:
        self._calibration_samples.clear()
        self.store.update(
            calibration_active=False,
            calibration_samples=0,
            message="Calibration cancelled",
        )

    def receive_cadence(self, raw_rpm: float, packet_count: int) -> None:
        estimate = self.estimator.estimate(raw_rpm)
        changes: dict[str, object] = dict(
            raw_cadence_rpm=raw_rpm,
            estimated_cadence_spm=estimate,
            packet_count=packet_count,
            last_packet_age_s=0.0,
            message="Live cadence is flowing",
        )
        snapshot = self.store.snapshot
        if snapshot.calibration_active and raw_rpm > 0:
            self._calibration_samples.append(raw_rpm)
            sample_count = len(self._calibration_samples)
            changes["calibration_samples"] = sample_count
            changes["message"] = f"Calibrating · sample {sample_count} of {snapshot.calibration_required}"
            if sample_count >= snapshot.calibration_required:
                median_rpm = statistics.median(self._calibration_samples)
                factor = min(3.0, max(0.5, snapshot.calibration_target_spm / median_rpm))
                self.estimator.multiplier = factor
                self.estimator.reset()
                estimate = self.estimator.estimate(raw_rpm)
                changes.update(
                    multiplier=factor,
                    estimated_cadence_spm=estimate,
                    calibration_active=False,
                    calibration_result_factor=factor,
                    message=f"Calibrated at {factor:.3f}×",
                )
                if self.persist_settings:
                    save_calibration(
                        CalibrationProfile(
                            multiplier=factor,
                            target_spm=snapshot.calibration_target_spm,
                            speed_mph=snapshot.calibration_speed_mph,
                            raw_median_rpm=median_rpm,
                            sample_count=sample_count,
                        )
                    )
                self._calibration_samples.clear()
        self.store.update(**changes)


class DemoBackend:
    """Deterministic simulation used to validate UI and state transitions."""

    def __init__(self, controller: AppController) -> None:
        self.controller = controller
        self._sources: list[int] = []
        self._tick = 0
        self._packets = 0

    def _later(self, delay_ms: int, callback) -> None:
        source = GLib.timeout_add(delay_ms, callback)
        self._sources.append(source)

    def start(self) -> None:
        if self.controller.store.snapshot.running:
            return
        self._tick = 0
        self._packets = 0
        self.controller.estimator.reset()
        self.controller.store.update(
            running=True,
            sensor_status=SensorStatus.SCANNING,
            publisher_status=PublisherStatus.STARTING,
            sensor_detail="Scanning for CSC service 0x1816…",
            publisher_detail="Preparing virtual RSC footpod…",
            raw_cadence_rpm=None,
            estimated_cadence_spm=None,
            packet_count=0,
            last_packet_age_s=None,
            message="Starting bridge",
        )
        self._later(800, self._sensor_connected)
        self._later(1500, self._advertising)
        self._later(2400, self._zwift_connected)

    def _sensor_connected(self) -> bool:
        self.controller.store.update(
            sensor_status=SensorStatus.CONNECTED,
            sensor_name="Magene cadence sensor",
            sensor_detail="Connected · receiving crank events",
            message="Sensor connected",
        )
        return GLib.SOURCE_REMOVE

    def _advertising(self) -> bool:
        self.controller.store.update(
            publisher_status=PublisherStatus.ADVERTISING,
            publisher_detail="Advertising as RUN CADENCE BRIDGE",
            message="Waiting for Zwift",
        )
        return GLib.SOURCE_REMOVE

    def _zwift_connected(self) -> bool:
        self.controller.store.update(
            publisher_status=PublisherStatus.CONNECTED,
            publisher_detail="RSC client subscribed",
            message="Bridge active",
        )
        cadence_source = GLib.timeout_add(450, self._emit_cadence)
        self._sources.append(cadence_source)
        return GLib.SOURCE_REMOVE

    def _emit_cadence(self) -> bool:
        if not self.controller.store.snapshot.running:
            return GLib.SOURCE_REMOVE
        self._tick += 1
        self._packets += 1
        raw = 82.0 + 5.5 * math.sin(self._tick / 3.2) + 1.2 * math.sin(self._tick / 1.3)
        self.controller.receive_cadence(raw, self._packets)
        return GLib.SOURCE_CONTINUE

    def stop(self) -> None:
        for source in self._sources:
            GLib.source_remove(source)
        self._sources.clear()
        self.controller.estimator.reset()
        self.controller.store.update(
            running=False,
            sensor_status=SensorStatus.DISCONNECTED,
            publisher_status=PublisherStatus.STOPPED,
            sensor_detail="Ready to scan",
            publisher_detail="Virtual footpod is off",
            raw_cadence_rpm=None,
            estimated_cadence_spm=None,
            last_packet_age_s=None,
            message="Bridge stopped",
        )
