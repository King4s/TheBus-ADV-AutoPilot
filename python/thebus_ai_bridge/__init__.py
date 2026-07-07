"""thebus_ai_bridge - let an AI drive The Bus (TML-Studios).

Telemetry + events through the game's official telemetry interface
(HTTP, port 37337); analog steering/throttle/brake through a virtual
gamepad (optional extra).

    from thebus_ai_bridge import TheBusBridge

    with TheBusBridge() as bus:
        t = bus.read()
        print(t.speed_kmh, t.next_stop.get("StopName"))
        bus.tap("DoorFrontOpenClose")
"""
from .bridge import (BridgeError, GameNotRunning, Telemetry,  # noqa: F401
                     TheBusBridge)

__version__ = "0.1.0"
__all__ = ["TheBusBridge", "Telemetry", "BridgeError", "GameNotRunning"]
