"""Analog driving controls through a virtual Xbox 360 gamepad.

The Bus telemetry interface has events and buttons but NO analog inputs,
so steering/throttle/brake go through a ViGEm virtual controller that the
game picks up like any real gamepad. Requires the optional extra:

    pip install -e python[pad]        # installs vgamepad
    (first use installs the ViGEmBus driver; vgamepad ships the installer)

In-game, once the pad exists the game binds it with its default gamepad
profile: left stick X = steering, right trigger = throttle, left trigger
= brake. If you changed the game's gamepad bindings, match them here via
the setter mapping.

Conventions:
  * steer is [-1, 1], POSITIVE = RIGHT (stick convention; note this is
    the opposite sign of the SCS/ETS2 bridge).
  * throttle/brake are [0, 1].

Safety: a watchdog neutralizes the pad if :meth:`set_controls` isn't
called for 2 s while any control is non-neutral (crashed script = the
bus doesn't keep accelerating), and :meth:`close` releases everything.
A human can always grab the wheel: the game mixes all devices.
"""
from __future__ import annotations

import threading
import time


class PadUnavailable(RuntimeError):
    """vgamepad / the ViGEmBus driver is not installed."""


class VirtualPad:
    """Virtual Xbox 360 controller for analog bus driving."""

    WATCHDOG_S = 2.0

    def __init__(self):
        try:
            import vgamepad
        except ImportError as e:
            raise PadUnavailable(
                "analog driving needs the 'vgamepad' package (and the "
                "ViGEmBus driver): pip install vgamepad") from e
        try:
            self._pad = vgamepad.VX360Gamepad()
        except Exception as e:  # driver missing / service down
            raise PadUnavailable(f"could not create the virtual gamepad "
                                 f"(ViGEmBus driver installed?): {e}") from e
        self._lock = threading.RLock()
        self._steer = 0.0
        self._throttle = 0.0
        self._brake = 0.0
        self._last_update = time.monotonic()
        self._closed = False
        self._wd = threading.Thread(target=self._watchdog, daemon=True,
                                    name="thebus-pad-watchdog")
        self._wd.start()

    # -- controls ---------------------------------------------------------
    def set_controls(self, steer: float | None = None,
                     throttle: float | None = None,
                     brake: float | None = None):
        """Set analog inputs; ``None`` leaves a value unchanged. Values
        persist until changed (call again within 2 s to keep non-neutral
        values alive - the autopilot loop does this for you)."""
        with self._lock:
            if steer is not None:
                self._steer = max(-1.0, min(1.0, float(steer)))
            if throttle is not None:
                self._throttle = max(0.0, min(1.0, float(throttle)))
            if brake is not None:
                self._brake = max(0.0, min(1.0, float(brake)))
            self._last_update = time.monotonic()
            self._apply()

    def neutral(self):
        """Center the stick, release both pedals."""
        self.set_controls(steer=0.0, throttle=0.0, brake=0.0)

    @property
    def controls(self) -> tuple:
        with self._lock:
            return (self._steer, self._throttle, self._brake)

    def _apply(self):
        if self._closed:
            return
        pad = self._pad
        pad.left_joystick_float(x_value_float=self._steer, y_value_float=0.0)
        pad.right_trigger_float(value_float=self._throttle)
        pad.left_trigger_float(value_float=self._brake)
        pad.update()

    def _watchdog(self):
        while not self._closed:
            time.sleep(0.25)
            with self._lock:
                idle = time.monotonic() - self._last_update
                nonneutral = (self._steer or self._throttle or self._brake)
                if nonneutral and idle > self.WATCHDOG_S:
                    self._steer = self._throttle = self._brake = 0.0
                    self._apply()

    # -- lifecycle ----------------------------------------------------------
    def close(self):
        """Neutralize and unplug the virtual controller."""
        with self._lock:
            if self._closed:
                return
            self._steer = self._throttle = self._brake = 0.0
            self._apply()
            self._closed = True
        try:
            self._pad.reset()
            self._pad.update()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
