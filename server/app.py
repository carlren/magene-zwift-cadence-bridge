from __future__ import annotations

import hmac
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from typing import Callable, Iterable


CADENCE_TTL_SECONDS = 4.0
CLIENT_TTL_SECONDS = 5.0


@dataclass(slots=True)
class RelayState:
    cadence_spm: float = 0.0
    source_received_at: float = 0.0
    source_sequence: int = 0
    tablet_seen_at: float = 0.0
    zwift_connected: bool = False


class StateStore:
    def __init__(self) -> None:
        self._state = RelayState()
        self._lock = threading.Lock()

    def update_cadence(self, cadence_spm: float, sequence: int, now: float | None = None) -> None:
        with self._lock:
            self._state.cadence_spm = cadence_spm
            self._state.source_sequence = sequence
            self._state.source_received_at = now if now is not None else time.time()

    def update_client(self, zwift_connected: bool, now: float | None = None) -> None:
        with self._lock:
            self._state.tablet_seen_at = now if now is not None else time.time()
            self._state.zwift_connected = zwift_connected

    def snapshot(self, now: float | None = None) -> dict[str, object]:
        current_time = now if now is not None else time.time()
        with self._lock:
            state = RelayState(**asdict(self._state))
        source_age = max(0.0, current_time - state.source_received_at) if state.source_received_at else None
        tablet_age = max(0.0, current_time - state.tablet_seen_at) if state.tablet_seen_at else None
        cadence_fresh = source_age is not None and source_age <= CADENCE_TTL_SECONDS
        tablet_connected = tablet_age is not None and tablet_age <= CLIENT_TTL_SECONDS
        return {
            "cadence_spm": round(state.cadence_spm, 3) if cadence_fresh else 0.0,
            "cadence_fresh": cadence_fresh,
            "source_sequence": state.source_sequence,
            "source_age_ms": round(source_age * 1000) if source_age is not None else None,
            "tablet_connected": tablet_connected,
            "tablet_age_ms": round(tablet_age * 1000) if tablet_age is not None else None,
            "zwift_connected": bool(state.zwift_connected and tablet_connected),
        }


STORE = StateStore()


def _json_response(start_response: Callable, status: HTTPStatus, payload: dict) -> Iterable[bytes]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    start_response(
        f"{status.value} {status.phrase}",
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store, max-age=0"),
            ("X-Content-Type-Options", "nosniff"),
        ],
    )
    return [body]


def _authorized(environ: dict) -> bool:
    expected = os.environ.get("CADENCE_BRIDGE_TOKEN", "")
    provided = environ.get("HTTP_AUTHORIZATION", "")
    if not expected or not provided.startswith("Bearer "):
        return False
    return hmac.compare_digest(provided.removeprefix("Bearer ").strip(), expected)


def _read_json(environ: dict) -> dict:
    try:
        length = int(environ.get("CONTENT_LENGTH") or "0")
    except ValueError as error:
        raise ValueError("invalid content length") from error
    if length <= 0 or length > 4096:
        raise ValueError("invalid request size")
    raw = environ["wsgi.input"].read(length)
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON object required")
    return payload


def app(environ: dict, start_response: Callable) -> Iterable[bytes]:
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "/")

    if path == "/health" and method == "GET":
        return _json_response(start_response, HTTPStatus.OK, {"ok": True})

    if path not in {"/api/current", "/api/client"}:
        return _json_response(start_response, HTTPStatus.NOT_FOUND, {"error": "not found"})
    if not _authorized(environ):
        return _json_response(start_response, HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})

    if path == "/api/current" and method == "GET":
        return _json_response(start_response, HTTPStatus.OK, STORE.snapshot())

    if path == "/api/current" and method == "PUT":
        try:
            payload = _read_json(environ)
            cadence = float(payload["cadence_spm"])
            sequence = int(payload.get("sequence", 0))
            if not 0.0 <= cadence <= 255.0:
                raise ValueError("cadence_spm outside 0..255")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return _json_response(start_response, HTTPStatus.BAD_REQUEST, {"error": str(error)})
        STORE.update_cadence(cadence, sequence)
        return _json_response(start_response, HTTPStatus.OK, STORE.snapshot())

    if path == "/api/client" and method == "PUT":
        try:
            payload = _read_json(environ)
            connected = payload["zwift_connected"]
            if not isinstance(connected, bool):
                raise ValueError("zwift_connected must be boolean")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            return _json_response(start_response, HTTPStatus.BAD_REQUEST, {"error": str(error)})
        STORE.update_client(connected)
        return _json_response(start_response, HTTPStatus.OK, STORE.snapshot())

    return _json_response(start_response, HTTPStatus.METHOD_NOT_ALLOWED, {"error": "method not allowed"})
