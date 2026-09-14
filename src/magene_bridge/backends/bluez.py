from __future__ import annotations

import struct
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import dbus
import dbus.exceptions
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

from ..model import PublisherStatus, SensorStatus
from .vps_relay import VpsRelay

if TYPE_CHECKING:
    from ..controller import AppController


BLUEZ = "org.bluez"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"
PROPERTIES = "org.freedesktop.DBus.Properties"
ADAPTER = "org.bluez.Adapter1"
DEVICE = "org.bluez.Device1"
GATT_MANAGER = "org.bluez.GattManager1"
GATT_SERVICE = "org.bluez.GattService1"
GATT_CHARACTERISTIC = "org.bluez.GattCharacteristic1"
ADV_MANAGER = "org.bluez.LEAdvertisingManager1"
ADVERTISEMENT = "org.bluez.LEAdvertisement1"

CSC_SERVICE_UUID = "00001816-0000-1000-8000-00805f9b34fb"
CSC_MEASUREMENT_UUID = "00002a5b-0000-1000-8000-00805f9b34fb"
RSC_SERVICE_UUID = "00001814-0000-1000-8000-00805f9b34fb"
RSC_MEASUREMENT_UUID = "00002a53-0000-1000-8000-00805f9b34fb"
RSC_FEATURE_UUID = "00002a54-0000-1000-8000-00805f9b34fb"
SENSOR_LOCATION_UUID = "00002a5d-0000-1000-8000-00805f9b34fb"

APP_PATH = "/com/carlren/magene_bridge"
SERVICE_PATH = APP_PATH + "/service0"
MEASUREMENT_PATH = SERVICE_PATH + "/char0"
FEATURE_PATH = SERVICE_PATH + "/char1"
LOCATION_PATH = SERVICE_PATH + "/char2"
ADVERTISEMENT_PATH = APP_PATH + "/advertisement0"
VIRTUAL_DEVICE_NAME = "RUN CADENCE BRIDGE"
DIS_SERVICE_PATH = APP_PATH + "/service1"
DIS_MANUFACTURER_PATH = DIS_SERVICE_PATH + "/char0"
DIS_MODEL_PATH = DIS_SERVICE_PATH + "/char1"
DIS_FIRMWARE_PATH = DIS_SERVICE_PATH + "/char2"

LOG = logging.getLogger(__name__)


class InvalidArgs(dbus.exceptions.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.InvalidArgs"


class NotSupported(dbus.exceptions.DBusException):
    _dbus_error_name = "org.bluez.Error.NotSupported"


def _uuid_list(value: object) -> list[str]:
    return [str(item).lower() for item in value or []]


def find_adapter_paths(bus: dbus.SystemBus) -> list[str]:
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
    return sorted(
        str(path)
        for path, interfaces in manager.GetManagedObjects().items()
        if ADAPTER in interfaces and GATT_MANAGER in interfaces and ADV_MANAGER in interfaces
    )


def adapter_usb_product(adapter_path: str) -> str | None:
    adapter_name = adapter_path.rsplit("/", 1)[-1]
    uevent = Path("/sys/class/bluetooth") / adapter_name / "device" / "uevent"
    try:
        for line in uevent.read_text(encoding="utf-8").splitlines():
            if line.startswith("PRODUCT="):
                return line.removeprefix("PRODUCT=").lower()
    except OSError:
        pass
    return None


def is_ub500(adapter_path: str) -> bool:
    product = adapter_usb_product(adapter_path)
    return bool(product and product.startswith("2357/604/"))


def parse_csc_measurement(payload: bytes) -> tuple[int, int] | None:
    """Return cumulative crank revolutions and 1/1024-second event time."""
    if not payload:
        return None
    flags = payload[0]
    offset = 1
    if flags & 0x01:
        if len(payload) < offset + 6:
            return None
        offset += 6
    if not flags & 0x02 or len(payload) < offset + 4:
        return None
    return struct.unpack_from("<HH", payload, offset)


class BlueZBackend:
    """Coordinate a BlueZ CSC client and local RSC peripheral."""

    def __init__(self, controller: AppController) -> None:
        self.controller = controller
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        adapters = find_adapter_paths(self.bus)
        if not adapters:
            raise RuntimeError("No Bluetooth adapter with GATT support was found")

        # The existing UB500 is reliable as the central/client input but its
        # manufacturer documents it as central-only. Prefer it for the Magene
        # and reserve a different controller for the virtual peripheral.
        self.sensor_adapter_path = next((path for path in adapters if is_ub500(path)), adapters[0])
        separate_publishers = [
            path for path in adapters if path != self.sensor_adapter_path and not is_ub500(path)
        ]
        if separate_publishers:
            self.publisher_adapter_path: str | None = separate_publishers[0]
        elif len(adapters) == 1 and not is_ub500(adapters[0]):
            self.publisher_adapter_path = adapters[0]
        else:
            self.publisher_adapter_path = None
        self.sensor = SensorClient(
            self.bus,
            self.sensor_adapter_path,
            on_connected=self._sensor_connected,
            on_disconnected=self._sensor_disconnected,
            on_cadence=self._cadence,
            on_error=self._sensor_error,
        )
        self.publisher = RscPublisher(
            self.bus,
            on_status=self._publisher_status,
            on_error=self._publisher_error,
        )
        self.vps_relay = VpsRelay(
            on_status=self._publisher_status,
            on_error=self._publisher_error,
        ) if self.publisher_adapter_path is None else None

    def start(self) -> None:
        publisher_available = self.publisher_adapter_path is not None or self.vps_relay is not None
        self.controller.store.update(
            running=True,
            sensor_status=SensorStatus.SCANNING,
            publisher_status=PublisherStatus.STARTING if publisher_available else PublisherStatus.ERROR,
            sensor_detail="Scanning for CSC service 0x1816…",
            publisher_detail=(
                "Connecting to cadence API…"
                if self.vps_relay
                else "Preparing virtual RSC footpod…"
            ),
            raw_cadence_rpm=None,
            estimated_cadence_spm=None,
            packet_count=0,
            last_packet_age_s=None,
            message="Looking for Magene S3+",
        )
        # Keep the public virtual sensor independent from the upstream sensor.
        # This lets Zwift stay subscribed while the battery-powered Magene
        # sleeps, disconnects, or is being rediscovered.
        if self.publisher_adapter_path:
            self.publisher.start(self.publisher_adapter_path)
        if self.vps_relay:
            self.vps_relay.start()
        self.sensor.start()

    def stop(self) -> None:
        self.publisher.stop()
        if self.vps_relay:
            self.vps_relay.stop()
        self.sensor.stop()
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

    def _sensor_connected(self, name: str) -> None:
        changes: dict[str, object] = dict(
            sensor_status=SensorStatus.CONNECTED,
            sensor_name=name,
            sensor_detail="Connected · receiving crank events",
            message="Sensor connected",
        )
        if self.publisher_adapter_path and not self.publisher.started:
            changes.update(
                publisher_status=PublisherStatus.STARTING,
                publisher_detail="Preparing virtual RSC footpod…",
            )
        self.controller.store.update(**changes)
        if self.publisher_adapter_path:
            self.publisher.start(self.publisher_adapter_path)
        LOG.info("Sensor connected: %s", name)

    def _sensor_disconnected(self) -> None:
        if not self.controller.store.snapshot.running:
            return
        self.publisher.publish(0.0)
        if self.vps_relay:
            self.vps_relay.publish(0.0)
        self.controller.store.update(
            sensor_status=SensorStatus.SCANNING,
            sensor_detail="Sensor unavailable · check direct BLE connections",
            raw_cadence_rpm=None,
            estimated_cadence_spm=None,
            message="Reconnecting sensor",
        )
        LOG.warning(
            "Magene disconnected; ensure Zwift is paired to %s rather than the physical sensor",
            VIRTUAL_DEVICE_NAME,
        )

    def _cadence(self, raw_rpm: float, packet_count: int) -> None:
        self.controller.receive_cadence(raw_rpm, packet_count)
        cadence = self.controller.store.snapshot.estimated_cadence_spm or 0.0
        self.publisher.publish(cadence)
        if self.vps_relay:
            self.vps_relay.publish(cadence)

    def _sensor_error(self, message: str) -> None:
        self.controller.store.update(
            sensor_status=SensorStatus.ERROR,
            sensor_detail=message,
            message="Sensor error",
        )

    def _publisher_status(self, status: PublisherStatus, detail: str) -> None:
        self.controller.store.update(
            publisher_status=status,
            publisher_detail=detail,
            message="Bridge active" if status is PublisherStatus.CONNECTED else self.controller.store.snapshot.message,
        )

    def _publisher_error(self, message: str) -> None:
        self.controller.store.update(
            publisher_status=PublisherStatus.ERROR,
            publisher_detail=message,
            message="Virtual footpod error",
        )


class SensorClient:
    def __init__(
        self,
        bus: dbus.SystemBus,
        adapter_path: str,
        on_connected: Callable[[str], None],
        on_disconnected: Callable[[], None],
        on_cadence: Callable[[float, int], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.bus = bus
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.on_cadence = on_cadence
        self.on_error = on_error
        self.adapter_path = adapter_path
        self.adapter = dbus.Interface(self.bus.get_object(BLUEZ, self.adapter_path), ADAPTER)
        self.running = False
        self.device_path: str | None = None
        self.characteristic_path: str | None = None
        self._last_revolutions: int | None = None
        self._last_event_time: int | None = None
        self._packet_count = 0
        self._zero_timer: int | None = None
        self._signal_matches: list[object] = []

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._signal_matches.append(
            self.bus.add_signal_receiver(
                self._interfaces_added,
                dbus_interface=OBJECT_MANAGER,
                signal_name="InterfacesAdded",
            )
        )
        self._signal_matches.append(
            self.bus.add_signal_receiver(
                self._properties_changed,
                dbus_interface=PROPERTIES,
                signal_name="PropertiesChanged",
                path_keyword="path",
            )
        )
        self._find_existing_or_scan()

    def _find_existing_or_scan(self) -> None:
        manager = dbus.Interface(self.bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
        candidates: list[tuple[int, str, dict]] = []
        for path, interfaces in manager.GetManagedObjects().items():
            props = interfaces.get(DEVICE)
            if props and CSC_SERVICE_UUID in _uuid_list(props.get("UUIDs")):
                signal_strength = int(props.get("RSSI", -127))
                candidates.append((-signal_strength, str(path), props))
        if candidates:
            _, path, props = sorted(candidates, key=lambda item: item[0])[0]
            self._connect(path, props)
            return
        self._start_scan()

    def _start_scan(self) -> None:
        if not self.running:
            return
        try:
            self.adapter.SetDiscoveryFilter(
                dbus.Dictionary({"UUIDs": dbus.Array([CSC_SERVICE_UUID], signature="s"), "Transport": "le"}, signature="sv")
            )
            self.adapter.StartDiscovery(
                reply_handler=lambda: None,
                error_handler=lambda error: self._scan_error(error),
            )
        except dbus.exceptions.DBusException as error:
            if "InProgress" not in error.get_dbus_name():
                self.on_error(f"Could not start Bluetooth scan: {error.get_dbus_message()}")

    def _scan_error(self, error: dbus.exceptions.DBusException) -> None:
        if "InProgress" not in error.get_dbus_name():
            self.on_error(f"Could not start Bluetooth scan: {error.get_dbus_message()}")

    def _stop_scan(self) -> None:
        try:
            self.adapter.StopDiscovery()
        except dbus.exceptions.DBusException:
            pass

    def _interfaces_added(self, path: dbus.ObjectPath, interfaces: dict) -> None:
        props = interfaces.get(DEVICE)
        if self.running and not self.device_path and props and CSC_SERVICE_UUID in _uuid_list(props.get("UUIDs")):
            self._connect(str(path), props)

    def _connect(self, path: str, props: dict) -> None:
        if self.device_path:
            return
        self.device_path = path
        self._stop_scan()
        device = dbus.Interface(self.bus.get_object(BLUEZ, path), DEVICE)
        if bool(props.get("Connected")):
            self._wait_for_services(path, props)
            return
        device.Connect(
            reply_handler=lambda: self._connected_reply(path),
            error_handler=lambda error: self._connect_error(path, error),
        )

    def _connected_reply(self, path: str) -> None:
        properties = dbus.Interface(self.bus.get_object(BLUEZ, path), PROPERTIES)
        try:
            props = properties.GetAll(DEVICE)
        except dbus.exceptions.DBusException as error:
            self._connect_error(path, error)
            return
        self._wait_for_services(path, props)

    def _wait_for_services(self, path: str, props: dict) -> None:
        if bool(props.get("ServicesResolved")):
            self._attach_measurement(path, props)

    def _connect_error(self, path: str, error: dbus.exceptions.DBusException) -> None:
        if not self.running:
            return
        self.device_path = None
        LOG.warning("Magene connection failed: %s", error.get_dbus_message())
        self.on_error(f"Connection failed: {error.get_dbus_message()}")
        GLib.timeout_add(1800, self._retry_scan)

    def _retry_scan(self) -> bool:
        if self.running and not self.device_path:
            self._start_scan()
        return GLib.SOURCE_REMOVE

    def _attach_measurement(self, path: str, device_props: dict | None = None) -> None:
        manager = dbus.Interface(self.bus.get_object(BLUEZ, "/"), OBJECT_MANAGER)
        for object_path, interfaces in manager.GetManagedObjects().items():
            props = interfaces.get(GATT_CHARACTERISTIC)
            if str(object_path).startswith(path + "/") and props and str(props.get("UUID", "")).lower() == CSC_MEASUREMENT_UUID:
                self.characteristic_path = str(object_path)
                characteristic = dbus.Interface(self.bus.get_object(BLUEZ, object_path), GATT_CHARACTERISTIC)
                characteristic.StartNotify(
                    reply_handler=lambda: self._notify_started(path, device_props),
                    error_handler=lambda error: self.on_error(f"Could not subscribe to cadence: {error.get_dbus_message()}"),
                )
                return
        self.on_error("Cadence characteristic was not found")

    def _notify_started(self, path: str, device_props: dict | None) -> None:
        name = "Magene S3+"
        if device_props:
            name = str(device_props.get("Name") or device_props.get("Alias") or name)
        self.on_connected(name)

    def _properties_changed(self, interface: str, changed: dict, _invalidated: list, path: str) -> None:
        path = str(path)
        if interface == DEVICE and self.running and not self.device_path:
            if "UUIDs" in changed and CSC_SERVICE_UUID in _uuid_list(changed["UUIDs"]):
                props = dbus.Interface(self.bus.get_object(BLUEZ, path), PROPERTIES).GetAll(DEVICE)
                self._connect(path, props)
            return
        if interface == DEVICE and path == self.device_path:
            if "Connected" in changed:
                LOG.info("Magene Connected property changed to %s", bool(changed["Connected"]))
            if "ServicesResolved" in changed and bool(changed["ServicesResolved"]) and not self.characteristic_path:
                props = dbus.Interface(self.bus.get_object(BLUEZ, path), PROPERTIES).GetAll(DEVICE)
                self._attach_measurement(path, props)
            if "Connected" in changed and not bool(changed["Connected"]):
                self.characteristic_path = None
                self.device_path = None
                self._last_revolutions = None
                self._last_event_time = None
                self.on_disconnected()
                self._start_scan()
            return
        if interface == GATT_CHARACTERISTIC and path == self.characteristic_path and "Value" in changed:
            self._consume(bytes(changed["Value"]))

    def _consume(self, payload: bytes) -> None:
        measurement = parse_csc_measurement(payload)
        if measurement is None:
            return
        revolutions, event_time = measurement
        self._packet_count += 1
        if self._last_revolutions is not None and self._last_event_time is not None:
            delta_revolutions = (revolutions - self._last_revolutions) & 0xFFFF
            delta_ticks = (event_time - self._last_event_time) & 0xFFFF
            if delta_revolutions and delta_ticks:
                rpm = delta_revolutions * 60.0 * 1024.0 / delta_ticks
                if 0.0 <= rpm <= 250.0:
                    self.on_cadence(rpm, self._packet_count)
                    self._arm_zero_timer()
        self._last_revolutions = revolutions
        self._last_event_time = event_time

    def _arm_zero_timer(self) -> None:
        if self._zero_timer:
            source = GLib.MainContext.default().find_source_by_id(self._zero_timer)
            if source:
                source.destroy()
        self._zero_timer = GLib.timeout_add(3000, self._set_zero)

    def _set_zero(self) -> bool:
        self._zero_timer = None
        if self.running and self.device_path:
            self.on_cadence(0.0, self._packet_count)
        return GLib.SOURCE_REMOVE

    def stop(self) -> None:
        self.running = False
        self._stop_scan()
        if self._zero_timer:
            source = GLib.MainContext.default().find_source_by_id(self._zero_timer)
            if source:
                source.destroy()
            self._zero_timer = None
        if self.characteristic_path:
            try:
                dbus.Interface(
                    self.bus.get_object(BLUEZ, self.characteristic_path), GATT_CHARACTERISTIC
                ).StopNotify()
            except dbus.exceptions.DBusException:
                pass
        if self.device_path:
            try:
                dbus.Interface(self.bus.get_object(BLUEZ, self.device_path), DEVICE).Disconnect()
            except dbus.exceptions.DBusException:
                pass
        for match in self._signal_matches:
            match.remove()
        self._signal_matches.clear()
        self.device_path = None
        self.characteristic_path = None


class GattApplication(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus, on_notify: Callable[[bool], None]) -> None:
        super().__init__(bus, APP_PATH)
        self.service = RscService(bus, on_notify)
        self.device_info = DeviceInfoService(bus)

    @dbus.service.method(OBJECT_MANAGER, out_signature="a{oa{sa{sv}}}")
    def GetManagedObjects(self) -> dict:
        return {
            dbus.ObjectPath(SERVICE_PATH): self.service.properties(),
            dbus.ObjectPath(MEASUREMENT_PATH): self.service.measurement.properties(),
            dbus.ObjectPath(FEATURE_PATH): self.service.feature.properties(),
            dbus.ObjectPath(LOCATION_PATH): self.service.location.properties(),
            dbus.ObjectPath(DIS_SERVICE_PATH): self.device_info.properties(),
            dbus.ObjectPath(DIS_MANUFACTURER_PATH): self.device_info.manufacturer.properties(),
            dbus.ObjectPath(DIS_MODEL_PATH): self.device_info.model.properties(),
            dbus.ObjectPath(DIS_FIRMWARE_PATH): self.device_info.firmware.properties(),
        }


class RscService(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus, on_notify: Callable[[bool], None]) -> None:
        super().__init__(bus, SERVICE_PATH)
        self.measurement = RscMeasurement(bus, on_notify)
        self.feature = ReadOnlyCharacteristic(bus, FEATURE_PATH, RSC_FEATURE_UUID, b"\x00\x00", SERVICE_PATH)
        self.location = ReadOnlyCharacteristic(bus, LOCATION_PATH, SENSOR_LOCATION_UUID, b"\x01", SERVICE_PATH)

    def properties(self) -> dict:
        return {
            GATT_SERVICE: {
                "UUID": RSC_SERVICE_UUID,
                "Primary": dbus.Boolean(True),
            }
        }

    @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str) -> dict:
        if interface != GATT_SERVICE:
            raise InvalidArgs()
        return self.properties()[GATT_SERVICE]


class BaseCharacteristic(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus, path: str, uuid: str, flags: list[str], service_path: str) -> None:
        super().__init__(bus, path)
        self.uuid = uuid
        self.flags = flags
        self.service_path = service_path

    def properties(self) -> dict:
        return {
            GATT_CHARACTERISTIC: {
                "Service": dbus.ObjectPath(self.service_path),
                "UUID": self.uuid,
                "Flags": dbus.Array(self.flags, signature="s"),
            }
        }

    @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str) -> dict:
        if interface != GATT_CHARACTERISTIC:
            raise InvalidArgs()
        return self.properties()[GATT_CHARACTERISTIC]

    @dbus.service.method(GATT_CHARACTERISTIC, in_signature="aya{sv}")
    def WriteValue(self, _value: list, _options: dict) -> None:
        raise NotSupported()

    @dbus.service.signal(PROPERTIES, signature="sa{sv}as")
    def PropertiesChanged(self, _interface: str, _changed: dict, _invalidated: list) -> None:
        pass


class ReadOnlyCharacteristic(BaseCharacteristic):
    def __init__(self, bus: dbus.SystemBus, path: str, uuid: str, value: bytes, service_path: str) -> None:
        super().__init__(bus, path, uuid, ["read"], service_path)
        self.value = value

    @dbus.service.method(GATT_CHARACTERISTIC, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, _options: dict) -> dbus.Array:
        return dbus.Array([dbus.Byte(byte) for byte in self.value], signature="y")


class RscMeasurement(BaseCharacteristic):
    def __init__(self, bus: dbus.SystemBus, on_notify: Callable[[bool], None]) -> None:
        super().__init__(bus, MEASUREMENT_PATH, RSC_MEASUREMENT_UUID, ["read", "notify"], SERVICE_PATH)
        self.notifying = False
        self.on_notify = on_notify
        self.value = b"\x00\x00\x00\x00"

    @dbus.service.method(GATT_CHARACTERISTIC)
    def StartNotify(self) -> None:
        if self.notifying:
            return
        self.notifying = True
        LOG.info("RSC client subscribed to cadence notifications")
        self.on_notify(True)
        self._emit_value()

    @dbus.service.method(GATT_CHARACTERISTIC)
    def StopNotify(self) -> None:
        if not self.notifying:
            return
        self.notifying = False
        LOG.info("RSC client stopped cadence notifications")
        self.on_notify(False)

    @dbus.service.method(GATT_CHARACTERISTIC, in_signature="a{sv}", out_signature="ay")
    def ReadValue(self, _options: dict) -> dbus.Array:
        return dbus.Array([dbus.Byte(byte) for byte in self.value], signature="y")

    def publish(self, cadence_spm: float) -> None:
        cadence = max(0, min(255, round(cadence_spm)))
        self.value = struct.pack("<BHB", 0, 0, cadence)
        if self.notifying:
            self._emit_value()

    def _emit_value(self) -> None:
        self.PropertiesChanged(
            GATT_CHARACTERISTIC,
            {"Value": dbus.Array([dbus.Byte(byte) for byte in self.value], signature="y")},
            [],
        )

    def notify_current(self) -> None:
        if self.notifying:
            self._emit_value()


class DeviceInfoService(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus) -> None:
        super().__init__(bus, DIS_SERVICE_PATH)
        self.manufacturer = ReadOnlyCharacteristic(
            bus, DIS_MANUFACTURER_PATH, "00002a29-0000-1000-8000-00805f9b34fb", b"Cadence Bridge", DIS_SERVICE_PATH
        )
        self.model = ReadOnlyCharacteristic(
            bus, DIS_MODEL_PATH, "00002a24-0000-1000-8000-00805f9b34fb", b"Virtual RSC Footpod", DIS_SERVICE_PATH
        )
        self.firmware = ReadOnlyCharacteristic(
            bus, DIS_FIRMWARE_PATH, "00002a26-0000-1000-8000-00805f9b34fb", b"0.1.1", DIS_SERVICE_PATH
        )

    def properties(self) -> dict:
        return {
            GATT_SERVICE: {
                "UUID": "0000180a-0000-1000-8000-00805f9b34fb",
                "Primary": dbus.Boolean(True),
            }
        }

    @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str) -> dict:
        if interface != GATT_SERVICE:
            raise InvalidArgs()
        return self.properties()[GATT_SERVICE]


class RscAdvertisement(dbus.service.Object):
    def __init__(self, bus: dbus.SystemBus) -> None:
        super().__init__(bus, ADVERTISEMENT_PATH)

    @dbus.service.method(PROPERTIES, in_signature="s", out_signature="a{sv}")
    def GetAll(self, interface: str) -> dict:
        if interface != ADVERTISEMENT:
            raise InvalidArgs()
        return {
            "Type": "peripheral",
            "ServiceUUIDs": dbus.Array([RSC_SERVICE_UUID], signature="s"),
            "LocalName": VIRTUAL_DEVICE_NAME,
            "Discoverable": dbus.Boolean(True),
            "Appearance": dbus.UInt16(0x0440),
            "Includes": dbus.Array(["tx-power"], signature="s"),
        }

    @dbus.service.method(ADVERTISEMENT)
    def Release(self) -> None:
        return


class RscPublisher:
    def __init__(
        self,
        bus: dbus.SystemBus,
        on_status: Callable[[PublisherStatus, str], None],
        on_error: Callable[[str], None],
    ) -> None:
        self.bus = bus
        self.on_status = on_status
        self.on_error = on_error
        self.adapter_path: str | None = None
        self.app: GattApplication | None = None
        self.advertisement: RscAdvertisement | None = None
        self.gatt_manager = None
        self.adv_manager = None
        self.app_registered = False
        self.adv_registered = False
        self.heartbeat_source: int | None = None

    @property
    def started(self) -> bool:
        return self.app is not None

    def start(self, adapter_path: str) -> None:
        if self.app:
            return
        self.adapter_path = adapter_path
        self.app = GattApplication(self.bus, self._notify_changed)
        self.advertisement = RscAdvertisement(self.bus)
        adapter_object = self.bus.get_object(BLUEZ, adapter_path)
        self.gatt_manager = dbus.Interface(adapter_object, GATT_MANAGER)
        self.adv_manager = dbus.Interface(adapter_object, ADV_MANAGER)
        self.gatt_manager.RegisterApplication(
            dbus.ObjectPath(APP_PATH),
            {},
            reply_handler=self._app_ready,
            error_handler=lambda error: self.on_error(f"GATT registration failed: {error.get_dbus_message()}"),
        )

    def _app_ready(self) -> None:
        self.app_registered = True
        assert self.adv_manager is not None
        self.adv_manager.RegisterAdvertisement(
            dbus.ObjectPath(ADVERTISEMENT_PATH),
            {},
            reply_handler=self._advertising,
            error_handler=lambda error: self.on_error(f"Advertising failed: {error.get_dbus_message()}"),
        )

    def _advertising(self) -> None:
        self.adv_registered = True
        if self.heartbeat_source is None:
            self.heartbeat_source = GLib.timeout_add(1000, self._heartbeat)
        LOG.info("Virtual RSC footpod is advertising")
        self.on_status(PublisherStatus.ADVERTISING, f"Advertising as {VIRTUAL_DEVICE_NAME}")

    def _notify_changed(self, connected: bool) -> None:
        if connected:
            self.on_status(PublisherStatus.CONNECTED, "RSC client subscribed")
        else:
            self.on_status(PublisherStatus.ADVERTISING, "Waiting for Zwift to reconnect")

    def _heartbeat(self) -> bool:
        if not self.app:
            self.heartbeat_source = None
            return GLib.SOURCE_REMOVE
        self.app.service.measurement.notify_current()
        return GLib.SOURCE_CONTINUE

    def publish(self, cadence_spm: float) -> None:
        if self.app:
            self.app.service.measurement.publish(cadence_spm)

    def stop(self) -> None:
        if self.heartbeat_source is not None:
            source = GLib.MainContext.default().find_source_by_id(self.heartbeat_source)
            if source:
                source.destroy()
            self.heartbeat_source = None
        if self.adv_registered and self.adv_manager:
            try:
                self.adv_manager.UnregisterAdvertisement(dbus.ObjectPath(ADVERTISEMENT_PATH))
            except dbus.exceptions.DBusException:
                pass
        if self.app_registered and self.gatt_manager:
            try:
                self.gatt_manager.UnregisterApplication(dbus.ObjectPath(APP_PATH))
            except dbus.exceptions.DBusException:
                pass
        if self.advertisement:
            self.advertisement.remove_from_connection()
        if self.app:
            self.app.service.measurement.remove_from_connection()
            self.app.service.feature.remove_from_connection()
            self.app.service.location.remove_from_connection()
            self.app.service.remove_from_connection()
            self.app.device_info.manufacturer.remove_from_connection()
            self.app.device_info.model.remove_from_connection()
            self.app.device_info.firmware.remove_from_connection()
            self.app.device_info.remove_from_connection()
            self.app.remove_from_connection()
        self.adapter_path = None
        self.app = None
        self.advertisement = None
        self.gatt_manager = None
        self.adv_manager = None
        self.app_registered = False
        self.adv_registered = False
