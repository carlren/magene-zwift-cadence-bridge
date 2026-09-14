from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch

import app as relay


TOKEN = "test-token"


def request(path: str, method: str = "GET", body: dict | None = None, token: str | None = TOKEN):
    raw = json.dumps(body).encode() if body is not None else b""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
    }
    if token is not None:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    with patch.dict(os.environ, {"CADENCE_BRIDGE_TOKEN": TOKEN}):
        response = b"".join(relay.app(environ, start_response))
    return int(captured["status"].split()[0]), json.loads(response)


class RelayApiTests(unittest.TestCase):
    def setUp(self) -> None:
        relay.STORE = relay.StateStore()

    def test_health_is_public(self) -> None:
        self.assertEqual(request("/health", token=None), (200, {"ok": True}))

    def test_api_requires_token(self) -> None:
        self.assertEqual(request("/api/current", token=None)[0], 401)

    def test_cadence_round_trip_and_expiration(self) -> None:
        status, payload = request("/api/current", "PUT", {"cadence_spm": 120.25, "sequence": 4})
        self.assertEqual(status, 200)
        self.assertTrue(payload["cadence_fresh"])
        self.assertEqual(payload["cadence_spm"], 120.25)
        relay.STORE._state.source_received_at -= relay.CADENCE_TTL_SECONDS + 1
        status, payload = request("/api/current")
        self.assertEqual(status, 200)
        self.assertFalse(payload["cadence_fresh"])
        self.assertEqual(payload["cadence_spm"], 0.0)

    def test_client_heartbeat_reports_zwift(self) -> None:
        status, payload = request("/api/client", "PUT", {"zwift_connected": True})
        self.assertEqual(status, 200)
        self.assertTrue(payload["tablet_connected"])
        self.assertTrue(payload["zwift_connected"])

    def test_rejects_invalid_cadence(self) -> None:
        self.assertEqual(request("/api/current", "PUT", {"cadence_spm": 300})[0], 400)


if __name__ == "__main__":
    unittest.main()
