"""Dark-mode control panel for The Bus AI bridge (tkinter, stdlib only).

Live telemetry (speed, limit, gear, doors, next stop), ENGAGE/RELEASE,
a checkbox per autopilot feature, manual buttons (doors, indicators,
warning lights, engine, brakes, gear, kneeling, wiper, horn-hold), a log,
and a big red RELEASE CONTROL button.

    python -m thebus_ai_bridge gui
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from .autopilot import FEATURE_LABELS, Autopilot
from .bridge import BridgeError, TheBusBridge

BG, PANEL, FG = "#16181d", "#1f2229", "#d7dae0"
DIM, GOOD, WARN, BAD = "#8b90a0", "#4caf50", "#e0b040", "#e05545"


class App:
    POLL_MS = 250

    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("The Bus AI Bridge")
        root.configure(bg=BG)
        root.minsize(560, 640)

        self.bridge = TheBusBridge()
        self.ap = Autopilot.from_config(self.bridge)
        self.ap.start()
        self._telemetry = None
        self._q = queue.Queue()
        self._alive = True

        self._build()
        threading.Thread(target=self._poll_loop, daemon=True,
                         name="gui-poll").start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_ui()

    # -- layout ------------------------------------------------------------
    def _build(self):
        f_head = tk.Frame(self.root, bg=PANEL)
        f_head.pack(fill="x", padx=8, pady=(8, 4))
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

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=8)

        # features
        f_feat = tk.LabelFrame(body, text=" autopilot features ", bg=BG,
                               fg=DIM, bd=1, relief="groove",
                               font=("Segoe UI", 9))
        f_feat.pack(side="left", fill="both", expand=True, pady=4)
        self._feat_vars = {}
        for name in FEATURE_LABELS:
            var = tk.BooleanVar(value=getattr(self.ap.features, name))
            cb = tk.Checkbutton(
                f_feat, text=FEATURE_LABELS[name], variable=var, anchor="w",
                bg=BG, fg=FG, selectcolor=PANEL, activebackground=BG,
                activeforeground=FG, font=("Segoe UI", 9), wraplength=250,
                justify="left",
                command=lambda n=name, v=var: self._set_feature(n, v.get()))
            cb.pack(fill="x", padx=6)
            self._feat_vars[name] = var

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
            [("< Ind", lambda: self._tap("SetIndicatorDown")),
             ("Ind off", lambda: self._tap("SetIndicatorOff")),
             ("Ind >", lambda: self._tap("SetIndicatorUp"))],
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

    def _toggle_engage(self):
        if self.ap.engaged:
            self.ap.disengage()
        else:
            self.ap.engage()

    def _release_all(self):
        self.ap.disengage("released")
        if self.ap._pad is not None:
            self.ap._pad.neutral()

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


def main():
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        style.theme_use("clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
