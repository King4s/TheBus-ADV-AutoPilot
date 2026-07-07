"""Bus autopilot with individually toggleable features.

Drives everything the telemetry interface can see: speed toward the
posted limit, service-stop approach (brake to a smooth halt at the next
timetable stop), door cycles with boarding/deboarding, hold brake,
indicators, engine, lights by the game's own night flag, and hazards on
emergency braking.

What it deliberately does NOT do is steer - the telemetry interface has
no analog inputs and the autopilot only ever touches throttle/brake on
the virtual gamepad. Lane keeping is on you, a vision agent, or the MCP
client. Longitudinal control needs the optional virtual pad
(:mod:`.pad`); every non-pedal feature (doors, lights, indicators,
brakes, engine) works without it.

All features only act while ENGAGED; disengaging neutralizes the pad and
hands everything back to the human (the pad watchdog backs that up if
this process dies).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, fields

from .bridge import BridgeError, Telemetry, TheBusBridge

KMH = 3.6  # m/s -> km/h


@dataclass
class Features:
    speed_control: bool = True
    overspeed_brake: bool = True
    stop_at_stops: bool = True
    auto_doors: bool = True
    auto_hold: bool = True
    auto_engine: bool = True
    auto_lights: bool = True
    auto_indicators: bool = True
    auto_hazards: bool = True
    driver_override: bool = True
    speed_limiter: bool = False
    depart_on_schedule: bool = True

    @classmethod
    def names(cls) -> list:
        return [f.name for f in fields(cls)]

    def as_dict(self) -> dict:
        return {n: getattr(self, n) for n in self.names()}


FEATURE_LABELS = {
    "speed_control": "Speed control (follow the posted limit)",
    "overspeed_brake": "Brake when over the target speed",
    "stop_at_stops": "Service stops: slow down and halt at the next "
                     "timetable stop",
    "auto_doors": "Door cycle at stops (open for boarding, close when done)",
    "auto_hold": "Hold brake at stops (stop brake while dwelling)",
    "auto_engine": "Ignition + engine start on engage; releases the "
                   "fixing brake to set off",
    "auto_lights": "Headlights by the game's own night flag",
    "auto_indicators": "Indicate into and out of bus stops",
    "auto_hazards": "Hazards on emergency braking (pedal floored)",
    "driver_override": "Yield when you brake (tap throttle to resume)",
    "speed_limiter": "Hard speed limiter - never above limiter_kmh, "
                     "even when YOU drive",
    "depart_on_schedule": "Wait for the timetable departure time before "
                          "leaving a stop",
}


@dataclass
class Settings:
    speed_offset_kmh: float = 0.0     # drive limit + offset
    max_speed_kmh: float = 55.0       # city bus - keep it civil
    fallback_limit_kmh: float = 30.0  # when AllowedSpeed reads 0
    limiter_kmh: float = 60.0         # speed_limiter feature: hard cap
    stop_decel: float = 1.0           # m/s^2 comfort braking into stops
    stop_trigger_m: float = 250.0     # start shaping speed this far out
    stop_halt_m: float = 8.0          # inside this: brake to a standstill
    stop_min_dwell_s: float = 4.0     # doors stay open at least this long
    hazard_brake_threshold: float = 0.9  # pedal jammed this far -> hazards
    hazard_duration_s: float = 5.0       # hazards back off after this
    indicator_lead_m: float = 60.0    # indicate this far before a stop
    depart_indicator_s: float = 4.0   # signal out of the stop this long
    lights_manual_pause_s: float = 300.0  # manual light press wins this long
    loop_hz: float = 10.0             # HTTP polling - 10 Hz is plenty


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


class Autopilot:
    """Drives a connected :class:`TheBusBridge`. Thread-safe toggles."""

    # speed PI (throttle)
    KP, KI = 0.10, 0.03

    def __init__(self, bridge: TheBusBridge, features: Features | None = None,
                 settings: Settings | None = None, autosave: bool = False,
                 pad=None):
        self.bridge = bridge
        self.features = features or Features()
        self.settings = settings or Settings()
        self.autosave = autosave  # persist every toggle to the config file
        self._pad = pad           # injectable for tests; lazily created
        self._pad_failed = False
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._engaged = False
        self.log = deque(maxlen=60)
        self._reset_runtime()

    @classmethod
    def from_config(cls, bridge: TheBusBridge, autosave: bool = True):
        """Autopilot with features/settings from the user's config file
        (%APPDATA%\\thebus-ai-bridge\\config.json); changes saved back."""
        from . import config
        feats, sets = config.load()
        return cls(bridge, features=feats, settings=sets, autosave=autosave)

    def save_config(self):
        from . import config
        try:
            config.save(self.features, self.settings)
        except OSError as e:
            self._say(f"config save failed: {e}")

    def _reset_runtime(self):
        self._integral = 0.0
        self._mode = "off"       # off | drive | approach | dwell | depart | hold
        self._target_kmh = 0.0
        self._cmd_throttle = 0.0
        self._cmd_brake = 0.0
        self._dwell_since = None      # monotonic when doors opened
        self._doors_by_us = []        # door events we opened, to close again
        self._depart_until = 0.0      # indicator-out timer
        self._hazards_by_us = False
        self._hazard_until = 0.0
        self._hold_by_us = False      # stop brake we engaged
        self._holding = False         # driver_override yield
        self._brake_ticks = 0
        self._lights_by_us = None     # state we last commanded
        self._lights_warned = False   # said "no light switch" once
        self._fix_tap_at = 0.0        # last fixing-brake release attempt
        self._lights_pause_until = 0.0
        self._indicated_stop = None   # StopName we already signaled for
        self._blinker_owned = False   # WE switched the indicator on
        self._engine_was_on = False
        self._limiter_active = False
        self._stopped_at = None       # StopName we are currently serving

    # -- lifecycle ----------------------------------------------------------
    def start(self):
        """Start the control loop (does not engage by itself)."""
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="thebus-autopilot", daemon=True)
            self._thread.start()

    def stop(self):
        """Disengage and stop the loop."""
        self.disengage()
        self._stop.set()
        self._thread = None

    @property
    def engaged(self) -> bool:
        return self._engaged

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def target_kmh(self) -> float:
        return self._target_kmh

    def engage(self):
        with self._lock:
            if self._engaged:
                return
            self._reset_runtime()
            self._engaged = True
            self._mode = "drive"
            self._say("engaged")
        self.start()

    def disengage(self, reason: str = ""):
        with self._lock:
            if not self._engaged:
                return
            self._engaged = False
            self._mode = "off"
            self._say(f"disengaged{' - ' + reason if reason else ''}")
            self._release_all()

    def _release_all(self):
        if self._pad is not None:
            try:
                self._pad.neutral()
            except Exception:
                pass
        self._doors_by_us = []  # doors stay as they are - never slam them
        if self._hazards_by_us:
            self._try_tap("ToggleWarningLights")
            self._hazards_by_us = False
        if self._hold_by_us:
            self._try_tap("StopBrakeOnOff")
            self._hold_by_us = False
        if self._blinker_owned:  # never leave OUR blinker flashing
            try:
                self.bridge.indicate(0, tries=4)
            except BridgeError:
                pass
            self._blinker_owned = False

    # -- toggles (thread-safe, persisted with autosave) ------------------------
    def set_feature(self, name: str, on: bool):
        if name not in Features.names():
            raise KeyError(name)
        with self._lock:
            setattr(self.features, name, bool(on))
            self._say(f"{name} {'on' if on else 'off'}")
        if self.autosave:
            self.save_config()

    def update_settings(self, **kw):
        """Change settings values (thread-safe; persisted with autosave)."""
        with self._lock:
            for k, v in kw.items():
                if not hasattr(self.settings, k):
                    raise KeyError(k)
                setattr(self.settings, k, type(getattr(self.settings, k))(v))
        if self.autosave:
            self.save_config()

    def _say(self, msg: str):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        self.log.append(line)

    # -- pad ------------------------------------------------------------------
    def _ensure_pad(self):
        if self._pad is not None or self._pad_failed:
            return self._pad
        try:
            from .pad import VirtualPad
            self._pad = VirtualPad()
            self._say("virtual gamepad connected")
        except Exception as e:
            self._pad_failed = True
            self._say(f"no virtual gamepad ({e}); pedal features off")
        return self._pad

    def _pedals(self, throttle: float, brake: float):
        pad = self._ensure_pad()
        self._cmd_throttle, self._cmd_brake = throttle, brake
        if pad is not None:
            pad.set_controls(throttle=throttle, brake=brake)

    def _try_tap(self, event: str):
        try:
            self.bridge.tap(event)
        except BridgeError as e:
            self._say(f"event {event} failed: {e}")

    # -- main loop ----------------------------------------------------------------
    def _loop(self):
        period = 1.0 / max(1.0, self.settings.loop_hz)
        while not self._stop.wait(period):
            if not self._engaged:
                continue
            try:
                t = self.bridge.read()
            except BridgeError:
                continue
            if not t.in_vehicle:
                self.disengage("left the vehicle")
                continue
            with self._lock:
                if self._engaged:
                    self._tick(t)

    def _tick(self, t: Telemetry):
        now = time.monotonic()
        s = self.features

        # engine off while engaged means "we're done" - hand everything back
        if self._engine_was_on and not t.engine_on:
            self.disengage("engine switched off")
            return
        if t.engine_on:
            self._engine_was_on = True
        elif s.auto_engine:
            self._start_engine(t)

        if s.auto_lights:
            self._auto_lights(t, now)
        if s.auto_hazards:
            self._auto_hazards(t, now)

        # driver override: the human pressing the brake wins
        if self._holding:
            if t.throttle > 0.15:  # human tapped the throttle: resume
                self._holding = False
                self._brake_ticks = 0
                self._mode = "drive"
                self._say("resuming")
            else:
                self._pedals(0.0, 0.0)
                return
        elif s.driver_override and self._override_active(t):
            self._holding = True
            self._mode = "hold"
            self._say("driver brake - holding (tap throttle to resume)")
            self._pedals(0.0, 0.0)
            self._integral = 0.0
            return

        # stop pipeline: approach -> dwell -> depart
        if s.stop_at_stops and self._stop_pipeline(t, now):
            return

        self._mode = "drive"
        # a blinker we own has no reason left (interrupted approach or
        # depart, feature toggled off, ...) - take it back off
        if self._blinker_owned and self._signal(t, 0):
            self._blinker_owned = False
        self._speed_control(t)

    # -- vehicle-adaptive signaling ---------------------------------------------
    def _signal(self, t: Telemetry, want: int) -> bool:
        """One step toward indicator state ``want`` (-1/0/+1), adapted to
        THIS bus: direct events when it has them, stalk notches
        (IndicatorUp/Down) otherwise. Returns True once telemetry
        confirms - call again next tick until it does."""
        cur = t.indicator
        if cur == want:
            return True
        acts = t.events
        direct = {1: "SetIndicatorUp", -1: "SetIndicatorDown",
                  0: "SetIndicatorOff"}[want]
        if direct in acts:
            self._try_tap(direct)
        elif want > cur:
            self._try_tap("IndicatorUp")
        else:
            self._try_tap("IndicatorDown")
        return False

    # -- engine / lights / hazards -------------------------------------------
    def _start_engine(self, t: Telemetry):
        if not t.ignition_on:
            # ignition control differs per bus ('Fake Ignition' on the
            # Scania) - find whatever THIS bus calls it
            btn = t.button_like("Fake Ignition", "Ignition")
            if btn is not None and "On" in (btn.get("States") or []):
                try:
                    self.bridge.set_button(btn["Name"], "On")
                except BridgeError:
                    pass
        try:
            self.bridge.hold("MotorStartStop", 0.4)
            self._say("engine start")
        except BridgeError as e:
            self._say(f"engine start failed: {e}")

    def _auto_lights(self, t: Telemetry, now: float):
        if now < self._lights_pause_until:
            return
        btn = t.button_like("Light Switch", "Lightswitch", "Light")
        states = (btn or {}).get("States") or []
        if btn is None or len(states) < 2:
            if not self._lights_warned:
                self._lights_warned = True
                self._say("this bus has no light switch telemetry - "
                          "auto_lights idle")
            return
        # pick from THIS bus's own state list ('Headlights' when named,
        # otherwise the strongest = last state; 'Off' or the first state)
        on_state = "Headlights" if "Headlights" in states else states[-1]
        off_state = "Off" if "Off" in states else states[0]
        want = on_state if t.is_night else off_state
        state = btn.get("State", "")
        if not state or state == want:
            self._lights_by_us = state or want
            return
        if self._lights_by_us is not None and state != self._lights_by_us:
            # a human moved the switch since we last did - back off a while
            self._lights_pause_until = now + self.settings.lights_manual_pause_s
            self._lights_by_us = None
            self._say("manual light switch - pausing auto_lights")
            return
        try:
            self.bridge.set_button(btn["Name"], want)
            self._lights_by_us = want
            self._say(f"lights {want.lower()}")
        except BridgeError:
            pass

    def _auto_hazards(self, t: Telemetry, now: float):
        braking_hard = (t.brake >= self.settings.hazard_brake_threshold
                        and t.speed_kmh > 20)
        if braking_hard and not t.warning_lights:
            self._try_tap("ToggleWarningLights")
            self._hazards_by_us = True
            self._hazard_until = now + self.settings.hazard_duration_s
        elif (self._hazards_by_us and t.warning_lights
              and now > self._hazard_until
              and t.brake < self.settings.hazard_brake_threshold):
            self._try_tap("ToggleWarningLights")
            self._hazards_by_us = False

    def _override_active(self, t: Telemetry) -> bool:
        """Human brake detection: measured brake well above what WE command."""
        if t.brake > self._cmd_brake + 0.3 and t.speed_kmh > 1:
            self._brake_ticks += 1
        else:
            self._brake_ticks = 0
        return self._brake_ticks >= 2

    # -- service stops ----------------------------------------------------------
    def _stop_pipeline(self, t: Telemetry, now: float) -> bool:
        """Approach/dwell/depart at the next timetable stop.
        Returns True when it owns the pedals this tick."""
        st = self.settings
        dist = t.next_stop_distance_m
        stop_name = t.next_stop.get("StopName", "")

        # currently dwelling at a stop?
        if self._mode == "dwell":
            self._dwell(t, now)
            return True

        # departing: keep the indicator promise, otherwise normal driving
        if self._mode == "depart":
            if now < self._depart_until:
                if self._blinker_owned:
                    self._signal(t, -1)   # pull out (right-hand traffic)
            elif not self._blinker_owned or self._signal(t, 0):
                self._blinker_owned = False
                self._mode = "drive"
            self._speed_control(t)
            return True

        if dist is None or not stop_name:
            return False

        # approaching: indicate, shape speed as v = sqrt(2*a*d), halt inside
        if dist <= st.stop_trigger_m:
            if (self.features.auto_indicators
                    and dist <= st.indicator_lead_m
                    and self._indicated_stop != stop_name
                    and (t.indicator == 0 or self._blinker_owned)):
                self._blinker_owned = True
                self._indicated_stop = stop_name
            if self._blinker_owned and self._indicated_stop == stop_name:
                self._signal(t, 1)        # curb side (right-hand traffic)
            if dist <= st.stop_halt_m or (t.at_stop and t.speed_kmh < 5):
                self._pedals(0.0, 0.6 if t.speed_kmh > 0.5 else 0.8)
                if t.speed_kmh < 0.5:
                    self._begin_dwell(t, now, stop_name)
                else:
                    self._mode = "approach"
                return True
            v_cap_kmh = ((2 * st.stop_decel * max(0.0, dist - st.stop_halt_m))
                         ** 0.5) * KMH
            self._mode = "approach"
            self._speed_control(t, cap_kmh=max(5.0, v_cap_kmh))
            return True
        return False

    def _begin_dwell(self, t: Telemetry, now: float, stop_name: str):
        self._mode = "dwell"
        self._stopped_at = stop_name
        self._dwell_since = now
        self._pedals(0.0, 0.0)
        if self.features.auto_hold and not self._hold_by_us:
            # not listed under Buttons Actions on every bus, but the
            # event works game-wide (unknown events are simply ignored)
            self._try_tap("StopBrakeOnOff")
            self._hold_by_us = True
        if self.features.auto_doors and not t.doors_open:
            # door events differ per bus (an 18m has a 4th door, a midi
            # bus only two) - prefer the ones THIS bus lists, fall back
            # to the front door event which every bus understands
            from .catalog import DOOR_EVENTS
            avail = ([e for e in DOOR_EVENTS if t.has_event(e)]
                     or [DOOR_EVENTS[0]])
            self._try_tap(avail[0])              # front door
            self._doors_by_us = [avail[0]]
            deboarding = int(
                t.current_stop.get("DeboardingPeopleCount", 0) or 0)
            if deboarding > 0 and len(avail) > 1:
                self._try_tap(avail[1])          # middle door
                self._doors_by_us.append(avail[1])
        self._say(f"serving stop: {stop_name or '(unnamed)'}")

    def _dwell(self, t: Telemetry, now: float):
        st = self.settings
        self._pedals(0.0, 0.0)  # hold brake carries the bus
        if self._blinker_owned:
            self._signal(t, 0)  # approach blinker off while we dwell
        if now - (self._dwell_since or now) < st.stop_min_dwell_s:
            return
        if t.boarding_pending > 0:
            return
        if self.features.depart_on_schedule and self._before_departure(t):
            return
        # done: close what we opened, release the hold, signal out
        if self.features.auto_doors and self._doors_by_us:
            if t.doors_open:
                for ev in self._doors_by_us:
                    self._try_tap(ev)
            self._doors_by_us = []
            return  # give the doors a tick to swing shut
        if t.doors_open:
            return  # human-opened doors: never pull away through them
        if self._hold_by_us:
            self._try_tap("StopBrakeOnOff")
            self._hold_by_us = False
        # pull-out blinker: the depart branch drives it (vehicle-adaptive)
        self._blinker_owned = self.features.auto_indicators
        self._depart_until = now + st.depart_indicator_s
        self._mode = "depart"
        self._say(f"departing {self._stopped_at or ''}".rstrip())
        self._stopped_at = None
        self._dwell_since = None

    def _before_departure(self, t: Telemetry) -> bool:
        """True while the timetable says it's too early to leave."""
        dep = (t.current_stop or {}).get("DepartureTime") or ""
        now = t.game_time
        # ISO strings compare lexicographically
        return bool(dep and now and now < dep)

    # -- speed ----------------------------------------------------------------
    def _speed_control(self, t: Telemetry, cap_kmh: float | None = None):
        st = self.settings
        limit = t.allowed_speed_kmh or st.fallback_limit_kmh
        target = _clamp(limit + st.speed_offset_kmh, 0.0, st.max_speed_kmh)
        if cap_kmh is not None:
            target = min(target, cap_kmh)
        if self.features.speed_limiter:
            target = min(target, st.limiter_kmh)
        self._target_kmh = target

        if not self.features.speed_control and cap_kmh is None:
            return
        if t.gear_selector == "Reverse":
            self._pedals(0.0, 0.0)
            return
        if t.fixing_brake or (self._hold_by_us and self._mode == "drive"):
            # release the hold we own; release the fixing brake to set off
            # (auto_engine, like the ETS2 bridge) - otherwise never fight it
            if self._hold_by_us:
                self._try_tap("StopBrakeOnOff")
                self._hold_by_us = False
            if t.fixing_brake:
                now = time.monotonic()
                if (self.features.auto_engine and target > 0
                        and now - self._fix_tap_at > 2.0):
                    self._fix_tap_at = now
                    self._try_tap("FixingBrake")
                    self._say("releasing the fixing brake")
                self._pedals(0.0, 0.0)
                return
        if t.gear_selector == "Neutral" and t.engine_on:
            self._try_tap("SetGearD")

        err_kmh = target - t.speed_kmh
        dt = 1.0 / max(1.0, st.loop_hz)
        if err_kmh > 0:
            self._integral = _clamp(self._integral + err_kmh * dt, 0.0, 8.0)
            throttle = _clamp(self.KP * err_kmh + self.KI * self._integral,
                              0.0, 0.85)
            self._pedals(throttle, 0.0)
        else:
            self._integral = 0.0
            over = -err_kmh
            if self.features.overspeed_brake and over > 3.0:
                self._pedals(0.0, _clamp(0.08 * (over - 3.0), 0.1, 0.6))
            else:
                self._pedals(0.0, 0.0)

    # -- status -----------------------------------------------------------------
    def status(self) -> dict:
        return {
            "engaged": self._engaged,
            "mode": self._mode,
            "target_kmh": round(self._target_kmh, 1),
            "holding": self._holding,
            "serving_stop": self._stopped_at,
            "features": self.features.as_dict(),
            "log": list(self.log)[-10:],
        }
