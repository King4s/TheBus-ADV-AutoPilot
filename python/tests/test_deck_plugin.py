"""Stream Deck plugin backend test: full chain, no Elgato app, no game.

mock Stream Deck app (websocket server, this file)
   <-> deck_plugin.Plugin (the real backend)
       <-> HTTP <-> mock game server (mock_game.py)

Verifies the SDK protocol (register, willAppear, keyDown, setTitle/
setState) AND that key presses actually reach the bus: the autopilot key
engages (throttle on the fake pad), the busbutton key fires its event at
the mock game.

Run:  python python\tests\test_deck_plugin.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import websockets

# keep the user's real config out of the test, and start from defaults
os.environ["THEBUS_AI_BRIDGE_CONFIG"] = os.path.join(
    tempfile.gettempdir(), "thebus_test_config.json")
if os.path.exists(os.environ["THEBUS_AI_BRIDGE_CONFIG"]):
    os.remove(os.environ["THEBUS_AI_BRIDGE_CONFIG"])

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))

from mock_game import MockGame  # noqa: E402

_checks = [0, 0]


def check(name: str, ok: bool, detail: str = ""):
    _checks[0] += 1
    _checks[1] += ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + (f"  ({detail})" if detail and not ok else ""))


class FakePad:
    def __init__(self):
        self.steer = self.throttle = self.brake = 0.0

    def set_controls(self, steer=None, throttle=None, brake=None):
        if steer is not None:
            self.steer = steer
        if throttle is not None:
            self.throttle = throttle
        if brake is not None:
            self.brake = brake

    def neutral(self):
        self.set_controls(0.0, 0.0, 0.0)


async def run_mock_app(game: MockGame, plugin_ready: threading.Event,
                       start_plugin):
    received = []
    conn = {}
    connected = asyncio.Event()

    async def handler(ws):
        conn["ws"] = ws
        connected.set()
        async for raw in ws:
            received.append(json.loads(raw))

    def of(event, ctx=None):
        return [m for m in received if m.get("event") == event
                and (ctx is None or m.get("context") == ctx)]

    async def send(event, action=None, ctx=None, **payload):
        msg = {"event": event}
        if action:
            msg["action"] = "com.thebusaibridge." + action
        if ctx:
            msg["context"] = ctx
        if payload:
            msg["payload"] = payload
        await conn["ws"].send(json.dumps(msg))

    async def key(ctx):
        await send("keyDown", ctx=ctx)
        await send("keyUp", ctx=ctx)

    async def settle(s=1.2):
        await asyncio.sleep(s)

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    plugin = start_plugin(port)
    await asyncio.wait_for(connected.wait(), timeout=10)

    print("protocol:")
    await settle(0.5)
    regs = [m for m in received if m.get("event") == "registerPlugin"]
    check("plugin registered", len(regs) == 1
          and regs[0].get("uuid") == "TESTUUID")

    # place one instance of each action
    await send("willAppear", "autopilot", "c_ap",
               settings={})
    await send("willAppear", "feature", "c_feat",
               settings={"feature": "auto_doors"})
    await send("willAppear", "busbutton", "c_door",
               settings={"event": "DoorFrontOpenClose"})
    await send("willAppear", "busbutton", "c_horn",
               settings={"event": "Horn", "hold": True})
    await send("willAppear", "speed", "c_speed", settings={})
    await settle()
    check("speed key shows a number",
          any(m["payload"]["title"].split("\n")[0].isdigit()
              for m in of("setTitle", "c_speed")))
    check("feature key state on (auto_doors default true)",
          any(m["payload"]["state"] == 1 for m in of("setState", "c_feat")))
    check("door key lit (fixture door open)",
          any(m["payload"]["state"] == 1 for m in of("setState", "c_door")))

    print("autopilot key:")
    await key("c_ap")
    await settle()
    check("engaged", plugin.ap.engaged)
    check("key state -> 1",
          any(m["payload"]["state"] == 1 for m in of("setState", "c_ap")))
    check("throttle flowing at the pad (target 30, speed 0)",
          plugin.ap._pad.throttle > 0)
    received.clear()
    await key("c_ap")
    await settle()
    check("released", not plugin.ap.engaged)
    check("pad neutral after release",
          plugin.ap._pad.throttle == 0 and plugin.ap._pad.brake == 0)
    check("key state -> 0",
          any(m["payload"]["state"] == 0 for m in of("setState", "c_ap")))

    print("feature key:")
    was = plugin.ap.features.auto_doors
    await key("c_feat")
    await settle(0.3)
    check("toggles the configured feature",
          plugin.ap.features.auto_doors is (not was))
    check("toggle persisted to config file",
          json.loads(Path(os.environ["THEBUS_AI_BRIDGE_CONFIG"])
                     .read_text())["features"]["auto_doors"] is (not was))

    print("busbutton key:")
    game.events.clear()
    await key("c_door")
    await settle(0.3)
    check("door event fired at the game",
          ("DoorFrontOpenClose", "push") in game.events)
    game.events.clear()
    await send("keyDown", ctx="c_horn")
    await settle(0.3)
    check("hold key -> press", ("Horn", "press") in game.events)
    await send("keyUp", ctx="c_horn")
    await settle(0.3)
    check("hold key -> release", ("Horn", "release") in game.events)

    print("dials:")
    await send("willAppear", "offsetdial", "c_off", settings={})
    await send("willAppear", "limiterdial", "c_lim", settings={})
    await send("willAppear", "wiperdial", "c_wip", settings={})
    await send("willAppear", "acdial", "c_ac", settings={})
    await send("willAppear", "drivedial", "c_dd", settings={})
    await settle()
    check("offset dial LCD feedback",
          any(m["payload"].get("title") == "LIMIT OFFSET"
              for m in of("setFeedback", "c_off")))
    check("wiper dial LCD shows live state",
          any(m["payload"].get("value") == "Off"
              for m in of("setFeedback", "c_wip")))

    await send("dialRotate", ctx="c_off", ticks=3)
    await settle(0.3)
    check("offset rotate +3", plugin.ap.settings.speed_offset_kmh == 3.0)
    check("offset persisted",
          json.loads(Path(os.environ["THEBUS_AI_BRIDGE_CONFIG"])
                     .read_text())["settings"]["speed_offset_kmh"] == 3.0)
    await send("dialUp", ctx="c_off")
    await settle(0.3)
    check("offset press resets to 0",
          plugin.ap.settings.speed_offset_kmh == 0.0)

    lim0 = plugin.ap.settings.limiter_kmh
    await send("dialRotate", ctx="c_lim", ticks=-5)
    await settle(0.3)
    check("limiter rotate -5", plugin.ap.settings.limiter_kmh == lim0 - 5)
    was_lim = plugin.ap.features.speed_limiter
    await send("dialUp", ctx="c_lim")
    await settle(0.3)
    check("limiter press toggles the limiter",
          plugin.ap.features.speed_limiter is (not was_lim))

    game.events.clear()
    await send("dialRotate", ctx="c_wip", ticks=2)
    await settle(0.3)
    check("wiper rotate -> WiperUp x2",
          game.events.count(("WiperUp", "push")) == 2)
    game.buttons_set.clear()
    await send("dialUp", ctx="c_wip")
    await settle(0.3)
    check("wiper press -> Wiper Off",
          ("Wiper", "Off") in game.buttons_set)

    game.events.clear()
    await send("dialRotate", ctx="c_ac", ticks=-1)
    await send("dialUp", ctx="c_ac")
    await settle(0.3)
    check("A/C rotate -> temperature down",
          ("AirconditionKeyDown", "push") in game.events)
    check("A/C press -> fan step", ("ACIntensity", "push") in game.events)

    received.clear()
    await send("dialDown", ctx="c_dd")
    await asyncio.sleep(0.7)                 # hold long
    await send("dialUp", ctx="c_dd")
    await settle(0.3)
    check("drive dial long-press switches to B",
          any(m["payload"].get("mode") == "B"
              for m in of("setSettings", "c_dd")))
    lim1 = plugin.ap.settings.limiter_kmh
    await send("dialRotate", ctx="c_dd", ticks=2)
    await settle(0.3)
    check("drive dial B rotates the limiter cap",
          plugin.ap.settings.limiter_kmh == lim1 + 2)
    check("drive dial LCD shows the B mode",
          any(str(m["payload"].get("title", "")).startswith("B ·")
              for m in of("setFeedback", "c_dd")))

    plugin.ws.close()
    server.close()
    await server.wait_closed()


def main():
    game = MockGame()
    url = game.start()
    os.environ["THEBUS_AI_BRIDGE_URL"] = url

    from thebus_ai_bridge import deck_plugin

    holder = {}

    def start_plugin(port):
        plugin = deck_plugin.Plugin(port, "TESTUUID", "registerPlugin")
        plugin.ap._pad = FakePad()   # no vgamepad/ViGEm needed in the test
        holder["p"] = plugin
        threading.Thread(target=plugin.run, daemon=True).start()
        # wait for the backend to connect
        time.sleep(0.3)
        return plugin

    asyncio.run(run_mock_app(game, threading.Event(), start_plugin))
    game.stop()

    total, ok = _checks
    print(f"\n{ok}/{total} checks passed.")
    if ok != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
