"""Command line: python -m thebus_ai_bridge {status|monitor|demo|gui|autopilot|ai|mcp|config|events}"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .bridge import GameNotRunning, TheBusBridge


def _fmt_status(bridge: TheBusBridge) -> str:
    t = bridge.read()
    if not t.in_vehicle:
        return (f"game          : running ({t.level or 'menu'})\n"
                f"player        : not in a vehicle (mode "
                f"{t.player.get('Mode', '?')})")
    d = t.next_stop_distance_m
    lines = [
        f"bus           : {t.vehicle.get('VehicleModel', '?')}"
        f"  ({t.vehicle_id})",
        f"speed         : {t.speed_kmh:6.1f} km/h   limit "
        f"{t.allowed_speed_kmh:5.1f} km/h   gear {t.gear_selector}",
        f"engine        : {'on' if t.engine_on else 'off'}"
        f"   rpm {t.rpm:4.0f}   ignition {'on' if t.ignition_on else 'off'}",
        f"controls      : steer {t.steering:+.2f}  throttle {t.throttle:.2f}"
        f"  brake {t.brake:.2f}  parking {'ON' if t.fixing_brake else 'off'}",
        f"doors         : {'OPEN' if t.doors_open else 'closed'}"
        f"   at stop: {'yes' if t.at_stop else 'no'}"
        f"   stop request: {'YES' if t.stop_requested else 'no'}",
        f"passengers    : {t.vehicle.get('NumOccupiedSeats', 0)}"
        f"/{t.vehicle.get('NumSeats', 0)} seats",
        f"fuel          : {t.fuel_frac * 100:.0f} %",
        f"world         : {t.level}  {t.game_time}"
        f"  {'night' if t.is_night else 'day'}",
    ]
    if t.next_stop:
        lines.append(
            f"next stop     : {t.next_stop.get('StopName', '?')}"
            + (f"  ({d:.0f} m)" if d is not None else "")
            + f"  dep {t.next_stop.get('DepartureTime', '')[-8:]}")
    if t.boarding_pending:
        lines.append(f"boarding      : {t.boarding_pending} waiting")
    return "\n".join(lines)


def cmd_status(_args) -> int:
    bridge = TheBusBridge()
    try:
        bridge.connect(wait=False)
    except GameNotRunning as e:
        print(f"not connected: {e}")
        return 1
    print(_fmt_status(bridge))
    return 0


def cmd_monitor(_args) -> int:
    bridge = TheBusBridge()
    print("waiting for the game ...")
    bridge.connect()
    try:
        while True:
            print("\x1b[2J\x1b[H", end="")  # clear screen
            print("The Bus AI Bridge monitor - Ctrl+C to exit\n")
            try:
                print(_fmt_status(bridge))
            except GameNotRunning as e:
                print(f"(waiting: {e})")
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def cmd_demo(_args) -> int:
    from .examples.line_driver import run
    run()
    return 0


def cmd_gui(_args) -> int:
    from .gui import main as gui_main
    gui_main()
    return 0


def cmd_autopilot(args) -> int:
    from .autopilot import Autopilot
    bridge = TheBusBridge()
    print("waiting for the game ...")
    bridge.connect()
    bridge.wait_in_vehicle()
    ap = Autopilot.from_config(bridge)
    if args.offset is not None:
        ap.settings.speed_offset_kmh = args.offset
    if args.max is not None:
        ap.settings.max_speed_kmh = args.max
    ap.engage()
    print("autopilot engaged - Ctrl+C to hand control back")
    try:
        while True:
            time.sleep(1.0)
            s = ap.status()
            print(f"\r{s['mode']:9} target {s['target_kmh']:5.1f} km/h   ",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ap.stop()
        print("\nreleased.")
    return 0


def cmd_ai(_args) -> int:
    """Mount the AI: autopilot + steering along the navigation route."""
    from .ai_driver import AiDriver
    from .autopilot import Autopilot
    bridge = TheBusBridge()
    print("waiting for the game ...")
    bridge.connect()
    bridge.wait_in_vehicle()
    ap = Autopilot.from_config(bridge)
    ai = AiDriver(bridge, ap)
    ai.start()
    print("AI driver mounted (set a route in the game's navigation!) - "
          "Ctrl+C dismounts")
    try:
        while True:
            time.sleep(1.0)
            s, a = ap.status(), ai.status()
            print(f"\r{a['mode']:7} steer {a['steer']:+.2f}  "
                  f"target {s['target_kmh']:5.1f} km/h  "
                  f"path {a['path_m']:4d} m   ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ai.stop()
        print("\ndismounted - the bus is yours again.")
    return 0


def cmd_mcp(_args) -> int:
    from .mcp_server import main as mcp_main
    mcp_main()
    return 0


def cmd_config(_args) -> int:
    from . import config
    feats, sets = config.load()
    print(f"config file: {config.config_path()}")
    print(json.dumps({"features": feats.as_dict(),
                      "settings": {k: getattr(sets, k) for k in vars(sets)}},
                     indent=2))
    return 0


def cmd_events(_args) -> int:
    from . import catalog
    for group, events in catalog.EVENT_GROUPS.items():
        print(f"\n[{group}]")
        for name, desc in events.items():
            print(f"  {name:28} {desc}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="thebus_ai_bridge",
        description="AI bridge for The Bus (TML-Studios telemetry interface)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="one-shot connection/state summary")
    sub.add_parser("monitor", help="live dashboard in the terminal")
    sub.add_parser("demo", help="autopilot line-driving demo (Ctrl+C stops)")
    sub.add_parser("gui", help="control panel (tkinter)")
    ap = sub.add_parser("autopilot", help="headless autopilot")
    ap.add_argument("--offset", type=float, default=None,
                    help="km/h relative to the posted limit")
    ap.add_argument("--max", type=float, default=None, help="max km/h")
    sub.add_parser("ai", help="mount the AI driver (autopilot + steering "
                              "along the game's navigation route)")
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    sub.add_parser("config", help="show the config file path and values")
    sub.add_parser("events", help="list known input events")
    args = p.parse_args(argv)
    return {"status": cmd_status, "monitor": cmd_monitor, "demo": cmd_demo,
            "gui": cmd_gui, "autopilot": cmd_autopilot, "ai": cmd_ai,
            "mcp": cmd_mcp, "config": cmd_config,
            "events": cmd_events}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
