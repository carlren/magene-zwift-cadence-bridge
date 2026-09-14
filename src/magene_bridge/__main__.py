from __future__ import annotations

import argparse
import logging


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Magene to Zwift cadence bridge")
    parser.add_argument("--demo", action="store_true", help="exercise the UI with simulated connections and cadence")
    parser.add_argument("--auto-start", action="store_true", help="start scanning as soon as the window opens")
    parser.add_argument("--calibrate", action="store_true", help="arm the locally configured calibration target")
    args, gtk_args = parser.parse_known_args()

    from .ui import CadenceApplication

    app = CadenceApplication(demo=args.demo, auto_start=args.auto_start, calibrate=args.calibrate)
    return app.run(gtk_args)


if __name__ == "__main__":
    raise SystemExit(main())
