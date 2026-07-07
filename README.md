# TheBus-ADV-AutoPilot (with AI connector)

**An advanced autopilot for The Bus (TML-Studios).** The driving
automation is deterministic, rule-based control logic (PI speed
control, comfort-braking curves, door/timetable state machines) on top
of the game's **official telemetry interface** (HTTP, no memory hacks,
no fake keypresses) — it reads the same values the dashboard shows and
reacts predictably, and your own pedals always win.

The **AI connector** is the optional second half: the same bridge
exposes the bus to programmatic drivers — scripted agents, RL loops,
vision models, and LLMs via an MCP server (so Claude *can* work a bus
line if you wire it up). Opt-in developer tooling; the autopilot works
without it. Sibling project of
[ets2-ai-connector](../ets2-ai-connector), same architecture adapted to
what The Bus offers.

```
The Bus (UE5, telemetry interface enabled)
 ├─ HTTP server 127.0.0.1:37337 ──JSON──►  telemetry (player, vehicle,
 │                                          world, mission, map, route)
 ├─ /vehicles/<id>/sendevent...  ◄──GET──  events (doors, gear, brakes,
 │                                          indicators, lights, ticketing)
 └─ virtual Xbox pad (ViGEm)     ◄────────  analog steer/throttle/brake
                                       ▲
                python: TheBusBridge ──┘   (zero-dependency core)
                ├─ Autopilot      speed / service stops / doors / lights
                ├─ GUI            feature toggles + live panel (tkinter)
                ├─ Stream Deck    .sdPlugin (coexists with TML's official
                │                 The Bus plugin)
                ├─ TheBusEnv      gym-style step()/reset()
                ├─ capture        PNG screenshots for vision agents
                ├─ CLI            status | monitor | demo | gui | autopilot | mcp
                └─ MCP server     14 tools so an LLM can drive
```

**Key difference from the ETS2 bridge:** The Bus has no SCS-style plugin
SDK, so no C++ DLL is needed at all — telemetry and button/event input
are a plain local HTTP API the game ships with. What that API does *not*
carry is analog driving input, so steering/throttle/brake go through a
**virtual gamepad** (ViGEm) that the game sees as a normal controller.
A human can always override: keyboard/wheel input keeps working, and the
pad watchdog neutralizes everything if the client dies.

## Setup

1. In the game: **Options → Enable Telemetry Interface** (BETA), restart.
2. `pip install -e python` — zero-dependency core.
   Optional extras: `python[pad]` (analog driving, installs vgamepad +
   ViGEmBus driver), `python[mcp]` (MCP server), `python[vision]`
   (screenshots), `python[all]`.
3. Start the game, load a route, sit in the bus, then:

   ```
   python -m thebus_ai_bridge status     # one-shot summary
   python -m thebus_ai_bridge monitor    # live dashboard
   ```

4. Let the AI work the line (speed, service stops, doors; YOU steer;
   Ctrl+C hands control back):

   ```
   python -m thebus_ai_bridge demo
   ```

5. Or go straight to the control panel:

   ```
   python -m thebus_ai_bridge gui
   ```

## Autopilot

`Autopilot` does everything the telemetry interface can see, each
behavior individually toggleable:

| Feature | What it does |
|---|---|
| `speed_control` | Follows the posted speed limit (`AllowedSpeed`, PI throttle via the virtual pad) |
| `overspeed_brake` | Brakes when over target |
| `stop_at_stops` | **Service stops**: shapes speed as `v = √(2·a·d)` toward the next timetable stop (distance = haversine of the stop's `GeoLocation` vs the player's). The marker sits mid-bay, so the bus **creeps past it (`stop_creep_kmh`) and halts at the end of the bus bay** — `stop_pull_past_m` (12 m) beyond the marker, detected via the closest-approach tracker; it also halts immediately if the stop zone ends sooner |
| `auto_doors` | Opens the front door at the stop (middle door too when passengers deboard), closes when boarding/deboarding is done. Never closes a door a human opened; never pulls away through open doors |
| `auto_hold` | Stop brake while dwelling at a stop, released on departure |
| `auto_engine` | Ignition + engine start on engage (`MotorStartStop`) |
| `auto_lights` | Headlights by the game's own `NightLightEnabled` flag; a manual flip of the light switch pauses it for 5 min |
| `auto_indicators` | Signals into the stop (right), out of the stop (left), cancels after pull-out |
| `auto_hazards` | Hazards on emergency braking (pedal ≥ 90 % while moving); off again 5 s after the pedal lets up |
| `driver_override` | **Your pedals always win**: braking puts the autopilot in HOLD — tap the throttle to resume |
| `speed_limiter` | Hard cap at `limiter_kmh` (off by default) |
| `depart_on_schedule` | Waits at the stop until the timetable's `DepartureTime` |

Built-in (no toggle): **switching the engine off disengages the
autopilot** — ignition off means "we're done".

What the autopilot deliberately does NOT do is **steer** — the wheel
stays with you, a vision agent, or the MCP client. (`/roadmap` does
expose the map's full lane geometry if you ever want to build lane
following on top.)

```python
from thebus_ai_bridge import TheBusBridge
from thebus_ai_bridge.autopilot import Autopilot

with TheBusBridge() as bus:
    ap = Autopilot(bus)
    ap.settings.speed_offset_kmh = -5   # drive 5 under the limit
    ap.engage()                          # ap.disengage() hands back
```

Headless: `python -m thebus_ai_bridge autopilot [--offset -5] [--max 50]`.

### Configuration

Every feature toggle and tunable persists in one human-editable file:

```
%APPDATA%\thebus-ai-bridge\config.json    (python -m thebus_ai_bridge config
                                           shows the path and current values)
```

The GUI, the autopilot CLI and the MCP server all load it on start and
save every change back. Unknown keys are ignored and broken values fall
back to defaults, so hand-editing is safe.

## GUI (no Stream Deck needed)

`python -m thebus_ai_bridge gui` — dark-mode control panel: live
telemetry (speed, limit, gear, doors, next stop + distance),
ENGAGE/RELEASE, a checkbox per autopilot feature, spinners for limit
offset / max speed / limiter cap / bay pull-past, manual buttons
(doors 1–3, clearance, kneeling, indicators, warning lights, lights,
wiper, engine, fixing/stop brake, gear R/N/D, horn-hold), a log, and a
big red RELEASE CONTROL button.

Usable **while you drive**, without alt-tabbing out of the game:

* **Global hotkeys** (work in any display mode, even exclusive
  fullscreen, while the game has focus):
  `Ctrl+Alt+A` autodrive engage/release, `Ctrl+Alt+R` RELEASE ALL,
  `Ctrl+Alt+L` limiter on/off, `Ctrl+Alt+S` service stops on/off,
  `Ctrl+Alt+D` front door, `Ctrl+Alt+W` warning lights.
* **Overlay** (header link) — a compact frameless always-on-top strip:
  live speed + mode/target, ENGAGE/RELEASE, DOOR, and STOPS/LIMIT
  buttons that light green when on. Drag it by the speed number. Like
  every overlay it can only draw over the game in **borderless /
  windowed** mode; in exclusive fullscreen use the hotkeys instead.
* **on top** checkbox — keeps the full panel above the game window.

## Stream Deck

`streamdeck_plugin\com.thebusaibridge.sdPlugin` runs inside the official
Elgato app, so it works on all connected decks and **coexists with TML's
own official The Bus plugin** (use theirs for cockpit switches, this one
for the AI bridge). Actions:

* **Autopilot (engage/release)** — one key for both, same semantics as
  the GUI button (title shows the target, `DWELL`/`HOLD` while serving a
  stop or yielding to your brake).
* **Autopilot Feature** — toggle any single feature chosen in the key's
  inspector (dropdown generated from `FEATURE_LABELS`).
* **Bus Button** — tap or hold any of the ~60 cataloged events (grouped
  dropdown: doors, signals, lights, driving, cabin, ticketing). Stateful
  ones (doors, hazards, fixing brake, indicators, engine) light the key
  green while active.
* **Speed Display** — live speed plus limit/target; press toggles speed
  control.

Dial actions (GALLEON 100 SD, Stream Deck +; the LCD shows live values):

* **Speed Offset (dial)** — rotate = drive ±1 km/h around the posted
  limit (persisted), press/touch = back to the limit.
* **Speed Limiter (dial)** — rotate = the hard cap (20–90 km/h,
  persisted), press/touch = limiter on/off; LCD shows `≤60 km/h` armed
  or `off · 60`.
* **Wiper (dial)** — rotate = Off/Interval/On/Fast, press = off; LCD
  mirrors the live wiper state from telemetry.
* **A/C (dial)** — rotate = temperature, press = next fan intensity;
  LCD shows `22.0°  fan 2`.
* **Drive Dial A/B** — two functions on ONE dial: **A** = limit offset,
  **B** = limiter cap. **Long-press switches A↔B** (LCD shows
  `A · LIMIT OFFSET` / `B · LIMITER`), short press = reset offset (A) /
  limiter on-off (B).

Build & install:

```
powershell streamdeck_plugin\build.ps1     # launcher.exe + icons + assemble
powershell streamdeck_plugin\install.ps1   # copy into the Elgato app, restart it
```

The app launches `thebuslauncher.exe` (tiny C++ shim) which runs
`python -m thebus_ai_bridge.deck_plugin`; the interpreter path is pinned
in `launcher.cfg`, diagnostics land in `deck_plugin.log`/`launcher.log`
inside the installed plugin folder. Protocol test without the app or the
game: `python python\tests\test_deck_plugin.py`.

## Start with the game from Steam

Steam has no plugin system, but its **Launch Options** can wrap the game
command — so the bridge starts and stops WITH the game:

1. Build once: `powershell tools\build_app.ps1` → produces
   `dist\TheBus Copilot.exe` (the GUI as a single exe, no Python needed)
   and `dist\TheBusSteamCompanion.exe` (the wrapper).
2. Steam → The Bus → **Properties… → Launch Options**:

   ```
   "C:\thebus-ai-connector\dist\TheBusSteamCompanion.exe" %command%
   ```

Every launch of The Bus from Steam now also opens the Copilot (skipped
if one is already running), and closing the game closes it again
(clean `WM_CLOSE` first; a kill-on-close job object reaps stragglers —
and the pad watchdog neutralizes controls in any case). Diagnostics:
`dist\companion.log`.

## Python API

```python
from thebus_ai_bridge import TheBusBridge

with TheBusBridge() as bus:               # connects to 127.0.0.1:37337
    t = bus.read()                        # snapshot (player+vehicle+world+mission)
    print(t.speed_kmh, t.allowed_speed_kmh, t.gear_selector,
          t.doors_open, t.next_stop["StopName"], t.next_stop_distance_m)

    bus.tap("DoorFrontOpenClose")         # one-shot event
    bus.hold("MotorStartStop", 0.4)       # press + release (engine start)
    bus.set_button("Wiper", "Interval")   # stateful cockpit button
    bus.set_button("Light Switch", "Headlights")
```

Analog driving (needs `[pad]`):

```python
from thebus_ai_bridge.pad import VirtualPad
pad = VirtualPad()                        # appears as an Xbox controller
pad.set_controls(steer=0.1, throttle=0.4) # steer [-1,1], POSITIVE = RIGHT
pad.neutral(); pad.close()                # release everything
```

**Conventions (straight from the game — different from the SCS/ETS2 bridge!):**

* `Speed` / `AllowedSpeed` are **km/h** already.
* Pad steering is `[-1, 1]` and **positive steers RIGHT** (stick convention).
* Booleans arrive as the strings `"true"`/`"false"`; the `Telemetry`
  properties decode them to real `bool`s.
* `GeoLocation` is `[latitude, longitude]` — the Berlin map is
  geo-referenced, so haversine distances against stop positions just work.
* `Rotation.Yaw` is degrees, clockwise positive.

**Buses differ — the bridge asks the vehicle.** Cockpit button names,
event sets and lamp names vary per bus (the Scania Citywide has direct
`SetIndicatorOff/Up/Down` events; the MAN Lion's City DD only has stalk
notches `IndicatorUp/Down`; some lamps simply don't exist on some
buses). The `Telemetry` capability helpers (`events`, `has_event()`,
`button_like()`, `lamp_any()`) discover what the current bus supports,
and `bridge.indicate(-1|0|+1)` drives the indicator correctly on any
bus — the GUI keys, the Stream Deck actions and the autopilot all use
this. Note `Buttons[].Actions` is not the complete event universe:
events like `StopBrakeOnOff` work even on buses that don't list them,
and unknown events are silently ignored, so sending them is safe.

Event catalog: `python -m thebus_ai_bridge events`, or
`thebus_ai_bridge/catalog.py` (doors, gear, brakes, indicators, lights,
kneeling, wipers, A/C, ticketing/cash…). The authoritative per-bus list
is `Buttons[].Actions`/`States` in the vehicle telemetry — the bridge
accepts any string, the catalog exists for discoverability.

### Telemetry endpoints (as reverse-engineered, game v3.x)

| Endpoint | Contents |
|---|---|
| `/player` | Mode, CurrentVehicle, Location, GeoLocation, Rotation (Yaw!) |
| `/vehicles` · `/vehicles/Current` · `/vehicles/<id>` | Speed, RPM, gear + selector, Steering/Throttle/Brake (combined inputs), FixingBrake, doors (+ per-door StopRequest), IsAtStop, AllowedSpeed, fuel, seats/occupancy, AllLamps, ~107 cockpit Buttons with Actions/States |
| `/world` | LevelName, DateTime, TimeFactor, **NightLightEnabled**, temperature, rain/snow/wind |
| `/mission` | Current/next stop: name, arrival/departure times, **Boarding/DeboardingPeopleCount**, GeoLocation |
| `/map` | Map name, world size |
| `/route` | Route paths |
| `/roadmap` | Full lane geometry of the map (large; the game emits slightly malformed JSON here — the bridge parses it anyway) |
| `/vehicles/<id>/sendevent?event=E` (+`press`/`release` variants) | Fire an input event |
| `/vehicles/<id>/setbutton?button=B&state=S` | Set a stateful button |

### Gym-style environment

```python
from thebus_ai_bridge.env import TheBusEnv
env = TheBusEnv(step_dt=0.1)
obs = env.reset()                                  # waits until you're in a bus
obs, reward, done, info = env.step({"steer": 0.0, "throttle": 0.3})
```

Reward = progress toward the next stop − speeding − harsh braking,
`done` at the stop. A documented starting point — tune it for real
experiments.

### Vision agents

```python
from thebus_ai_bridge.capture import screenshot_png
png = screenshot_png("frame.png")    # game window client area
```

## Let Claude drive (MCP)

```
claude mcp add thebus -- python -m thebus_ai_bridge mcp
```

Tools: `get_status`, `get_telemetry`, `get_raw_vehicle`, `get_mission`,
`get_world`, `drive`, `send_event`, `set_button`, `release_control`,
`screenshot`, plus `autopilot_engage` / `autopilot_disengage` /
`autopilot_set_feature` / `autopilot_status`.

Pattern that works at LLM cadence: engage the autopilot (it owns speed,
service stops, doors, signals), then loop `get_telemetry()` +
`screenshot()` and make small `drive(steer=…)` corrections.

## Safety

* **Pad watchdog:** the virtual gamepad self-neutralizes if no command
  arrives for **2 s** while any control is non-neutral (crashed script =
  the bus doesn't keep accelerating), and `close()` releases everything.
* `release_control` (MCP) / RELEASE CONTROL (GUI) / Ctrl+C in the demo
  neutralize and disengage; door/brake events are never replayed on exit.
* Human inputs are never blocked — the game mixes all devices, and the
  `driver_override` feature yields on your brake pedal.

## Rebuilding / testing

No build step — pure Python. Tests run against a **mock game server**
(real HTTP, captured live JSON), no game needed:

```
python python\tests\test_bridge.py       # 35-check client/protocol roundtrip
python python\tests\test_autopilot.py    # 36-check autopilot behavior
python python\tests\test_deck_plugin.py  # 29-check Stream Deck backend
                                         # (mock Elgato app <-> real backend,
                                         #  keys + dials)
```

`python/tests/data/` holds real captures from a live session (Scania
Citywide LF 18m, Berlin map) — refresh them by hitting the endpoints
with the game running.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `status` says not connected | Game not running, or telemetry not enabled (Options → Enable Telemetry Interface, then restart the game). |
| Player not in a vehicle | Telemetry works from the main menu on, but vehicle data needs you in the driver's seat on a loaded route. |
| `drive()` does nothing | `pad` extra not installed, or the ViGEmBus driver is missing; check `python -c "import vgamepad"`. Also check the game's controller bindings (default gamepad profile: left stick X = steer, RT = throttle, LT = brake). |
| Events do nothing | Wrong vehicle id (bus changed): the bridge re-resolves `Current` automatically on the next `read()`. |
| Doors won't open while driving | The game itself refuses door events above walking speed — stop first. |

## Credits & licenses

* Telemetry interface © TML-Studios / TML Edition GmbH — official game
  feature; protocol details verified against
  [tml-studios/telemetry-stream-deck-plugin](https://github.com/tml-studios/telemetry-stream-deck-plugin)
  (their official Stream Deck plugin — use that for deck control, it
  coexists fine with this bridge) and
  [thatzok/TheBusTelemetry](https://github.com/thatzok/TheBusTelemetry).
* Virtual gamepad: [vgamepad](https://github.com/yannbouteiller/vgamepad) /
  [ViGEmBus](https://github.com/nefarius/ViGEmBus).
* This project: MIT.
