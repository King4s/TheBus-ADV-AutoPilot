"""Dark-mode control panel for The Bus AI bridge (tkinter, stdlib only).

For everyone WITHOUT a Stream Deck - everything the deck plugin does,
plus ways to use it without alt-tabbing out of the game:

  * GLOBAL HOTKEYS (work in any display mode, even exclusive fullscreen,
    while the game has focus):
        Ctrl+Alt+A  autopilot engage / release ("autodrive")
        Ctrl+Alt+R  RELEASE ALL (autopilot off, pad neutral)
        Ctrl+Alt+L  speed limiter on/off
        Ctrl+Alt+S  service stops on/off (stop_at_stops)
        Ctrl+Alt+D  front door open/close
        Ctrl+Alt+W  warning lights
  * OVERLAY (header link) - a compact frameless always-on-top strip:
    live speed + mode, ENGAGE/RELEASE, DOOR, STOPS and LIMIT buttons
    that light green when on. Drag it by the speed number. Like every
    overlay it can only draw over the game in borderless/windowed mode;
    in exclusive fullscreen use the hotkeys instead.
  * "on top" checkbox - keeps the full panel above the game window.

Also: live telemetry, a checkbox per autopilot feature, spinners for
speed offset / max speed / limiter cap / bay pull-past, manual buttons
(doors, indicators, warning lights, engine, brakes, gear, kneeling,
wiper, horn-hold), a log, and a big red RELEASE CONTROL button.

    python -m thebus_ai_bridge gui
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from .autopilot import FEATURE_LABELS, Autopilot
from .bridge import BridgeError, TheBusBridge

BG, PANEL, FG = "#16181d", "#1f2229", "#d7dae0"
DIM, GOOD, WARN, BAD = "#8b90a0", "#4caf50", "#e0b040", "#e05545"
BLUE = "#5a9bd4"


class App:
    POLL_MS = 250
    # system-wide hotkeys (Ctrl+Alt+<key>): usable while the GAME has focus
    HOTKEYS = [
        (1, "A", "autodrive on/off"),
        (2, "R", "RELEASE ALL"),
        (3, "L", "limiter"),
        (4, "S", "service stops"),
        (5, "D", "front door"),
        (6, "W", "warning lights"),
    ]

    def __init__(self, root: tk.Tk, selftest: bool = False):
        self.root = root
        root.title("The Bus AI Bridge")
        root.configure(bg=BG)
        root.minsize(620, 680)

        self.bridge = TheBusBridge()
        self.ap = Autopilot.from_config(self.bridge)
        self.ap.start()
        self._telemetry = None
        self._q = queue.Queue()
        self._alive = True
        self.overlay = None
        self._overlay_pos = "+40+40"

        self._build()
        threading.Thread(target=self._poll_loop, daemon=True,
                         name="gui-poll").start()
        threading.Thread(target=self._hotkey_loop, daemon=True,
                         name="gui-hotkeys").start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_ui()
        if selftest:
            root.after(500, self._toggle_overlay)  # exercise it too
            root.after(2000, self._on_close)

    # -- layout ------------------------------------------------------------
    def _build(self):
        f_head = tk.Frame(self.root, bg=PANEL)
        f_head.pack(fill="x", padx=8, pady=(8, 0))
        self.l_speed = tk.Label(f_head, text="--", font=("Segoe UI", 30,
                                "bold"), bg=PANEL, fg=FG)
        self.l_speed.pack(side="left", padx=12, pady=6)
        self.l_info = tk.Label(f_head, text="waiting for the game ...",
                               justify="left", anchor="w",
                               font=("Consolas", 10), bg=PANEL, fg=DIM)
        self.l_info.pack(side="left", fill="x", expand=True, pady=6)
        self.b_engage = tk.Button(f_head, text="ENGAGE", width=12,
                                  font=("Segoe UI", 11, "bold"),
                                  bg=GOOD, fg="black", relief="flat",
                                  command=self._toggle_engage)
        self.b_engage.pack(side="right", padx=12)

        f_bar = tk.Frame(self.root, bg=BG)
        f_bar.pack(fill="x", padx=10)
        tk.Button(f_bar, text="overlay", bg=BG, fg=BLUE, relief="flat",
                  bd=0, font=("Segoe UI", 9, "underline"),
                  command=self._toggle_overlay).pack(side="left")
        self.var_top = tk.BooleanVar(value=False)
        tk.Checkbutton(f_bar, text="on top", variable=self.var_top, bg=BG,
                       fg=DIM, selectcolor=PANEL, activebackground=BG,
                       activeforeground=FG, font=("Segoe UI", 9),
                       command=lambda: self.root.attributes(
                           "-topmost", self.var_top.get())
                       ).pack(side="left", padx=(6, 0))
        legend = " · ".join(f"^⌥{k} {what}" for _i, k, what in self.HOTKEYS)
        tk.Label(f_bar, text=f"in-game hotkeys (Ctrl+Alt):  {legend}",
                 fg=DIM, bg=BG, font=("Segoe UI", 8)).pack(side="left",
                                                           padx=(10, 0))

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=8)

        # features + tunables
        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        f_feat = tk.LabelFrame(left, text=" autopilot features ", bg=BG,
                               fg=DIM, bd=1, relief="groove",
                               font=("Segoe UI", 9))
        f_feat.pack(fill="both", expand=True, pady=4)
        self._feat_vars = {}
        for name in FEATURE_LABELS:
            var = tk.BooleanVar(value=getattr(self.ap.features, name))
            cb = tk.Checkbutton(
                f_feat, text=FEATURE_LABELS[name], variable=var, anchor="w",
                bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                activeforeground=FG, font=("Segoe UI", 9), wraplength=270,
                justify="left",
                command=lambda n=name, v=var: self._set_feature(n, v.get()))
            cb.pack(fill="x", padx=6)
            self._feat_vars[name] = var

        f_set = tk.LabelFrame(left, text=" tunables ", bg=BG, fg=DIM,
                              bd=1, relief="groove", font=("Segoe UI", 9))
        f_set.pack(fill="x", pady=4)
        self._spin_vars = {}
        spins = [
            ("speed_offset_kmh", "limit offset km/h", -25, 25, 1),
            ("max_speed_kmh", "max speed km/h", 20, 90, 5),
            ("limiter_kmh", "limiter cap km/h", 20, 90, 1),
            ("stop_pull_past_m", "bay pull-past m", 0, 30, 1),
        ]
        for i, (name, label, lo, hi, step) in enumerate(spins):
            r, c = divmod(i, 2)
            tk.Label(f_set, text=label, bg=BG, fg=DIM,
                     font=("Segoe UI", 9)).grid(row=r, column=c * 2,
                                                sticky="w", padx=(8, 4),
                                                pady=2)
            var = tk.DoubleVar(value=getattr(self.ap.settings, name))
            sp = tk.Spinbox(
                f_set, from_=lo, to=hi, increment=step, width=6,
                textvariable=var, bg=PANEL, fg=FG, buttonbackground=PANEL,
                relief="flat", font=("Segoe UI", 9),
                command=lambda n=name, v=var: self._set_setting(n, v))
            sp.bind("<Return>",
                    lambda _e, n=name, v=var: self._set_setting(n, v))
            sp.grid(row=r, column=c * 2 + 1, sticky="w", pady=2)
            self._spin_vars[name] = var

        # manual buttons
        f_man = tk.LabelFrame(body, text=" manual ", bg=BG, fg=DIM, bd=1,
                              relief="groove", font=("Segoe UI", 9))
        f_man.pack(side="right", fill="y", pady=4, padx=(8, 0))
        rows = [
            [("Door 1", lambda: self._tap("DoorFrontOpenClose")),
             ("Door 2", lambda: self._tap("DoorMiddleOpenClose")),
             ("Door 3", lambda: self._tap("DoorRearOpenClose"))],
            [("Clearance", lambda: self._tap("ToggleDoorClearance")),
             ("Kneel v", lambda: self._tap("KneelDown")),
             ("Kneel ^", lambda: self._tap("KneelUp"))],
            [("< Ind", lambda: self._indicate(-1)),
             ("Ind off", lambda: self._indicate(0)),
             ("Ind >", lambda: self._indicate(1))],
            [("Warning", lambda: self._tap("ToggleWarningLights")),
             ("Lights", lambda: self._tap("Lightswitch")),
             ("Wiper", lambda: self._tap("WiperUp"))],
            [("Engine", lambda: self._hold("MotorStartStop")),
             ("Fix brake", lambda: self._tap("FixingBrake")),
             ("Stop brake", lambda: self._tap("StopBrakeOnOff"))],
            [("Gear R", lambda: self._tap("SetGearR")),
             ("Gear N", lambda: self._tap("SetGearN")),
             ("Gear D", lambda: self._tap("SetGearD"))],
        ]
        for r in rows:
            fr = tk.Frame(f_man, bg=BG)
            fr.pack(fill="x", padx=4, pady=2)
            for label, cmd in r:
                tk.Button(fr, text=label, width=9, relief="flat", bg=PANEL,
                          fg=FG, activebackground="#2a2e38",
                          activeforeground=FG, font=("Segoe UI", 9),
                          command=cmd).pack(side="left", padx=2)
        b_horn = tk.Button(f_man, text="HORN (hold)", relief="flat",
                           bg=PANEL, fg=FG, font=("Segoe UI", 9, "bold"))
        b_horn.pack(fill="x", padx=6, pady=(6, 4))
        b_horn.bind("<ButtonPress-1>", lambda e: self._event("Horn", "press"))
        b_horn.bind("<ButtonRelease-1>",
                    lambda e: self._event("Horn", "release"))

        # log + release
        self.t_log = tk.Text(self.root, height=7, bg=PANEL, fg=DIM,
                             relief="flat", font=("Consolas", 9),
                             state="disabled")
        self.t_log.pack(fill="x", padx=8, pady=4)
        tk.Button(self.root, text="RELEASE CONTROL", bg=BAD, fg="white",
                  font=("Segoe UI", 12, "bold"), relief="flat",
                  command=self._release_all).pack(fill="x", padx=8,
                                                  pady=(0, 8))

    # -- actions ----------------------------------------------------------
    def _tap(self, event: str):
        threading.Thread(target=lambda: self._safe(
            lambda: self.bridge.tap(event)), daemon=True).start()

    def _hold(self, event: str):
        threading.Thread(target=lambda: self._safe(
            lambda: self.bridge.hold(event, 0.4)), daemon=True).start()

    def _indicate(self, want: int):
        """Vehicle-adaptive indicator (direct events or stalk notches)."""
        threading.Thread(target=lambda: self._safe(
            lambda: self.bridge.indicate(want)), daemon=True).start()

    def _event(self, event: str, mode: str):
        threading.Thread(target=lambda: self._safe(
            lambda: self.bridge.send_event(event, mode)),
            daemon=True).start()

    def _safe(self, fn):
        try:
            fn()
        except BridgeError as e:
            self._q.put(f"error: {e}")

    def _set_feature(self, name: str, on: bool):
        self.ap.set_feature(name, on)

    def _toggle_feature(self, name: str):
        self.ap.set_feature(name, not getattr(self.ap.features, name))
        if name in self._feat_vars:
            self._feat_vars[name].set(getattr(self.ap.features, name))

    def _set_setting(self, name: str, var):
        try:
            self.ap.update_settings(**{name: float(var.get())})
        except (tk.TclError, KeyError, ValueError):
            pass

    def _toggle_engage(self):
        if self.ap.engaged:
            self.ap.disengage()
        else:
            self.ap.engage()

    def _release_all(self):
        self.ap.disengage("released")
        if self.ap._pad is not None:
            self.ap._pad.neutral()

    # -- global hotkeys (work while the game has focus) ---------------------
    def _hotkey_loop(self):
        """RegisterHotKey needs its own thread-bound message loop; actions
        hop back onto the Tk thread via root.after."""
        user32 = ctypes.windll.user32
        mod = 0x0001 | 0x0002 | 0x4000  # ALT | CONTROL | NOREPEAT
        for hid, key, label in self.HOTKEYS:
            if not user32.RegisterHotKey(None, hid, mod, ord(key)):
                self._q.put(f"hotkey Ctrl+Alt+{key} ({label}) "
                            "taken by another app")
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                try:
                    self.root.after(0, self._hotkey_action, int(msg.wParam))
                except RuntimeError:
                    return  # window gone

    def _hotkey_action(self, hid: int):
        if hid == 1:
            self._toggle_engage()
        elif hid == 2:
            self._release_all()
        elif hid == 3:
            self._toggle_feature("speed_limiter")
        elif hid == 4:
            self._toggle_feature("stop_at_stops")
        elif hid == 5:
            self._tap("DoorFrontOpenClose")
        elif hid == 6:
            self._tap("ToggleWarningLights")

    # -- overlay: compact frameless strip over the game ---------------------
    def _toggle_overlay(self):
        if self.overlay is not None:
            self._overlay_pos = "+%d+%d" % (self.overlay.winfo_x(),
                                            self.overlay.winfo_y())
            self.overlay.destroy()
            self.overlay = None
            return
        ov = self.overlay = tk.Toplevel(self.root)
        ov.overrideredirect(True)          # no title bar
        ov.attributes("-topmost", True)    # above a borderless game window
        ov.attributes("-alpha", 0.93)
        ov.configure(bg=BG)
        ov.geometry(self._overlay_pos)
        row = tk.Frame(ov, bg=BG, bd=1, relief="solid")
        row.pack()
        self.ov_speed = tk.Label(row, text="--", fg=FG, bg=BG, width=4,
                                 font=("Segoe UI", 22, "bold"),
                                 cursor="fleur")
        self.ov_speed.pack(side="left", padx=(8, 2))
        self.ov_state = tk.Label(row, text="", fg=DIM, bg=BG, width=9,
                                 anchor="w", font=("Segoe UI", 9),
                                 cursor="fleur")
        self.ov_state.pack(side="left")
        self.ov_engage = tk.Button(row, text="ENGAGE", bg=GOOD, fg="black",
                                   relief="flat", width=8,
                                   font=("Segoe UI", 11, "bold"),
                                   command=self._toggle_engage)
        self.ov_engage.pack(side="left", padx=3, pady=6)
        tk.Button(row, text="DOOR", bg=PANEL, fg=FG, relief="flat", width=5,
                  font=("Segoe UI", 9, "bold"),
                  command=lambda: self._tap("DoorFrontOpenClose")
                  ).pack(side="left", padx=3)
        self.ov_feat = {}
        for name, text in (("stop_at_stops", "STOPS"),
                           ("speed_limiter", "LIMIT")):
            b = tk.Button(row, text=text, bg=PANEL, fg=DIM, relief="flat",
                          width=6, font=("Segoe UI", 9, "bold"),
                          command=lambda n=name: self._toggle_feature(n))
            b.pack(side="left", padx=3)
            self.ov_feat[name] = b
        tk.Button(row, text="✕", bg=BG, fg=DIM, relief="flat", bd=0,
                  command=self._toggle_overlay).pack(side="left",
                                                     padx=(3, 6))
        # drag anywhere on the speed/state labels to move it
        for w in (self.ov_speed, self.ov_state):
            w.bind("<ButtonPress-1>", self._ov_drag_start)
            w.bind("<B1-Motion>", self._ov_drag)

    def _ov_drag_start(self, e):
        self._ov_grab = (e.x_root - self.overlay.winfo_x(),
                         e.y_root - self.overlay.winfo_y())

    def _ov_drag(self, e):
        gx, gy = self._ov_grab
        self.overlay.geometry(f"+{e.x_root - gx}+{e.y_root - gy}")

    # -- polling ------------------------------------------------------------
    def _poll_loop(self):
        while self._alive:
            try:
                self._telemetry = self.bridge.read()
            except BridgeError:
                self._telemetry = None
            time.sleep(self.POLL_MS / 1000.0)

    def _refresh_ui(self):
        t = self._telemetry
        if t is None or not t.in_vehicle:
            self.l_speed.config(text="--", fg=DIM)
            self.l_info.config(
                text="waiting for the game / enter a bus\n"
                     "(options -> Enable Telemetry Interface)")
        else:
            self.l_speed.config(
                text=f"{t.speed_kmh:.0f}",
                fg=WARN if (t.allowed_speed_kmh and
                            t.speed_kmh > t.allowed_speed_kmh + 3) else FG)
            d = t.next_stop_distance_m
            mode = self.ap.mode
            self.l_info.config(text=(
                f"limit {t.allowed_speed_kmh:.0f}  gear {t.gear_selector:8}"
                f"  doors {'OPEN' if t.doors_open else 'closed'}\n"
                f"next  {t.next_stop.get('StopName', '-')[:30]}"
                f"{'' if d is None else f'  {d:.0f} m'}\n"
                f"mode  {mode}   target {self.ap.target_kmh:.0f} km/h"
                f"{'   STOP REQ' if t.stop_requested else ''}"))
        if self.ap.engaged:
            self.b_engage.config(text="RELEASE", bg=BAD, fg="white")
        else:
            self.b_engage.config(text="ENGAGE", bg=GOOD, fg="black")

        # feature checkboxes follow toggles made elsewhere (hotkey/overlay)
        for name, var in self._feat_vars.items():
            actual = getattr(self.ap.features, name)
            if var.get() != actual:
                var.set(actual)

        if self.overlay is not None:
            self.ov_speed.config(
                text="--" if (t is None or not t.in_vehicle)
                else f"{t.speed_kmh:.0f}")
            if self.ap.engaged:
                self.ov_state.config(
                    text=f"{self.ap.mode} →{self.ap.target_kmh:.0f}")
                self.ov_engage.config(text="RELEASE", bg=BAD, fg="white")
            else:
                self.ov_state.config(text="off")
                self.ov_engage.config(text="ENGAGE", bg=GOOD, fg="black")
            for name, b in self.ov_feat.items():
                on = getattr(self.ap.features, name)
                b.config(bg=GOOD if on else PANEL,
                         fg="black" if on else DIM)

        while not self._q.empty():
            self.ap.log.append(self._q.get())
        log = "\n".join(list(self.ap.log)[-7:])
        self.t_log.config(state="normal")
        self.t_log.delete("1.0", "end")
        self.t_log.insert("1.0", log)
        self.t_log.config(state="disabled")

        if self._alive:
            self.root.after(self.POLL_MS, self._refresh_ui)

    def _on_close(self):
        self._alive = False
        try:
            self.ap.stop()
        finally:
            self.root.destroy()


def main(selftest: bool = False):
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        style.theme_use("clam")
    except tk.TclError:
        pass
    App(root, selftest=selftest)
    root.mainloop()


if __name__ == "__main__":
    main()
