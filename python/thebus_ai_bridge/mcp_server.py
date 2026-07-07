"""MCP server exposing the bus to LLM agents (e.g. Claude).

Register with Claude Code:
    claude mcp add thebus -- python -m thebus_ai_bridge mcp

Driving pattern that works at LLM cadence: engage the autopilot for
longitudinal control (speed, service stops, doors) and use drive() only
for steering corrections based on get_telemetry() + screenshot(). All
pad values persist until changed; the pad's watchdog neutralizes them if
this server dies. release_control() always hands back to the human.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from . import catalog
from .autopilot import FEATURE_LABELS, Autopilot
from .bridge import GameNotRunning, TheBusBridge

_bridge: TheBusBridge | None = None
_autopilot: Autopilot | None = None


def _bus() -> TheBusBridge:
    global _bridge
    if _bridge is None:
        b = TheBusBridge()
        b.connect(wait=False)  # raise immediately if the game is not up
        _bridge = b
    return _bridge


def _ap() -> Autopilot:
    global _autopilot
    if _autopilot is None:
        _autopilot = Autopilot.from_config(_bus())
        _autopilot.start()
    return _autopilot


def _summary(b: TheBusBridge) -> dict:
    t = b.read()
    d = t.to_dict()
    d["autopilot_engaged"] = _ap().engaged
    d["autopilot_mode"] = _ap().mode
    return d


def build_server():
    mcp = FastMCP(
        "thebus",
        instructions=(
            "Tools for driving a city bus in The Bus (TML-Studios). The "
            "game must be running with 'Enable Telemetry Interface' on. "
            "Steering is in [-1, 1] and POSITIVE STEERS RIGHT (gamepad "
            "convention). Analog values persist until changed. Recommended: "
            "engage the autopilot for speed/stops/doors and steer with "
            "small drive() corrections from telemetry + screenshots."))

    @mcp.tool()
    def get_status() -> dict:
        """Connection state and a compact driving summary."""
        try:
            b = _bus()
            return {"connected": b.attached, **_summary(b)}
        except GameNotRunning as e:
            return {"connected": False, "error": str(e)}

    @mcp.tool()
    def get_telemetry() -> dict:
        """Decoded telemetry snapshot (speed, gear, doors, stops, world)."""
        return _bus().read().to_dict()

    @mcp.tool()
    def get_raw_vehicle() -> dict:
        """The full raw vehicle JSON: every lamp, wheel, and the complete
        cockpit Buttons array with per-button Actions and States."""
        return _bus().read().vehicle

    @mcp.tool()
    def get_mission() -> dict:
        """Timetable: current/next stop, arrival & departure times,
        boarding/deboarding counts, geo positions."""
        return _bus().mission()

    @mcp.tool()
    def get_world() -> dict:
        """World state: level, in-game clock, night flag, weather."""
        return _bus().world()

    @mcp.tool()
    def drive(steer: float | None = None, throttle: float | None = None,
              brake: float | None = None) -> dict:
        """Set analog controls on the virtual gamepad; omitted values stay
        unchanged and persist. steer in [-1,1], POSITIVE = RIGHT;
        throttle/brake in [0,1]. Needs the 'pad' extra (vgamepad + ViGEm).
        Returns the post-command summary."""
        from .pad import PadUnavailable
        ap = _ap()
        pad = ap._ensure_pad()
        if pad is None:
            raise PadUnavailable("virtual gamepad unavailable - install the "
                                 "'pad' extra (pip install vgamepad)")
        pad.set_controls(steer=steer, throttle=throttle, brake=brake)
        return _summary(_bus())

    @mcp.tool()
    def send_event(event: str, mode: str = "push") -> dict:
        """Fire a vehicle input event. mode: 'push' (default one-shot),
        'press' (hold down), 'release'. Common events: DoorFrontOpenClose,
        DoorMiddleOpenClose, DoorRearOpenClose, ToggleDoorClearance,
        SetGearD/SetGearN/SetGearR, FixingBrake, StopBrakeOnOff,
        MotorStartStop (press+release), SetIndicatorDown/Up/Off,
        ToggleWarningLights, Horn, Lightswitch, KneelDown/KneelUp,
        Take Cash Money, Coins50... An unknown name returns the catalog."""
        if event not in catalog.EVENTS:
            # not fatal - buses expose extra events - but help discovery
            known = any(event in (b.get("Actions") or [])
                        for b in _bus().read().buttons)
            if not known:
                return {"error": f"unknown event '{event}'",
                        "valid": sorted(catalog.EVENTS)}
        _bus().send_event(event, mode)
        return {"ok": True, "event": event, "mode": mode}

    @mcp.tool()
    def set_button(name: str, state: str) -> dict:
        """Put a stateful cockpit button into an explicit state, e.g.
        set_button('Wiper', 'Interval') or set_button('Light Switch',
        'Headlights'). Names/states: get_raw_vehicle() Buttons array."""
        _bus().set_button(name, state)
        return {"ok": True, "button": name, "state": state}

    @mcp.tool()
    def release_control() -> dict:
        """Neutralize the pad, disengage the autopilot, hand back to the
        human."""
        ap = _ap()
        ap.disengage("released via MCP")
        if ap._pad is not None:
            ap._pad.neutral()
        return {"ok": True}

    @mcp.tool()
    def autopilot_engage() -> dict:
        """Engage the autopilot: starts the engine, follows the posted
        speed limit, brakes smoothly into the next timetable stop, opens
        and closes the doors around boarding, holds the stop brake, and
        signals in and out of stops. It does NOT steer - keep steering
        via drive() from screenshots."""
        ap = _ap()
        ap.engage()
        return ap.status()

    @mcp.tool()
    def autopilot_disengage() -> dict:
        """Disengage the autopilot and hand control back."""
        ap = _ap()
        ap.disengage()
        return ap.status()

    @mcp.tool()
    def autopilot_set_feature(name: str, enabled: bool) -> dict:
        """Toggle one autopilot feature. Valid names: speed_control,
        overspeed_brake, stop_at_stops, auto_doors, auto_hold, auto_engine,
        auto_lights, auto_indicators, auto_hazards, driver_override,
        speed_limiter, depart_on_schedule."""
        ap = _ap()
        if name not in FEATURE_LABELS:
            return {"error": f"unknown feature '{name}'",
                    "valid": sorted(FEATURE_LABELS)}
        ap.set_feature(name, enabled)
        return ap.status()

    @mcp.tool()
    def autopilot_status() -> dict:
        """Autopilot state: engaged, mode (drive/approach/dwell/depart/
        hold), target speed, feature flags, recent log lines."""
        return _ap().status()

    @mcp.tool()
    def screenshot() -> Image:
        """PNG screenshot of the game window (the AI's eyes - needs the
        'vision' extra installed and the game window visible)."""
        from .capture import screenshot_png
        return Image(data=screenshot_png(), format="png")

    return mcp


def main():
    build_server().run()


if __name__ == "__main__":
    main()
