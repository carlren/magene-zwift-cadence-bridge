from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from ..model import PublisherStatus


DEFAULT_ENDPOINT = ""


def config_path() -> Path:
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "magene-zwift-bridge" / "vps.json"


def load_vps_config(path: Path | None = None) -> tuple[str, str]:
    target = path or config_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        endpoint = str(payload.get("endpoint") or DEFAULT_ENDPOINT).rstrip("/")
        token = str(payload["token"]).strip()
        if not token:
            raise ValueError("empty token")
        return endpoint, token
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_ENDPOINT, ""


class VpsRelay:
    """Publish current cadence to the authenticated VPS relay once per second."""

    def __init__(
        self,
        on_status: Callable[[PublisherStatus, str], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.on_status = on_status
        self.on_error = on_error
        self.endpoint, self.token = load_vps_config()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cadence_spm = 0.0
        self._sequence = 0
        self._last_status: tuple[PublisherStatus, str] | None = None
        self.started = False

    def start(self) -> None:
        if self.started:
            return
        if not self.endpoint or not self.token:
            self.on_error("VPS relay credentials are missing")
            return
        self.started = True
        self._stop.clear()
        self._set_status(PublisherStatus.STARTING, "Connecting to cadence API…")
        self._thread = threading.Thread(target=self._run, name="vps-cadence-relay", daemon=True)
        self._thread.start()

    def publish(self, cadence_spm: float) -> None:
        self._cadence_spm = max(0.0, min(255.0, cadence_spm))

    def _set_status(self, status: PublisherStatus, detail: str) -> None:
        value = (status, detail)
        if value != self._last_status:
            self._last_status = value
            self.on_status(status, detail)

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            self._sequence += 1
            body = json.dumps(
                {"cadence_spm": round(self._cadence_spm, 3), "sequence": self._sequence},
                separators=(",", ":"),
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{self.endpoint}/current",
                data=body,
                method="PUT",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "magene-zwift-bridge/0.2",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                failures = 0
                if payload.get("zwift_connected"):
                    self._set_status(PublisherStatus.CONNECTED, "Zwift subscribed through Fire tablet")
                elif payload.get("tablet_connected"):
                    self._set_status(PublisherStatus.ADVERTISING, "Fire online · waiting for Zwift")
                else:
                    self._set_status(PublisherStatus.STARTING, "Cadence uploaded · waiting for Fire tablet")
            except (OSError, urllib.error.URLError, json.JSONDecodeError):
                failures += 1
                if failures >= 2:
                    self._set_status(PublisherStatus.ERROR, "Cadence API is unreachable")
            self._stop.wait(1.0)

    def stop(self) -> None:
        self.started = False
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=6)
        self._thread = None
