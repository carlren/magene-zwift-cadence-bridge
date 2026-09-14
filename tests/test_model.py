import unittest

from magene_bridge.model import BridgeStore, SensorStatus


class BridgeStoreTests(unittest.TestCase):
    def test_subscriber_receives_initial_and_updated_state(self) -> None:
        store = BridgeStore()
        observed = []
        unsubscribe = store.subscribe(observed.append)
        store.update(sensor_status=SensorStatus.CONNECTED, packet_count=3)

        self.assertEqual(len(observed), 2)
        self.assertEqual(observed[-1].sensor_status, SensorStatus.CONNECTED)
        self.assertEqual(observed[-1].packet_count, 3)

        unsubscribe()
        store.update(packet_count=4)
        self.assertEqual(len(observed), 2)


if __name__ == "__main__":
    unittest.main()
