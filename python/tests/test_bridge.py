"""Roundtrip test: real bridge client against the mock game server.

    python python\tests\test_bridge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from mock_game import MockGame  # noqa: E402
from thebus_ai_bridge.bridge import (GameNotRunning,  # noqa: E402
                                     TheBusBridge)

PASS = 0


def check(name: str, cond: bool):
    global PASS
    status = "ok" if cond else "FAIL"
    print(f"  [{status}] {name}")
    if cond:
        PASS += 1
    else:
        raise SystemExit(f"check failed: {name}")


def main():
    game = MockGame()
    url = game.start()
    bridge = TheBusBridge(base_url=url)
    bridge.connect(wait=False)

    print("telemetry decode:")
    t = bridge.read()
    check("in_vehicle", t.in_vehicle)
    check("vehicle model", t.vehicle.get("VehicleModel") == "Citywide LF")
    check("speed 0.0 km/h", t.speed_kmh == 0.0)
    check("allowed speed 30", t.allowed_speed_kmh == 30.0)
    check("engine on (string bool)", t.engine_on is True)
    check("fixing brake on", t.fixing_brake is True)
    check("doors open", t.doors_open is True)
    check("at stop", t.at_stop is True)
    check("gear Neutral", t.gear_selector == "Neutral")
    check("indicator off", t.indicator == 0)
    check("no stop request", t.stop_requested is False)
    check("geo ~ Berlin", abs(t.geo[0] - 52.5268) < 0.01)
    check("night flag decoded", t.is_night is False)
    check("level Berlin", t.level == "Berlin")
    check("next stop name", "Lehrter" in t.next_stop.get("StopName", ""))
    d = t.next_stop_distance_m
    check("next stop distance 200-400 m", d is not None and 200 < d < 400)
    check("button state lookup", t.button_state("Wiper") == "Off")
    check("lamp lookup", t.lamp("Light BRAKE") == 1.0)
    check("to_dict has 25+ keys", len(t.to_dict()) >= 25)

    print("events:")
    bridge.tap("DoorFrontOpenClose")
    check("event recorded", game.events[-1] == ("DoorFrontOpenClose", "push"))
    check("door state flipped (was open)",
          not game.vehicle["doors"][0]["Open"])
    t = bridge.read()
    check("other doors still open", t.doors_open is True)
    for ev in ("DoorMiddleOpenClose",):
        bridge.tap(ev)  # close the middle door too (rear doors stay put)

    bridge.press("MotorStartStop")
    bridge.release("MotorStartStop")
    check("press+release recorded",
          game.events[-2:] == [("MotorStartStop", "press"),
                               ("MotorStartStop", "release")])

    bridge.tap("SetGearD")
    t = bridge.read()
    check("gear selector -> Drive", t.gear_selector == "Drive")

    bridge.tap("ToggleWarningLights")
    t = bridge.read()
    check("warning lights on", t.warning_lights is True)

    print("buttons:")
    bridge.set_button("Wiper", "Interval")
    check("setbutton recorded",
          game.buttons_set[-1] == ("Wiper", "Interval"))
    t = bridge.read()
    check("wiper state Interval", t.button_state("Wiper") == "Interval")
    check("wiper level mirrored", t.vehicle["WiperLevel"] == 1)

    print("misc endpoints:")
    check("world", bridge.world()["LevelName"] == "Berlin")
    check("mission stops",
          bridge.mission()["NextStop"]["StopName"].startswith("Lehrter"))
    check("vehicles list", bridge.vehicles() == [game.vehicle_id])
    check("map", bridge.map()["MapName"] == "Berlin")
    rm = bridge.roadmap()
    check("roadmap tolerant of malformed JSON", rm.get("Lanes") == [])
    check("attached (not in menu)", bridge.attached is True)

    print("error handling:")
    game.stop()
    try:
        TheBusBridge(base_url=url, timeout=0.2).connect(wait=False)
        check("GameNotRunning raised", False)
    except GameNotRunning:
        check("GameNotRunning raised", True)

    print(f"\n{PASS}/{PASS} checks passed.")


if __name__ == "__main__":
    main()
