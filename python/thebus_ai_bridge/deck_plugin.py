"""Elgato Stream Deck plugin backend (websocket, official SDK protocol).

Runs INSIDE the Elgato Stream Deck app, so it works on every connected
deck at once and coexists with your other plugins - including TML's own
official The Bus plugin: use theirs for cockpit buttons if you like,
this one for the AI bridge.

The app launches ``thebuslauncher.exe`` (streamdeck_plugin\\launcher.cpp),
which execs:  python -m thebus_ai_bridge.deck_plugin -port .. -pluginUUID ..
              -registerEvent .. -info ..

Actions (UUIDs under com.thebusaibridge.*):
  autopilot   one key = engage AND release; shows target speed / DWELL / HOLD
  feature     toggle one autopilot feature (dropdown in the inspector)
  busbutton   fire any input event, tap or hold (grouped dropdown);
              stateful ones (doors, hazards, parking brake, indicators,
              engine) light the key up while active
  speed       live speed/limit display; press toggles speed control
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import time
from pathlib import Path

import websocket  # websocket-client

from . import catalog  # noqa: F401  (imported for the PI generators)
from .autopilot import Autopilot
from .bridge import BridgeError, TheBusBridge

log = logging.getLogger("thebus.sdplugin")

ACTION_PREFIX = "com.thebusaibridge."

# default key titles when the user has not typed their own in the app
FEATURE_SHORT = {
    "speed_control": "SPEED\nCTRL",
    "overspeed_brake": "OVERSPD\nBRAKE",
    "stop_at_stops": "SERVICE\nSTOPS",
    "auto_doors": "AUTO\nDOORS",
    "auto_hold": "AUTO\nHOLD",
    "auto_engine": "AUTO\nENGINE",
    "auto_lights": "AUTO\nLIGHTS",
    "auto_indicators": "AUTO\nBLINK",
    "auto_hazards": "AUTO\nHAZARD",
    "driver_override": "DRIVER\nYIELD",
    "speed_limiter": "LIMITER",
    "depart_on_schedule": "ON\nTIME",
}

_D = {  # event -> telemetry predicate that lights the key (state 1)
    "DoorFrontOpenClose": lambda t: _door(t, 0),
    "DoorMiddleOpenClose": lambda t: _door(t, 1),
    "DoorRearOpenClose": lambda t: _door(t, 2),
    "DoorFourthOpenClose": lambda t: _door(t, 3),
    "ToggleWarningLights": lambda t: t.warning_lights,
    "FixingBrake": lambda t: t.fixing_brake,
    "MotorStartStop": lambda t: t.engine_on,
    "SetIndicatorDown": lambda t: t.indicator == -1,
    "SetIndicatorUp": lambda t: t.indicator == 1,
    "IndicatorDown": lambda t: t.indicator == -1,
    "IndicatorUp": lambda t: t.indicator == 1,
    "Lightswitch": lambda t: t.button_state("Light Switch")
    not in ("", "Off"),
    "StopBrakeOnOff": lambda t: t.lamp("ButtonLight BusStopBrake") > 0.5,
    "ToggleDoorClearance": lambda t: t.button_state("Door Clearance")
    == "Secondary",
}


def _door(t, i: int) -> bool:
    doors = t.doors
    if i >= len(doors):
        return False
    v = doors[i].get("Open", False)
    return v if isinstance(v, bool) else str(v).lower() == "true"


class Plugin:
    REFRESH_S = 0.4

    def __init__(self, port: int, plugin_uuid: str, register_event: str):
        self.uuid = plugin_uuid
        self.register_event = register_event
        self.bridge = TheBusBridge()
        self.ap = Autopilot.from_config(self.bridge)
        self.ap.start()
        self.contexts = {}   # context -> {action, settings, user_title, shown}
        self._send_lock = threading.Lock()
        self.ws = websocket.WebSocketApp(
            f"ws://127.0.0.1:{port}",
            on_open=self._on_open, on_message=self._on_message,
            on_error=lambda _ws, e: log.error("ws error: %s", e))

    # -- transport -----------------------------------------------------------
    def run(self):
        threading.Thread(target=self._update_loop, daemon=True,
                         name="thebus-sd-update").start()
        self.ws.run_forever()          # returns when the app closes us
        self.ap.stop()

    def send(self, event: str, context: str | None = None, **payload):
        msg = {"event": event}
        if context is not None:
            msg["context"] = context
        if payload:
            msg["payload"] = payload
        with self._send_lock:
            self.ws.send(json.dumps(msg))

    def _on_open(self, _ws):
        with self._send_lock:
            self.ws.send(json.dumps({"event": self.register_event,
                                     "uuid": self.uuid}))
        log.info("registered with Stream Deck app")

    # -- events from the app -----------------------------------------------
    def _on_message(self, _ws, raw: str):
        try:
            msg = json.loads(raw)
            event = msg.get("event")
            ctx = msg.get("context")
            action = msg.get("action", "")
            payload = msg.get("payload", {})
            if event == "willAppear":
                self.contexts[ctx] = {
                    "action": action.removeprefix(ACTION_PREFIX),
                    "settings": payload.get("settings", {}),
                    "user_title": "", "shown": None}
            elif event == "willDisappear":
                self.contexts.pop(ctx, None)
            elif event == "didReceiveSettings" and ctx in self.contexts:
                self.contexts[ctx]["settings"] = payload.get("settings", {})
                self.contexts[ctx]["shown"] = None  # force redraw
            elif event == "titleParametersDidChange" and ctx in self.contexts:
                self.contexts[ctx]["user_title"] = payload.get("title", "")
                self.contexts[ctx]["shown"] = None
            elif event in ("keyDown", "keyUp"):
                info = self.contexts.get(ctx)
                if info:
                    threading.Thread(
                        target=self._key, daemon=True,
                        args=(ctx, info, event == "keyDown")).start()
        except Exception:
            log.exception("bad message: %.200s", raw)

    # -- key handling ----------------------------------------------------------
    def _key(self, ctx: str, info: dict, down: bool):
        action, settings = info["action"], info["settings"]
        try:
            if action == "autopilot" and down:
                if self.ap.engaged:
                    self.ap.disengage()
                else:
                    self.ap.engage()
            elif action == "feature" and down:
                name = settings.get("feature", "speed_control")
                self.ap.set_feature(
                    name, not getattr(self.ap.features, name))
            elif action == "busbutton":
                self._bus_button(settings, down)
            elif action == "speed" and down:
                self.ap.set_feature(
                    "speed_control", not self.ap.features.speed_control)
        except (BridgeError, ValueError, KeyError) as e:
            log.warning("%s: %s", action, e)
            self.send("showAlert", ctx)

    def _bus_button(self, settings: dict, down: bool):
        event = settings.get("event", "Horn")
        if bool(settings.get("hold")):      # hold while the key is down
            self.bridge.send_event(event, "press" if down else "release")
        elif down:
            self.bridge.tap(event)

    # -- live key updates --------------------------------------------------------
    def _update_loop(self):
        while True:
            time.sleep(self.REFRESH_S)
            try:
                t = self.bridge.read()
                if not t.in_vehicle:
                    t = None
            except BridgeError:
                t = None
            st = self.ap.status()
            for ctx, info in list(self.contexts.items()):
                try:
                    title, state = self._render(info, t, st)
                except Exception:
                    continue
                if info["shown"] == (title, state):
                    continue
                info["shown"] = (title, state)
                try:
                    if title is not None:
                        self.send("setTitle", ctx, title=title, target=0)
                    if state is not None:
                        self.send("setState", ctx, state=state)
                except Exception:
                    info["shown"] = None  # socket hiccup; retry next tick
                    break

    def _render(self, info: dict, t, st: dict):
        """-> (title or None, state or None) for one action instance."""
        action, settings = info["action"], info["settings"]
        user_title = info["user_title"]

        if action == "autopilot":
            if not st["engaged"]:
                return "", 0
            mode = st["mode"]
            if mode == "hold":
                title = "HOLD"
            elif mode in ("dwell", "approach", "depart"):
                title = mode.upper()
            else:
                title = f"→{st['target_kmh']:.0f}"
            return title, 1

        if action == "feature":
            name = settings.get("feature", "speed_control")
            on = st["features"].get(name, False)
            title = user_title or FEATURE_SHORT.get(name, name)
            if name == "speed_limiter" and on:
                title = f"≤{self.ap.settings.limiter_kmh:.0f}"
            return title, 1 if on else 0

        if action == "busbutton":
            event = settings.get("event", "Horn")
            title = user_title or settings.get("label", event)
            pred = _D.get(event)
            if pred is None:
                return title, None  # momentary control, no bus state
            on = t is not None and bool(pred(t))
            return title, 1 if on else 0

        if action == "speed":
            if t is None:
                return "--", None
            lim = t.allowed_speed_kmh
            line2 = (f"\n→{st['target_kmh']:.0f}" if st["engaged"]
                     else (f"\nlim {lim:.0f}" if lim > 1 else ""))
            return f"{t.speed_kmh:.0f}{line2}", None
        return None, None


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("-port", type=int, required=True)
    p.add_argument("-pluginUUID", required=True)
    p.add_argument("-registerEvent", required=True)
    p.add_argument("-info", default="{}")
    args = p.parse_args(argv)

    logging.basicConfig(
        filename=str(Path(__file__).with_name("deck_plugin.log")),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")
    log.info("starting (port %s)", args.port)
    Plugin(args.port, args.pluginUUID, args.registerEvent).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
