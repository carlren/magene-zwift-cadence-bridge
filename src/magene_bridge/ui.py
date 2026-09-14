from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from .controller import AppController
from .model import BridgeSnapshot, PublisherStatus, SensorStatus


CSS = """
window { background: #0b1118; }
.app-shell { padding: 22px 28px 28px; }
.hero-title { font-size: 30px; font-weight: 800; color: #f2f7fa; }
.hero-subtitle { font-size: 14px; color: #8fa3b2; }
.panel {
  background: #111b24;
  border: 1px solid rgba(150, 185, 204, 0.12);
  border-radius: 18px;
  padding: 22px;
}
.status-card {
  background: #111b24;
  border: 1px solid rgba(150, 185, 204, 0.12);
  border-radius: 16px;
  padding: 16px;
}
.card-kicker { color: #6f8798; font-size: 11px; font-weight: 700; letter-spacing: 1.1px; }
.status-title { color: #edf5f8; font-size: 17px; font-weight: 700; }
.status-detail { color: #8095a4; font-size: 12px; }
.cadence-number { color: #f5fbfd; font-size: 64px; font-weight: 800; font-feature-settings: "tnum"; }
.cadence-unit { color: #67e2c2; font-size: 13px; font-weight: 800; letter-spacing: 1.4px; }
.gauge-shell {
  background: radial-gradient(circle, #14232d 0%, #0e171f 72%);
  border: 12px solid #263945;
  border-radius: 999px;
  padding: 54px;
}
.cadence-track trough {
  background: #263945;
  border-radius: 999px;
  min-height: 8px;
}
.cadence-track progress {
  background: #67e2c2;
  border-radius: 999px;
  min-height: 8px;
}
.metric-label { color: #718797; font-size: 11px; font-weight: 700; }
.metric-value { color: #dce9ee; font-size: 16px; font-weight: 700; font-feature-settings: "tnum"; }
.flow-node {
  background: rgba(75, 111, 132, 0.12);
  border: 1px solid rgba(125, 163, 184, 0.15);
  border-radius: 12px;
  padding: 11px 14px;
}
.flow-node-title { color: #dce8ec; font-size: 13px; font-weight: 700; }
.flow-node-detail { color: #6f8798; font-size: 10px; }
.flow-arrow { color: #3f5b6d; font-size: 20px; }
.live-pill {
  background: rgba(103, 226, 194, 0.11);
  border: 1px solid rgba(103, 226, 194, 0.22);
  border-radius: 999px;
  padding: 7px 12px;
  color: #67e2c2;
  font-size: 11px;
  font-weight: 800;
}
.idle-pill {
  background: rgba(131, 151, 164, 0.10);
  border: 1px solid rgba(131, 151, 164, 0.16);
  border-radius: 999px;
  padding: 7px 12px;
  color: #8da0ad;
  font-size: 11px;
  font-weight: 800;
}
.primary-action {
  background: #67e2c2;
  color: #06271f;
  font-weight: 800;
  border-radius: 10px;
  padding: 6px 18px;
}
.danger-action { background: #263845; color: #dce7ec; font-weight: 700; }
.separator-line { background: rgba(150, 185, 204, 0.10); min-height: 1px; }
.section-heading { color: #e6f0f4; font-size: 15px; font-weight: 700; }
.section-copy { color: #78909f; font-size: 12px; }
.status-dot { font-size: 17px; }
.dot-idle { color: #607a89; }
.dot-pending { color: #f9b851; }
.dot-connected { color: #67e2c2; }
.dot-error { color: #ff5e6b; }
spinbutton { background: #0d151c; border-radius: 9px; }
scale trough { background: #263744; min-height: 5px; }
scale highlight { background: #67e2c2; }
"""


class StatusDot(Gtk.Label):
    def __init__(self) -> None:
        super().__init__(label="●")
        self.status = "disconnected"
        self.add_css_class("status-dot")
        self.add_css_class("dot-idle")

    def set_status(self, status: str) -> None:
        self.status = status
        for css_class in ("dot-idle", "dot-pending", "dot-connected", "dot-error"):
            self.remove_css_class(css_class)
        if status == "connected":
            css_class = "dot-connected"
        elif status in {"scanning", "connecting", "starting", "advertising"}:
            css_class = "dot-pending"
        elif status == "error":
            css_class = "dot-error"
        else:
            css_class = "dot-idle"
        self.add_css_class(css_class)


class StatusCard(Gtk.Box):
    def __init__(self, kicker: str, icon_name: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=11)
        self.add_css_class("status-card")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(18)
        header.append(icon)
        kicker_label = Gtk.Label(label=kicker, xalign=0)
        kicker_label.add_css_class("card-kicker")
        kicker_label.set_hexpand(True)
        header.append(kicker_label)
        self.dot = StatusDot()
        header.append(self.dot)
        self.append(header)

        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class("status-title")
        self.append(self.title)
        self.detail = Gtk.Label(xalign=0, ellipsize=3)
        self.detail.add_css_class("status-detail")
        self.append(self.detail)

    def update(self, title: str, detail: str, status: str) -> None:
        self.title.set_text(title)
        self.detail.set_text(detail)
        self.dot.set_status(status)


class CadenceGauge(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._cadence: float | None = None
        self.set_size_request(300, 300)
        self.add_css_class("gauge-shell")
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        center.set_halign(Gtk.Align.FILL)
        center.set_valign(Gtk.Align.CENTER)
        self.number = Gtk.Label(label="—")
        self.number.add_css_class("cadence-number")
        center.append(self.number)
        unit = Gtk.Label(label="STEPS / MIN")
        unit.add_css_class("cadence-unit")
        center.append(unit)
        self.append(center)

        self.progress = Gtk.ProgressBar(fraction=0.0)
        self.progress.add_css_class("cadence-track")
        self.progress.set_hexpand(True)
        self.append(self.progress)

    def set_cadence(self, cadence: float | None) -> None:
        self._cadence = cadence
        self.number.set_text("—" if cadence is None else f"{cadence:.0f}")
        fraction = 0.0 if cadence is None else min(1.0, max(0.0, cadence / 220.0))
        self.progress.set_fraction(fraction)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, controller: AppController) -> None:
        super().__init__(application=app, title="Cadence Bridge")
        self.controller = controller
        self.set_default_size(1040, 760)
        self.set_size_request(900, 650)
        self.connect("close-request", self._close_requested)

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toast_overlay.set_child(root)

        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Cadence Bridge"))
        root.append(header)

        clamp = Adw.Clamp(maximum_size=1050, tightening_threshold=900)
        clamp.set_hexpand(True)
        clamp.set_vexpand(True)
        root.append(clamp)

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        shell.add_css_class("app-shell")
        clamp.set_child(shell)

        shell.append(self._build_heading())
        shell.append(self._build_flow())

        content = Gtk.Grid(column_spacing=18, row_spacing=18)
        content.set_hexpand(True)
        content.set_vexpand(True)
        shell.append(content)

        cadence_panel = self._build_cadence_panel()
        cadence_panel.set_hexpand(True)
        cadence_panel.set_vexpand(True)
        content.attach(cadence_panel, 0, 0, 1, 2)

        status_stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        status_stack.set_size_request(365, -1)
        self.sensor_card = StatusCard("MAGENE SENSOR", "bluetooth-active-symbolic")
        self.publisher_card = StatusCard("ZWIFT LINK", "network-transmit-receive-symbolic")
        status_stack.append(self.sensor_card)
        status_stack.append(self.publisher_card)
        content.attach(status_stack, 1, 0, 1, 1)
        content.attach(self._build_estimator_panel(), 1, 1, 1, 1)

        self.controller.store.subscribe(self._render)

    def _close_requested(self, _window: Gtk.Window) -> bool:
        if self.controller.store.snapshot.running:
            self.controller.backend.stop()
        return False

    def _build_heading(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        titles.set_hexpand(True)
        title = Gtk.Label(label="Turn motion into running cadence", xalign=0)
        title.add_css_class("hero-title")
        subtitle = Gtk.Label(
            label="Magene crank events in · Zwift-compatible step cadence out",
            xalign=0,
        )
        subtitle.add_css_class("hero-subtitle")
        titles.append(title)
        titles.append(subtitle)
        row.append(titles)

        self.mode_pill = Gtk.Label(label="IDLE")
        self.mode_pill.add_css_class("idle-pill")
        row.append(self.mode_pill)

        self.action_button = Gtk.Button(label="Start bridge")
        self.action_button.add_css_class("primary-action")
        self.action_button.connect("clicked", lambda _button: self.controller.toggle_bridge())
        row.append(self.action_button)
        return row

    def _flow_node(self, title: str, detail: str, icon_name: str) -> Gtk.Widget:
        node = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        node.add_css_class("flow-node")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(18)
        node.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.add_css_class("flow-node-title")
        detail_label = Gtk.Label(label=detail, xalign=0)
        detail_label.add_css_class("flow-node-detail")
        text.append(title_label)
        text.append(detail_label)
        node.append(text)
        return node

    def _build_flow(self) -> Gtk.Widget:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.append(self._flow_node("Magene S3+", "BLE · CSC 0x1816", "bluetooth-symbolic"))
        arrow = Gtk.Label(label="›")
        arrow.add_css_class("flow-arrow")
        row.append(arrow)
        row.append(self._flow_node("Cadence estimator", "RPM → steps/min", "view-refresh-symbolic"))
        arrow = Gtk.Label(label="›")
        arrow.add_css_class("flow-arrow")
        row.append(arrow)
        row.append(self._flow_node("Virtual footpod", "BLE · RSC 0x1814", "media-playlist-shuffle-symbolic"))
        return row

    def _metric(self, label: str) -> tuple[Gtk.Widget, Gtk.Label]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        caption = Gtk.Label(label=label, xalign=0)
        caption.add_css_class("metric-label")
        value = Gtk.Label(label="—", xalign=0)
        value.add_css_class("metric-value")
        box.append(caption)
        box.append(value)
        return box, value

    def _build_cadence_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        panel.add_css_class("panel")
        heading = Gtk.Label(label="ESTIMATED RUN CADENCE", xalign=0)
        heading.add_css_class("card-kicker")
        panel.append(heading)
        self.gauge = CadenceGauge()
        self.gauge.set_halign(Gtk.Align.CENTER)
        self.gauge.set_valign(Gtk.Align.CENTER)
        self.gauge.set_vexpand(True)
        panel.append(self.gauge)

        line = Gtk.Box()
        line.add_css_class("separator-line")
        panel.append(line)

        metrics = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, homogeneous=True, spacing=18)
        metric, self.raw_value = self._metric("RAW SENSOR")
        metrics.append(metric)
        metric, self.packet_value = self._metric("PACKETS")
        metrics.append(metric)
        metric, self.age_value = self._metric("LAST UPDATE")
        metrics.append(metric)
        panel.append(metrics)
        return panel

    def _build_estimator_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=13)
        panel.add_css_class("panel")
        heading = Gtk.Label(label="Cadence estimation", xalign=0)
        heading.add_css_class("section-heading")
        panel.append(heading)
        copy = Gtk.Label(
            label="Convert one crank revolution into a full left/right gait cycle. Fine-tune this after a measured calibration run.",
            xalign=0,
            wrap=True,
        )
        copy.add_css_class("section-copy")
        panel.append(copy)

        control_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        control_row.append(Gtk.Label(label="Step multiplier", xalign=0, hexpand=True))
        adjustment = Gtk.Adjustment(
            value=self.controller.store.snapshot.multiplier,
            lower=0.5,
            upper=3.0,
            step_increment=0.01,
            page_increment=0.1,
        )
        self.multiplier_spin = Gtk.SpinButton(adjustment=adjustment, digits=2, numeric=True)
        self.multiplier_spin.set_width_chars(5)
        self.multiplier_handler = self.multiplier_spin.connect("value-changed", self._multiplier_changed)
        control_row.append(self.multiplier_spin)
        panel.append(control_row)

        self.multiplier_scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment)
        self.multiplier_scale.set_draw_value(False)
        panel.append(self.multiplier_scale)

        factor = self.controller.store.snapshot.multiplier
        self.estimator_note = Gtk.Label(label=f"Calibrated model · {factor:.3f} steps per revolution", xalign=0)
        self.estimator_note.add_css_class("status-detail")
        panel.append(self.estimator_note)

        line = Gtk.Box()
        line.add_css_class("separator-line")
        panel.append(line)

        calibration_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        calibration_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        calibration_text.set_hexpand(True)
        calibration_title = Gtk.Label(label="Measured calibration", xalign=0)
        calibration_title.add_css_class("section-heading")
        snapshot = self.controller.store.snapshot
        self.calibration_detail = Gtk.Label(
            label=f"Target · {snapshot.calibration_target_spm:.0f} SPM at {snapshot.calibration_speed_mph:.1f} mph",
            xalign=0,
        )
        self.calibration_detail.add_css_class("status-detail")
        calibration_text.append(calibration_title)
        calibration_text.append(self.calibration_detail)
        calibration_row.append(calibration_text)
        self.calibration_button = Gtk.Button(label="Calibrate now")
        self.calibration_button.connect("clicked", self._calibration_clicked)
        calibration_row.append(self.calibration_button)
        panel.append(calibration_row)
        return panel

    def _multiplier_changed(self, spin: Gtk.SpinButton) -> None:
        value = spin.get_value()
        self.controller.set_multiplier(value)
        self.estimator_note.set_text(f"Initial model · {value:.2f} steps per revolution")

    def _calibration_clicked(self, _button: Gtk.Button) -> None:
        if self.controller.store.snapshot.calibration_active:
            self.controller.cancel_calibration()
        else:
            self.controller.begin_calibration()

    def _render(self, snapshot: BridgeSnapshot) -> None:
        GLib.idle_add(self._render_now, snapshot)

    def _render_now(self, snapshot: BridgeSnapshot) -> bool:
        sensor_titles = {
            SensorStatus.DISCONNECTED: "Sensor disconnected",
            SensorStatus.SCANNING: "Looking for sensor",
            SensorStatus.CONNECTING: "Connecting…",
            SensorStatus.CONNECTED: "Sensor connected",
            SensorStatus.ERROR: "Sensor error",
        }
        publisher_titles = {
            PublisherStatus.STOPPED: "Zwift disconnected",
            PublisherStatus.STARTING: "Starting publisher…",
            PublisherStatus.ADVERTISING: "Waiting for Zwift",
            PublisherStatus.CONNECTED: "Zwift connected",
            PublisherStatus.ERROR: "Publisher error",
        }
        self.sensor_card.update(
            sensor_titles[snapshot.sensor_status],
            snapshot.sensor_detail,
            snapshot.sensor_status.value,
        )
        self.publisher_card.update(
            publisher_titles[snapshot.publisher_status],
            snapshot.publisher_detail,
            snapshot.publisher_status.value,
        )
        self.gauge.set_cadence(snapshot.estimated_cadence_spm)
        self.raw_value.set_text("—" if snapshot.raw_cadence_rpm is None else f"{snapshot.raw_cadence_rpm:.1f} RPM")
        self.packet_value.set_text(f"{snapshot.packet_count:,}")
        self.age_value.set_text("—" if snapshot.last_packet_age_s is None else "just now")
        if abs(self.multiplier_spin.get_value() - snapshot.multiplier) > 0.0005:
            self.multiplier_spin.handler_block(self.multiplier_handler)
            self.multiplier_spin.set_value(snapshot.multiplier)
            self.multiplier_spin.handler_unblock(self.multiplier_handler)
        self.estimator_note.set_text(f"Calibrated model · {snapshot.multiplier:.3f} steps per revolution")
        if snapshot.calibration_active:
            self.calibration_detail.set_text(
                f"Capturing {snapshot.calibration_samples}/{snapshot.calibration_required} · "
                f"hold {snapshot.calibration_target_spm:.0f} SPM at {snapshot.calibration_speed_mph:.1f} mph"
            )
            self.calibration_button.set_label("Cancel")
        elif snapshot.calibration_result_factor is not None:
            self.calibration_detail.set_text(
                f"Calibrated · {snapshot.calibration_result_factor:.3f}× from "
                f"{snapshot.calibration_target_spm:.0f} SPM at {snapshot.calibration_speed_mph:.1f} mph"
            )
            self.calibration_button.set_label("Recalibrate")
        else:
            self.calibration_detail.set_text(
                f"Target · {snapshot.calibration_target_spm:.0f} SPM at {snapshot.calibration_speed_mph:.1f} mph"
            )
            self.calibration_button.set_label("Calibrate now")

        self.mode_pill.set_text("BRIDGE ACTIVE" if snapshot.running else "IDLE")
        self.mode_pill.remove_css_class("live-pill")
        self.mode_pill.remove_css_class("idle-pill")
        self.mode_pill.add_css_class("live-pill" if snapshot.running else "idle-pill")
        self.action_button.set_label("Stop bridge" if snapshot.running else "Start bridge")
        self.action_button.remove_css_class("primary-action")
        self.action_button.remove_css_class("danger-action")
        self.action_button.add_css_class("danger-action" if snapshot.running else "primary-action")
        return GLib.SOURCE_REMOVE


class CadenceApplication(Adw.Application):
    def __init__(self, demo: bool = False, auto_start: bool = False, calibrate: bool = False) -> None:
        super().__init__(application_id="com.carlren.MageneZwiftBridge")
        self.demo = demo
        self.auto_start = auto_start
        self.calibrate = calibrate
        self.window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def do_activate(self) -> None:
        if self.window is None:
            from .model import BridgeStore

            store = BridgeStore()
            controller = AppController(store, demo=self.demo)
            self.window = MainWindow(self, controller)
            if self.demo or self.auto_start:
                GLib.timeout_add(300, self._start_backend, controller)
        self.window.present()

    def _start_backend(self, controller: AppController) -> bool:
        if self.calibrate:
            controller.begin_calibration()
        controller.toggle_bridge()
        return GLib.SOURCE_REMOVE
