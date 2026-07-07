# TheBus-ADV-AutoPilot — Installation

*(English below)*

**Avanceret autopilot til The Bus (TML-Studios).** Fart, stoppesteder,
døre, blinklys og lys styres af deterministisk, regelbaseret logik oven
på spillets **officielle telemetri-interface** — alt kører lokalt og
forudsigeligt, og dine egne pedaler vinder altid. Ingen Python
nødvendig. (AI-connector-delen til udviklere ligger i kildekoden.)

## Dansk

1. **I spillet:** Indstillinger → slå **"Enable Telemetry Interface"**
   til → genstart spillet (én gang).
2. Pak zippen ud i en mappe, fx `C:\TheBusCopilot\`.
3. Start **`TheBus Copilot.exe`** — sæt dig i en bus, og panelet viser
   live-data. Tryk **ENGAGE** (eller `Ctrl+Alt+A` inde i spillet).
4. *(Valgfrit — start automatisk med spillet)*: Steam → højreklik
   The Bus → **Egenskaber… → Startindstillinger**:

   ```
   "C:\TheBusCopilot\TheBusSteamCompanion.exe" %command%
   ```

5. *(Valgfrit — analog gas/bremse)*: første gang autopiloten skal bruge
   pedalerne, kræves ViGEmBus-driveren:
   [github.com/nefarius/ViGEmBus/releases](https://github.com/nefarius/ViGEmBus/releases)
   (driveren til den virtuelle Xbox-controller).

**Genveje i spillet** (virker også i fuldskærm):
`Ctrl+Alt+A` autopilot til/fra · `Ctrl+Alt+R` slip alt ·
`Ctrl+Alt+L` fartbegrænser · `Ctrl+Alt+S` stoppested-service ·
`Ctrl+Alt+D` fordør · `Ctrl+Alt+W` havariblink.
Overlay: klik "overlay" øverst i panelet (kræver borderless/windowed).

**Sikkerhed:** din bremse vinder altid (autopiloten går i HOLD);
`Ctrl+Alt+R` eller den røde knap slipper alt; dør/bremse-events røres
aldrig ved exit. Watchdog nulstiller pedalerne, hvis appen dør.

## English

**Advanced autopilot for The Bus.** Deterministic, rule-based
automation (speed, timetable stops, doors, indicators, lights) on the
game's official telemetry interface — everything runs locally and your
own pedals always win. (The AI connector for developers lives in the
source repo.)

1. **In the game:** Options → enable **"Enable Telemetry Interface"**
   → restart the game (once).
2. Unzip to a folder, e.g. `C:\TheBusCopilot\`.
3. Run **`TheBus Copilot.exe`**, sit in a bus, press **ENGAGE**
   (or `Ctrl+Alt+A` in-game).
4. *(Optional — auto-start with the game)*: Steam → The Bus →
   **Properties… → Launch Options**:
   `"C:\TheBusCopilot\TheBusSteamCompanion.exe" %command%`
5. *(Optional — analog throttle/brake)*: install the ViGEmBus driver
   (virtual Xbox controller):
   [github.com/nefarius/ViGEmBus/releases](https://github.com/nefarius/ViGEmBus/releases)

Hotkeys: `Ctrl+Alt+A` autopilot, `R` release all, `L` limiter,
`S` service stops, `D` front door, `W` warning lights.

Source, docs, Stream Deck plugin & MCP server (for developers):
https://github.com/King4s/TheBus-ADV-AutoPilot
