"""Client for The Bus telemetry interface (TML-Studios).

The game (with "Enable Telemetry Interface" switched on in the options)
runs a local HTTP server on port 37337 that serves JSON snapshots and
accepts control events:

    GET /player                      mode, current vehicle, position, rotation
    GET /world                       level, clock, weather, NightLightEnabled
    GET /map /mission /route         map info, bus stops, timetable
    GET /vehicles                    names of spawned vehicles
    GET /vehicles/Current            full vehicle state (speed, gear, doors,
                                     lamps, ~100 cockpit buttons)
    GET /vehicles/<id>/sendevent?event=E          one-shot ("push")
    GET /vehicles/<id>/sendeventpress?event=E     hold down ...
    GET /vehicles/<id>/sendeventrelease?event=E   ... and release
    GET /vehicles/<id>/setbutton?button=B&state=S set a stateful button

This module is the Python side: read telemetry, fire events. Analog
driving (steer/throttle/brake) is NOT part of the telemetry interface -
that goes through a virtual gamepad, see :mod:`.pad`.

Conventions (straight from the game):
  * Speed and AllowedSpeed are km/h already.
  * Rotation.Yaw is degrees, 0 = east of the UE world, clockwise positive.
  * GeoLocation is [latitude, longitude] (Berlin map is geo-referenced).
  * Booleans arrive as the strings "true"/"false" - reads here return bool.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://127.0.0.1:37337"


def _base_url() -> str:
    """Server base URL; THEBUS_AI_BRIDGE_URL overrides (test isolation)."""
    return os.environ.get("THEBUS_AI_BRIDGE_URL", DEFAULT_URL).rstrip("/")


class BridgeError(RuntimeError):
    """Problem talking to the game's telemetry server."""


class GameNotRunning(BridgeError):
    """The game is not running or the telemetry interface is disabled."""


def _b(value) -> bool:
    """The API serializes booleans as 'true'/'false' strings."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in meters between two [lat, lon] points."""
    rad = math.radians
    dlat, dlon = rad(lat2 - lat1), rad(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(rad(lat1)) * math.cos(rad(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371000.0 * 2 * math.asin(math.sqrt(a))


class Telemetry:
    """One consistent snapshot: player + current vehicle (+ cached world &
    mission, which change slowly and are refreshed at a lower rate).

    Raw JSON stays reachable via :attr:`player`, :attr:`vehicle`,
    :attr:`world`, :attr:`mission`; the properties below decode the fields
    an agent needs every tick.
    """

    __slots__ = ("player", "vehicle", "world", "mission", "stamp")

    def __init__(self, player: dict, vehicle: dict | None,
                 world: dict | None, mission: dict | None):
        self.player = player or {}
        self.vehicle = vehicle or {}
        self.world = world or {}
        self.mission = mission or {}
        self.stamp = time.monotonic()

    # -- player ----------------------------------------------------------
    @property
    def in_vehicle(self) -> bool:
        return self.player.get("Mode") == "Vehicle" and bool(self.vehicle)

    @property
    def vehicle_id(self) -> str:
        return self.player.get("CurrentVehicle", "") or ""

    @property
    def geo(self) -> tuple:
        """(latitude, longitude) of the player."""
        g = self.player.get("GeoLocation") or [0.0, 0.0]
        return (float(g[0]), float(g[1]))

    @property
    def heading_deg(self) -> float:
        """UE yaw in degrees (clockwise positive)."""
        return float((self.player.get("Rotation") or {}).get("Yaw", 0.0))

    # -- vehicle ---------------------------------------------------------
    @property
    def speed_kmh(self) -> float:
        return float(self.vehicle.get("Speed", 0.0))

    @property
    def allowed_speed_kmh(self) -> float:
        return float(self.vehicle.get("AllowedSpeed", 0.0))

    @property
    def rpm(self) -> float:
        return float(self.vehicle.get("RPM", 0.0))

    @property
    def engine_on(self) -> bool:
        return _b(self.vehicle.get("EngineStarted", False))

    @property
    def ignition_on(self) -> bool:
        return _b(self.vehicle.get("IgnitionEnabled", False))

    @property
    def fixing_brake(self) -> bool:
        """Parking ("fixing") brake engaged."""
        return _b(self.vehicle.get("FixingBrake", False))

    @property
    def warning_lights(self) -> bool:
        return _b(self.vehicle.get("WarningLights", False))

    @property
    def doors_open(self) -> bool:
        return _b(self.vehicle.get("PassengerDoorsOpen", False))

    @property
    def at_stop(self) -> bool:
        """The game's own 'within the bus stop zone' flag."""
        return _b(self.vehicle.get("IsAtStop", False))

    @property
    def gear_selector(self) -> str:
        """'Drive' | 'Neutral' | 'Reverse' (from the Gear Selector button;
        Gearbox.CurrentSelector holds the short form N/D/R)."""
        return self.button_state("Gear Selector") or \
            (self.vehicle.get("Gearbox") or {}).get("CurrentSelector", "")

    @property
    def indicator(self) -> int:
        """-1 left, 0 off, +1 right."""
        return int(self.vehicle.get("IndicatorState", 0))

    @property
    def throttle(self) -> float:
        """Combined throttle input the game sees (human + AI)."""
        return float(self.vehicle.get("Throttle", 0.0))

    @property
    def brake(self) -> float:
        return float(self.vehicle.get("Brake", 0.0))

    @property
    def steering(self) -> float:
        return float(self.vehicle.get("Steering", 0.0))

    @property
    def fuel_frac(self) -> float:
        return float(self.vehicle.get("DisplayFuel", 0.0))

    @property
    def cruise_active(self) -> bool:
        return _b(self.vehicle.get("CruiseControlActive", False))

    @property
    def stop_requested(self) -> bool:
        """A passenger pressed the stop-request button."""
        return any(_b(d.get("StopRequest")) for d in self.doors)

    @property
    def doors(self) -> list:
        return self.vehicle.get("doors") or []

    @property
    def buttons(self) -> list:
        return self.vehicle.get("Buttons") or []

    def button(self, name: str) -> dict | None:
        for b in self.buttons:
            if b.get("Name") == name:
                return b
        return None

    def button_state(self, name: str) -> str:
        b = self.button(name)
        return (b or {}).get("State", "")

    def lamp(self, name: str) -> float:
        return float((self.vehicle.get("AllLamps") or {}).get(name, 0.0))

    # -- world / mission ---------------------------------------------------
    @property
    def is_night(self) -> bool:
        return _b(self.world.get("NightLightEnabled", False))

    @property
    def game_time(self) -> str:
        return self.world.get("DateTime", "")

    @property
    def level(self) -> str:
        return self.world.get("LevelName", "")

    @property
    def next_stop(self) -> dict:
        return self.mission.get("NextStop") or {}

    @property
    def current_stop(self) -> dict:
        return self.mission.get("CurrentStop") or {}

    @property
    def next_stop_distance_m(self) -> float | None:
        """Great-circle distance to the next timetable stop (None when no
        mission is active)."""
        g = self.next_stop.get("GeoLocation")
        if not g:
            return None
        lat, lon = self.geo
        return _haversine_m(lat, lon, float(g[0]), float(g[1]))

    @property
    def boarding_pending(self) -> int:
        s = self.current_stop
        return int(s.get("BoardingPeopleCount", 0) or 0) + \
            int(s.get("DeboardingPeopleCount", 0) or 0)

    def to_dict(self) -> dict:
        """Compact dict of the decoded values (for status displays / MCP)."""
        return {
            "in_vehicle": self.in_vehicle,
            "vehicle_id": self.vehicle_id,
            "vehicle_model": self.vehicle.get("VehicleModel", ""),
            "speed_kmh": round(self.speed_kmh, 1),
            "allowed_speed_kmh": self.allowed_speed_kmh,
            "rpm": round(self.rpm),
            "gear_selector": self.gear_selector,
            "engine_on": self.engine_on,
            "ignition_on": self.ignition_on,
            "fixing_brake": self.fixing_brake,
            "doors_open": self.doors_open,
            "at_stop": self.at_stop,
            "stop_requested": self.stop_requested,
            "indicator": self.indicator,
            "warning_lights": self.warning_lights,
            "throttle": round(self.throttle, 2),
            "brake": round(self.brake, 2),
            "steering": round(self.steering, 3),
            "fuel_frac": round(self.fuel_frac, 3),
            "cruise_active": self.cruise_active,
            "geo": self.geo,
            "heading_deg": round(self.heading_deg, 1),
            "level": self.level,
            "game_time": self.game_time,
            "is_night": self.is_night,
            "next_stop": self.next_stop.get("StopName", ""),
            "next_stop_distance_m": (
                None if self.next_stop_distance_m is None
                else round(self.next_stop_distance_m)),
            "boarding_pending": self.boarding_pending,
            "seats": f"{self.vehicle.get('NumOccupiedSeats', 0)}"
                     f"/{self.vehicle.get('NumSeats', 0)}",
        }

    def __repr__(self):
        return (f"<Telemetry {self.vehicle.get('VehicleModel', '(no bus)')} "
                f"speed={self.speed_kmh:.1f}km/h gear={self.gear_selector} "
                f"doors={'open' if self.doors_open else 'closed'}>")


class TheBusBridge:
    """Talk to the game's telemetry HTTP server; read state, fire events."""

    WORLD_REFRESH_S = 2.0    # world/mission change slowly - poll them slower
    MISSION_REFRESH_S = 1.0

    def __init__(self, base_url: str | None = None, timeout: float = 0.5):
        self.base_url = (base_url or _base_url()).rstrip("/")
        self.timeout = timeout
        self._lock = threading.RLock()
        self._world = None
        self._world_at = 0.0
        self._mission = None
        self._mission_at = 0.0
        self._vehicle_id = ""

    # -- HTTP ---------------------------------------------------------------
    def _get(self, path: str, parse: bool = True):
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                body = r.read()
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise GameNotRunning(
                f"cannot reach the telemetry server at {self.base_url} - "
                "is the game running with 'Enable Telemetry Interface' "
                f"switched on in the options? ({e})") from e
        if not parse:
            return body
        try:
            return json.loads(body)
        except ValueError:
            # /roadmap is known to emit slightly broken JSON ("{," prefix)
            try:
                return json.loads(body.decode("utf-8", "replace")
                                  .replace("{,", "{", 1))
            except ValueError as e:
                raise BridgeError(f"bad JSON from /{path}: {e}") from e

    # -- connection -----------------------------------------------------------
    def connect(self, wait: bool = True, timeout: float | None = None):
        """Verify the server responds. With ``wait=True`` blocks until the
        game shows up (optionally up to ``timeout`` seconds)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                self._get("world")
                return self
            except GameNotRunning:
                if not wait:
                    raise
                if deadline is not None and time.monotonic() > deadline:
                    raise GameNotRunning(
                        "timed out waiting for the telemetry server")
                time.sleep(0.5)

    @property
    def attached(self) -> bool:
        """True when the game is running and out of the main menu."""
        try:
            w = self._get("world")
        except BridgeError:
            return False
        return not _b(w.get("IsInMainMenu", False))

    # -- telemetry -------------------------------------------------------------
    def read(self) -> Telemetry:
        """Snapshot of player + current vehicle; world & mission are cached
        and refreshed at a lower rate (they change slowly)."""
        player = self._get("player")
        vehicle = None
        vid = player.get("CurrentVehicle") or ""
        if player.get("Mode") == "Vehicle" and vid:
            try:
                vehicle = self._get(f"vehicles/{urllib.parse.quote(vid)}")
            except BridgeError:
                vehicle = None
        with self._lock:
            self._vehicle_id = vid
            now = time.monotonic()
            # >= so a refresh interval of 0 disables caching entirely
            # (monotonic() ticks coarsely on Windows < 3.13)
            if self._world is None or now - self._world_at >= self.WORLD_REFRESH_S:
                try:
                    self._world = self._get("world")
                    self._world_at = now
                except BridgeError:
                    pass
            if (self._mission is None
                    or now - self._mission_at >= self.MISSION_REFRESH_S):
                try:
                    self._mission = self._get("mission")
                    self._mission_at = now
                except BridgeError:
                    self._mission = None
            return Telemetry(player, vehicle, self._world, self._mission)

    def wait_in_vehicle(self, timeout: float | None = None) -> Telemetry:
        """Block until the player sits in a vehicle."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            t = self.read()
            if t.in_vehicle:
                return t
            if deadline is not None and time.monotonic() > deadline:
                raise GameNotRunning("timed out waiting for the player "
                                     "to enter a vehicle")
            time.sleep(0.5)

    def world(self) -> dict:
        return self._get("world")

    def mission(self) -> dict:
        return self._get("mission")

    def map(self) -> dict:
        return self._get("map")

    def route(self) -> dict:
        return self._get("route")

    def roadmap(self) -> dict:
        """Lane geometry of the whole map (large; malformed-JSON tolerant)."""
        return self._get("roadmap")

    def vehicles(self) -> list:
        return self._get("vehicles")

    # -- control (events & buttons) ---------------------------------------------
    def _vehicle_path(self) -> str:
        vid = self._vehicle_id
        if not vid:
            vid = (self._get("player").get("CurrentVehicle") or "")
            self._vehicle_id = vid
        if not vid:
            raise BridgeError("player is not in a vehicle - nothing to control")
        return f"vehicles/{urllib.parse.quote(vid)}"

    def send_event(self, event: str, mode: str = "push"):
        """Fire a vehicle input event.

        mode: 'push' = complete press, 'press' = hold down,
        'release' = let go (pair with 'press').
        Event names: see :data:`catalog.EVENTS` or ``Buttons[].Actions``
        in the vehicle telemetry.
        """
        suffix = {"push": "sendevent", "press": "sendeventpress",
                  "release": "sendeventrelease"}[mode]
        q = urllib.parse.urlencode({"event": event})
        self._get(f"{self._vehicle_path()}/{suffix}?{q}", parse=False)

    def tap(self, event: str):
        """One-shot event (e.g. tap('DoorFrontOpenClose'))."""
        self.send_event(event, "push")

    def press(self, event: str):
        self.send_event(event, "press")

    def release(self, event: str):
        self.send_event(event, "release")

    def hold(self, event: str, duration: float = 0.4):
        """Press, wait, release (for hold-style inputs like MotorStartStop)."""
        self.press(event)
        time.sleep(duration)
        self.release(event)

    def set_button(self, name: str, state: str):
        """Put a stateful cockpit button into an explicit state
        (e.g. set_button('Wiper', 'Interval')). Names and valid states:
        ``Buttons[].Name`` / ``Buttons[].States`` in the vehicle telemetry."""
        q = urllib.parse.urlencode({"button": name, "state": state})
        self._get(f"{self._vehicle_path()}/setbutton?{q}", parse=False)

    def command(self, command: str):
        """Game-level command endpoint (GET /command?Command=...)."""
        q = urllib.parse.urlencode({"Command": command})
        self._get(f"command?{q}", parse=False)

    # -- lifecycle ----------------------------------------------------------------
    def close(self):
        pass  # stateless HTTP - nothing to tear down

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_exc):
        self.close()
