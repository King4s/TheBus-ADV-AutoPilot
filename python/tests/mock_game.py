"""Mock of The Bus telemetry HTTP server, for tests without the game.

Serves the JSON captured from a live game session (tests/data/) on an
ephemeral localhost port, applies simple state transitions for the
events/buttons the bridge sends, and records every command so tests can
assert on them.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = Path(__file__).parent / "data"


def _load(name: str):
    return json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))


class MockGame:
    """State container + HTTP server. Mutate the dicts freely in tests."""

    def __init__(self):
        self.player = _load("player")
        self.vehicle = _load("vehicle_current")
        self.world = _load("world")
        self.mission = _load("mission")
        self.map = _load("map")
        self.route = _load("route")
        self.events: list[tuple[str, str]] = []   # (event, mode)
        self.buttons_set: list[tuple[str, str]] = []
        self.vehicle_id = self.player["CurrentVehicle"]
        self._server = None
        self._thread = None

    # -- state transitions for events the bridge fires --------------------
    def _apply_event(self, event: str):
        v = self.vehicle

        def set_button(name, state):
            for b in v["Buttons"]:
                if b["Name"] == name:
                    b["State"] = state

        if event == "DoorFrontOpenClose":
            v["doors"][0]["Open"] = not _b(v["doors"][0]["Open"])
            v["PassengerDoorsOpen"] = any(_b(d["Open"]) for d in v["doors"])
        elif event == "DoorMiddleOpenClose":
            v["doors"][1]["Open"] = not _b(v["doors"][1]["Open"])
            v["PassengerDoorsOpen"] = any(_b(d["Open"]) for d in v["doors"])
        elif event == "ToggleWarningLights":
            v["WarningLights"] = not _b(v["WarningLights"])
        elif event == "FixingBrake":
            v["FixingBrake"] = not _b(v["FixingBrake"])
        elif event in ("SetGearD", "SetGearN", "SetGearR"):
            sel = {"SetGearD": "Drive", "SetGearN": "Neutral",
                   "SetGearR": "Reverse"}[event]
            set_button("Gear Selector", sel)
            v["Gearbox"]["CurrentSelector"] = sel[0]
        elif event == "SetIndicatorUp":
            v["IndicatorState"] = 1
        elif event == "SetIndicatorDown":
            v["IndicatorState"] = -1
        elif event == "SetIndicatorOff":
            v["IndicatorState"] = 0
        elif event == "MotorStartStop":
            pass  # engine start handled via press+release in tests

    def _apply_button(self, name: str, state: str):
        for b in self.vehicle["Buttons"]:
            if b["Name"] == name:
                b["State"] = state
        if name == "Fake Ignition":
            self.vehicle["IgnitionEnabled"] = state in ("On", "Start")
        if name == "Wiper":
            self.vehicle["WiperLevel"] = \
                {"Off": 0, "Interval": 1, "On": 2, "Fast": 3}.get(state, 0)

    # -- server ------------------------------------------------------------
    def start(self) -> str:
        game = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _json(self, obj, raw: bytes | None = None):
                body = raw if raw is not None else \
                    json.dumps(obj).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                parts = [p for p in parsed.path.split("/") if p]
                q = urllib.parse.parse_qs(parsed.query)
                p0 = parts[0].lower() if parts else ""
                if p0 == "player":
                    return self._json(game.player)
                if p0 == "world":
                    return self._json(game.world)
                if p0 == "mission":
                    return self._json(game.mission)
                if p0 == "map":
                    return self._json(game.map)
                if p0 == "route":
                    return self._json(game.route)
                if p0 == "roadmap":
                    # reproduce the game's malformed-JSON quirk
                    return self._json(None, raw=b'{,"Lanes":[]}')
                if p0 == "vehicles":
                    if len(parts) == 1:
                        return self._json([game.vehicle_id])
                    if len(parts) == 2:
                        return self._json(game.vehicle)
                    cmd = parts[2].lower()
                    if cmd in ("sendevent", "sendeventpress",
                               "sendeventrelease"):
                        event = q.get("event", [""])[0]
                        mode = {"sendevent": "push",
                                "sendeventpress": "press",
                                "sendeventrelease": "release"}[cmd]
                        game.events.append((event, mode))
                        if mode == "push":
                            game._apply_event(event)
                        return self._json({"ok": True})
                    if cmd == "setbutton":
                        name = q.get("button", [""])[0]
                        state = q.get("state", [""])[0]
                        game.buttons_set.append((name, state))
                        game._apply_button(name, state)
                        return self._json({"ok": True})
                self.send_response(404)
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server.server_close()


def _b(v) -> bool:
    return v if isinstance(v, bool) else str(v).lower() == "true"
