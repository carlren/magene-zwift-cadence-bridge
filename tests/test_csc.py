import struct
import unittest

from magene_bridge.backends.bluez import parse_csc_measurement


class CscParserTests(unittest.TestCase):
    def test_parses_crank_only_measurement(self) -> None:
        self.assertEqual(parse_csc_measurement(bytes.fromhex("02 57 00 84 7b")), (87, 31620))

    def test_skips_wheel_fields_when_both_are_present(self) -> None:
        payload = bytes([0x03]) + struct.pack("<IH", 123456, 900) + struct.pack("<HH", 42, 1200)
        self.assertEqual(parse_csc_measurement(payload), (42, 1200))

    def test_rejects_wheel_only_and_truncated_measurements(self) -> None:
        self.assertIsNone(parse_csc_measurement(bytes.fromhex("01 00 00 00 00 00 00")))
        self.assertIsNone(parse_csc_measurement(bytes.fromhex("02 01")))


if __name__ == "__main__":
    unittest.main()
