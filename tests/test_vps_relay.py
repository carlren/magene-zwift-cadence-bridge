import json
import tempfile
import unittest
from pathlib import Path

from magene_bridge.backends.vps_relay import DEFAULT_ENDPOINT, load_vps_config


class VpsRelayTests(unittest.TestCase):
    def test_loads_endpoint_and_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vps.json"
            path.write_text(json.dumps({"endpoint": "https://example.test/api/", "token": "secret"}))
            self.assertEqual(load_vps_config(path), ("https://example.test/api", "secret"))

    def test_missing_config_has_no_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(DEFAULT_ENDPOINT, "")
            self.assertEqual(load_vps_config(Path(directory) / "missing.json"), ("", ""))


if __name__ == "__main__":
    unittest.main()
