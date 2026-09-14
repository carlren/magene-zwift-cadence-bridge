import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from magene_bridge.backends.bluez import adapter_usb_product, is_ub500


class AdapterTests(unittest.TestCase):
    def test_missing_sysfs_product_is_unknown(self) -> None:
        with patch("magene_bridge.backends.bluez.Path.read_text", side_effect=FileNotFoundError):
            self.assertIsNone(adapter_usb_product("/org/bluez/hci9"))

    def test_ub500_product_is_detected(self) -> None:
        with patch(
            "magene_bridge.backends.bluez.Path.read_text",
            return_value="DEVTYPE=usb_interface\nPRODUCT=2357/604/200\n",
        ):
            self.assertTrue(is_ub500("/org/bluez/hci0"))


if __name__ == "__main__":
    unittest.main()
