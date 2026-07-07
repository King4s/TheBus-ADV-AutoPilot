"""Gym-style environment for RL experiments on The Bus.

    from thebus_ai_bridge.env import TheBusEnv
    env = TheBusEnv(step_dt=0.1)
    obs = env.reset()                 # waits until you sit in a bus
    obs, reward, done, info = env.step({"steer": 0.0, "throttle": 0.3})

Observation: the decoded telemetry dict (see Telemetry.to_dict()).
Reward: progress toward the next stop, minus speeding, minus harsh
pedal work. done when the mission's next-stop distance drops under 10 m
at standstill. It's a documented starting point - tune it for real
experiments.

Analog actions need the 'pad' extra (vgamepad + ViGEm driver).
"""
from __future__ import annotations

import time

from .bridge import TheBusBridge
from .pad import VirtualPad


class TheBusEnv:
    def __init__(self, step_dt: float = 0.1,
                 bridge: TheBusBridge | None = None, pad=None):
        self.bridge = bridge or TheBusBridge()
        self.pad = pad
        self.step_dt = step_dt
        self._prev_dist = None

    def reset(self) -> dict:
        self.bridge.connect()
        t = self.bridge.wait_in_vehicle()
        if self.pad is None:
            self.pad = VirtualPad()
        self.pad.neutral()
        self._prev_dist = t.next_stop_distance_m
        return t.to_dict()

    def step(self, action: dict):
        self.pad.set_controls(steer=action.get("steer"),
                              throttle=action.get("throttle"),
                              brake=action.get("brake"))
        time.sleep(self.step_dt)
        t = self.bridge.read()
        obs = t.to_dict()

        dist = t.next_stop_distance_m
        progress = 0.0
        if dist is not None and self._prev_dist is not None:
            progress = self._prev_dist - dist  # meters closed this step
        self._prev_dist = dist

        speeding = max(0.0, t.speed_kmh -
                       (t.allowed_speed_kmh or 50.0)) * 0.05
        harshness = 0.2 * float(action.get("brake") or 0.0) ** 2
        reward = progress - speeding - harshness

        done = bool(dist is not None and dist < 10.0 and t.speed_kmh < 0.5)
        return obs, reward, done, {"distance_to_stop_m": dist}

    def close(self):
        if self.pad is not None:
            self.pad.close()
