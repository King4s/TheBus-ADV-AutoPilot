"""Demo: the autopilot works a bus line, you steer.

Starts the engine, follows the posted limit, brakes into every
timetable stop, cycles the doors around boarding, signals in and out.
Ctrl+C hands control back.

    python -m thebus_ai_bridge demo
"""
from __future__ import annotations

import time

from ..autopilot import Autopilot
from ..bridge import TheBusBridge


def run():
    bridge = TheBusBridge()
    print("waiting for the game (telemetry interface on port 37337) ...")
    bridge.connect()
    print("waiting for you to sit in a bus ...")
    t = bridge.wait_in_vehicle()
    print(f"driving: {t.vehicle.get('VehicleModel', '?')} on "
          f"{t.level or '?'}")

    ap = Autopilot.from_config(bridge)
    ap.engage()
    print("autopilot engaged - it handles speed, stops and doors; "
          "YOU steer. Ctrl+C to stop.\n")
    try:
        while True:
            time.sleep(1.0)
            t = bridge.read()
            s = ap.status()
            stop = t.next_stop.get("StopName", "-")
            d = t.next_stop_distance_m
            print(f"\r{s['mode']:9} {t.speed_kmh:5.1f}/{s['target_kmh']:5.1f}"
                  f" km/h  next: {stop[:28]:28}"
                  f" {'' if d is None else f'{d:5.0f} m'}   ",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ap.stop()
        print("\nreleased - the bus is yours again.")
