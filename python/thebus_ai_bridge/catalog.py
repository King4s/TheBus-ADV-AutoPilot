"""Curated catalog of The Bus input events and stateful cockpit buttons.

Captured live from a Scania Citywide LF 18M (game v3.x, telemetry
interface enabled): the union of every ``Buttons[].Actions`` entry plus
the events used by TML's own Stream Deck plugin. Other buses expose the
same core set; anything vehicle-specific shows up in the ``Buttons``
array of the vehicle telemetry at runtime - the bridge accepts any
string, this catalog exists for discoverability (CLI/GUI/MCP dropdowns).

Events fire via ``bridge.tap/press/release``; stateful buttons are set
via ``bridge.set_button(name, state)``.
"""
from __future__ import annotations

# event name -> short description; grouped for UI dropdowns
EVENT_GROUPS: dict[str, dict[str, str]] = {
    "driving": {
        "MotorStartStop": "engine start/stop (hold-style: press+release)",
        "FixingBrake": "toggle the parking (fixing) brake",
        "StopBrakeOnOff": "toggle the bus-stop holding brake",
        "SetGearD": "gear selector: Drive",
        "SetGearN": "gear selector: Neutral",
        "SetGearR": "gear selector: Reverse",
        "Hingeprotection": "articulation joint-lock alarm on/off",
        "ASRThresholdOn": "traction control on",
        "ASRThresholdOff": "traction control off",
    },
    "signals": {
        "IndicatorUp": "indicator stalk up (right)",
        "IndicatorDown": "indicator stalk down (left)",
        "SetIndicatorOff": "indicator off",
        "SetIndicatorUp": "indicator right on",
        "SetIndicatorDown": "indicator left on",
        "ToggleWarningLights": "hazard lights on/off",
        "Horn": "horn (press/release to hold)",
    },
    "lights": {
        "Lightswitch": "cycle the main light switch",
        "LightSwitchUp": "light switch one notch up",
        "LightSwitchDown": "light switch one notch down",
        "ToggleTravellerLights": "high beam on/off",
        "TempTravellerLights": "headlight flash (hold)",
        "ToggleFogLight": "front fog light on/off",
        "FogBackLight": "rear fog light on/off",
        "ToggleDriversLight": "driver's spot light",
        "TogglePassengersLight": "passenger interior light",
        "ToggleDoorLight": "bus-stop door light",
    },
    "doors": {
        "DoorFrontOpenClose": "door 1 (front) open/close",
        "DoorMiddleOpenClose": "door 2 (middle) open/close",
        "DoorRearOpenClose": "door 3 (rear) open/close",
        "DoorFourthOpenClose": "door 4 open/close (18m buses)",
        "ToggleDoorClearance": "door clearance (passenger self-service)",
        "DriverDoorOpenClose": "driver cab door",
        "ResetStopRequest": "clear the passenger stop request",
        "KneelUp": "kneeling up",
        "KneelDown": "kneeling down (curb side)",
        "LiftUp": "suspension lift up",
        "LiftDown": "suspension lift down",
    },
    "cabin": {
        "WiperUp": "wiper one notch faster",
        "WiperDown": "wiper one notch slower",
        "DriverWindowOpen": "driver window down",
        "DriverWindowClose": "driver window up",
        "AirconditionKeyUp": "A/C temperature up",
        "AirconditionKeyDown": "A/C temperature down",
        "ACIntensity": "A/C fan intensity step",
        "SwitchObservCamera": "cycle the mirror/observation camera",
        "Select Boardcomputer": "board computer select key",
    },
    "ticketing": {
        "Take Cash Money": "take the passenger's cash",
        "CashChangeSelect": "select change amount",
        "Coins5": "give 5 cent change", "Coins10": "give 10 cent change",
        "Coins15": "give 15 cent change", "Coins20": "give 20 cent change",
        "Coins30": "give 30 cent change", "Coins50": "give 50 cent change",
        "Coins60": "give 60 cent change", "Coins100": "give 1 EUR change",
        "Coins200": "give 2 EUR change", "Coins400": "give 4 EUR change",
        "Coins600": "give 6 EUR change", "Coins800": "give 8 EUR change",
    },
}

#: flat {event: description}
EVENTS: dict[str, str] = {
    name: desc
    for group in EVENT_GROUPS.values() for name, desc in group.items()
}

# stateful buttons worth exposing in UIs: name -> known states
# (query Buttons[].States at runtime for the authoritative per-bus list)
BUTTONS: dict[str, list[str]] = {
    "Wiper": ["Off", "Interval", "On", "Fast"],
    "Light Switch": ["Off", "Parking Lights", "Headlights"],
    "Gear Selector": ["Drive", "Neutral", "Reverse"],
    "Fake Ignition": ["Off", "On", "Start"],
    "Parking Brake": ["Primary", "Secondary"],
    "Warning Light": ["Primary", "Secondary"],
    "Door 1": ["On", "Off"],
    "Door 2": ["On", "Off"],
    "Door 3": ["On", "Off"],
    "Air Condition": ["Off", "1", "2", "3"],
}

#: doors in order; index 0 = front. Event per door.
DOOR_EVENTS = ["DoorFrontOpenClose", "DoorMiddleOpenClose",
               "DoorRearOpenClose", "DoorFourthOpenClose"]
