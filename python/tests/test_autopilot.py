"""Autopilot behavior test: real Autopilot + real bridge + mock game.

Drives the autopilot's tick synchronously (no thread) against mutated
game state, with a fake pad recording the pedal commands.

    python python\tests\test_autopilot.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from mock_game import MockGame  # noqa: E402
from thebus_ai_bridge.autopilot import Autopilot, Features, Settings  # noqa: E402
from thebus_ai_bridge.bridge import TheBusBridge  # noqa: E402

PASS = 0


def check(name: str, cond: bool):
    global PASS
    print(f"  [{'ok' if cond else 'FAIL'}] {name}")
    if cond:
        PASS += 1
    else:
        raise SystemExit(f"check failed: {name}")


class FakePad:
    def __init__(self):
        self.steer = self.throttle = self.brake = 0.0
        self.calls = 0

    def set_controls(self, steer=None, throttle=None, brake=None):
        if steer is not None:
            self.steer = steer
        if throttle is not None:
            self.throttle = throttle
        if brake is not None:
            self.brake = brake
        self.calls += 1

    def neutral(self):
        self.set_controls(0.0, 0.0, 0.0)


def make_ap(bridge, pad):
    feats = Features()
    sets = Settings()
    sets.stop_min_dwell_s = 0.0
    feats.depart_on_schedule = False
    feats.auto_engine = False   # engine already runs in the fixture
    feats.auto_lights = False   # exercised separately
    ap = Autopilot(bridge, features=feats, settings=sets, pad=pad)
    # engage without starting the polling thread: tests tick synchronously
    ap._engaged = True
    ap._mode = "drive"
    return ap


def tick(ap, bridge):
    ap._tick(bridge.read())


def main():
    game = MockGame()
    url = game.start()
    bridge = TheBusBridge(base_url=url)
    bridge.WORLD_REFRESH_S = 0.0    # no caching: tests mutate world state
    bridge.MISSION_REFRESH_S = 0.0
    v = game.vehicle

    # neutral starting state: rolling on open road, brakes off, doors shut
    v["Brake"] = 0.0
    v["FixingBrake"] = "false"
    v["IsAtStop"] = "false"
    for d in v["doors"]:
        d["Open"] = "false"
    v["PassengerDoorsOpen"] = "false"
    for b in v["Buttons"]:
        if b["Name"] == "Gear Selector":
            b["State"] = "Drive"
    # push the next stop far away so the stop pipeline stays out of the way
    game.mission["NextStop"]["GeoLocation"] = [52.6, 13.5]

    pad = FakePad()
    ap = make_ap(bridge, pad)

    print("speed control:")
    v["Speed"] = 10.0
    tick(ap, bridge)
    check("under limit -> throttle", pad.throttle > 0 and pad.brake == 0)
    check("target = allowed speed", ap.target_kmh == 30.0)
    v["Speed"] = 40.0
    tick(ap, bridge)
    check("well over limit -> brake", pad.brake > 0 and pad.throttle == 0)
    ap.settings.speed_offset_kmh = -5
    tick(ap, bridge)
    check("offset lowers target", ap.target_kmh == 25.0)
    ap.settings.speed_offset_kmh = 0

    print("speed limiter:")
    ap.set_feature("speed_limiter", True)
    ap.settings.limiter_kmh = 20.0
    tick(ap, bridge)
    check("limiter caps target", ap.target_kmh == 20.0)
    ap.set_feature("speed_limiter", False)

    print("gear handling:")
    for b in v["Buttons"]:
        if b["Name"] == "Gear Selector":
            b["State"] = "Reverse"
    tick(ap, bridge)
    check("reverse -> no pedals", pad.throttle == 0 and pad.brake == 0)
    for b in v["Buttons"]:
        if b["Name"] == "Gear Selector":
            b["State"] = "Neutral"
    game.events.clear()
    v["Speed"] = 5.0
    tick(ap, bridge)
    check("neutral while engaged -> SetGearD",
          ("SetGearD", "push") in game.events)
    for b in v["Buttons"]:
        if b["Name"] == "Gear Selector":
            b["State"] = "Drive"

    print("hazards on emergency braking:")
    v["Speed"] = 45.0
    v["Brake"] = 0.95
    ap.features.driver_override = False  # isolate the hazard logic
    game.events.clear()
    tick(ap, bridge)
    check("hazards fired", ("ToggleWarningLights", "push") in game.events)
    check("hazard state on", bridge.read().warning_lights is True)
    v["Brake"] = 0.0
    ap._hazard_until = 0.0  # fast-forward the back-off timer
    tick(ap, bridge)
    check("hazards released", bridge.read().warning_lights is False)

    print("driver override:")
    ap.features.driver_override = True
    v["Speed"] = 30.0
    v["Brake"] = 0.8
    tick(ap, bridge)  # our own overspeed brake command decays first
    tick(ap, bridge)
    tick(ap, bridge)
    check("human brake -> hold mode", ap.mode == "hold")
    check("pedals released in hold",
          pad.throttle == 0.0 and pad.brake == 0.0)
    v["Brake"] = 0.0
    v["Throttle"] = 0.5
    tick(ap, bridge)
    check("throttle tap resumes", ap.mode != "hold")
    v["Throttle"] = 0.0

    print("service stop pipeline:")
    # place the next stop ~100 m ahead of the player
    game.mission["NextStop"]["GeoLocation"] = [52.526791, 13.370250]
    v["Speed"] = 30.0
    tick(ap, bridge)
    check("approach mode", ap.mode == "approach")

    # indicate toward the curb inside the indicator lead distance
    game.mission["NextStop"]["GeoLocation"] = [52.526791, 13.369500]
    game.events.clear()
    tick(ap, bridge)
    check("right indicator toward the stop",
          ("SetIndicatorUp", "push") in game.events)

    # close enough that the comfort-braking curve dips under the limit
    game.mission["NextStop"]["GeoLocation"] = [52.526791, 13.369334]
    tick(ap, bridge)
    check("approach caps target below limit", ap.target_kmh < 30.0)

    # rolling to a halt inside the stop zone
    game.mission["NextStop"]["GeoLocation"] = [52.526791, 13.368773]
    game.mission["CurrentStop"]["BoardingPeopleCount"] = 2
    game.mission["CurrentStop"]["DeboardingPeopleCount"] = 1
    v["Speed"] = 3.0
    v["IsAtStop"] = "true"
    tick(ap, bridge)
    check("braking to a halt", pad.brake > 0)
    v["Speed"] = 0.0
    game.events.clear()
    tick(ap, bridge)
    check("dwell mode", ap.mode == "dwell")
    check("hold brake engaged", ("StopBrakeOnOff", "push") in game.events)
    check("front door opened",
          ("DoorFrontOpenClose", "push") in game.events)
    check("middle door for deboarding",
          ("DoorMiddleOpenClose", "push") in game.events)
    check("doors open in game", bridge.read().doors_open is True)

    # boarding still pending: stay put
    game.events.clear()
    tick(ap, bridge)
    check("waits while boarding", ap.mode == "dwell" and not game.events)

    # boarding done: close doors, release hold, indicate out, depart
    game.mission["CurrentStop"]["BoardingPeopleCount"] = 0
    game.mission["CurrentStop"]["DeboardingPeopleCount"] = 0
    tick(ap, bridge)   # closes the doors it opened
    check("doors closed again", bridge.read().doors_open is False)
    game.events.clear()
    tick(ap, bridge)
    check("hold brake released", ("StopBrakeOnOff", "push") in game.events)
    check("left indicator to pull out",
          ("SetIndicatorDown", "push") in game.events)
    check("depart mode", ap.mode == "depart")
    ap._depart_until = 0.0
    game.events.clear()
    v["IsAtStop"] = "false"
    game.mission["NextStop"]["GeoLocation"] = [52.6, 13.5]
    tick(ap, bridge)
    check("indicator cancelled after pull-out",
          ("SetIndicatorOff", "push") in game.events)
    check("back to drive", ap.mode == "drive")

    print("auto lights:")
    ap.features.auto_lights = True
    game.world["NightLightEnabled"] = "true"
    game.buttons_set.clear()
    tick(ap, bridge)
    check("night -> headlights",
          ("Light Switch", "Headlights") in game.buttons_set)
    # a human flips the switch: auto_lights backs off
    for b in v["Buttons"]:
        if b["Name"] == "Light Switch":
            b["State"] = "Parking Lights"
    game.buttons_set.clear()
    tick(ap, bridge)
    check("manual switch pauses auto_lights",
          not game.buttons_set and ap._lights_pause_until > time.monotonic())

    print("disengage on engine off:")
    v["EngineStarted"] = "false"
    tick(ap, bridge)
    check("engine off -> disengaged", not ap.engaged)
    check("pad neutralized", (pad.steer, pad.throttle, pad.brake)
          == (0.0, 0.0, 0.0))

    game.stop()
    print(f"\n{PASS}/{PASS} checks passed.")


if __name__ == "__main__":
    main()
