"""Full self-driving: the game's own navigation data steers the bus.

The telemetry interface exposes everything a lane follower needs:

  * ``/roadmap``  - the whole map's lane geometry (cubic hermite splines,
                    world coordinates in centimeters), fetched once
  * ``/route``    - the lanes of the active navigation route (an
                    UNORDERED overlay set - it exists for map rendering)
  * ``/player``   - live position (Location.X/Y, cm) and heading
                    (Rotation.Yaw, degrees, clockwise positive)

``AiDriver`` builds a directed lane graph from the roadmap (lane end ->
lane start adjacency), locates the lane the bus is on (position + heading
alignment), walks the graph forward preferring lanes that are part of the
route - that is what picks the correct arm of every junction - and
follows the resulting local path with pure-pursuit steering on the
virtual pad. Speed stays with the Autopilot (service stops, doors,
limits); the driver only adds a curve cap via ``external_cap_kmh``.

Steering is released (mode "lost") when no plausible lane is found; the
pad watchdog and the autopilot's driver_override keep the human in
charge the moment they intervene.
"""
from __future__ import annotations

import math
import threading
import time

from .autopilot import Autopilot
from .bridge import BridgeError, TheBusBridge

CM = 100.0  # game world units per meter


class AiDriver:
    """Pure-pursuit lane follower on top of the Autopilot."""

    LOOP_HZ = 20.0
    LOOKAHEAD_GAIN_S = 0.9      # lookahead = speed * this
    LOOKAHEAD_MIN_M = 7.0
    LOOKAHEAD_MAX_M = 28.0
    STEER_GAIN = 9.0            # pad units per 1/m of demanded curvature
    STEER_RATE = 3.0            # max pad units per second (smoothing)
    OFF_PATH_M = 12.0           # farther than this from the local path = relocate
    CURVE_LAT_ACCEL = 1.5       # m/s^2 comfort cornering for the cap
    CURVE_SCAN_M = 45.0         # cap looks this far ahead
    ROUTE_REFRESH_S = 5.0
    PATH_REBUILD_S = 2.0        # rebuild the local path this often
    PATH_LENGTH_M = 350.0       # how far ahead the local path extends
    SUBDIVIDE = 4               # hermite samples per lane segment
    LINK_GAP_M = 10.0           # lane end -> lane start = connected
    GRID_M = 25.0               # spatial hash cell size
    ALIGN_MAX_DEG = 70.0        # lane direction vs bus heading tolerance

    def __init__(self, bridge: TheBusBridge, autopilot: Autopilot):
        self.bridge = bridge
        self.ap = autopilot
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._active = False
        self.mode = "off"        # off | follow | roam | lost
        self._lanes = None       # id -> [(x, y), ...] sampled polyline
        self._succ = {}          # id -> [successor ids] (whole roadmap)
        self._grid = {}          # (cx, cy) -> [(lane_id, pt_idx)]
        self._route_set = set()  # lane ids of the active route
        self._route_at = 0.0
        self._path = []          # local forward polyline [(x, y), ...]
        self._cum = []           # cumulative arc length (cm)
        self._path_on_route = False
        self._path_at = 0.0
        self._near_i = 0
        self._steer = 0.0

    # -- lifecycle ----------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    def start(self):
        """Engage the autopilot and start steering along the route."""
        with self._lock:
            if self._active:
                return
            self._active = True
            self.mode = "lost"
        self.ap.engage()
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, name="thebus-ai-driver", daemon=True)
            self._thread.start()
        self.ap._say("AI driver on")

    def stop(self, disengage: bool = True):
        """Stop steering; optionally hand everything back."""
        with self._lock:
            was = self._active
            self._active = False
            self.mode = "off"
        self.ap.external_cap_kmh = None
        if was:
            pad = self.ap._pad
            if pad is not None:
                try:
                    pad.set_controls(steer=0.0)
                except Exception:
                    pass
            self.ap._say("AI driver off")
        if disengage:
            self.ap.disengage()
        self._stop.set()
        self._thread = None

    def status(self) -> dict:
        return {"active": self._active, "mode": self.mode,
                "steer": round(self._steer, 3),
                "on_route": self._path_on_route,
                "path_m": round((self._cum[-1] / CM) if self._cum else 0)}

    # -- roadmap ---------------------------------------------------------------
    def _load_roadmap(self):
        raw = self.bridge.roadmap()
        lanes, grid = {}, {}
        cell = self.GRID_M * CM
        for lane in raw.get("Lanes", []):
            pts = lane.get("Points") or []
            if len(pts) < 2:
                continue
            lid = int(lane["ID"])
            sampled = self._sample_lane(pts)
            lanes[lid] = sampled
            for idx, (x, y) in enumerate(sampled):
                grid.setdefault((int(x // cell), int(y // cell)),
                                []).append((lid, idx))
        # successor graph: end point -> nearby start points
        starts = {}
        for lid, poly in lanes.items():
            sx, sy = poly[0]
            starts.setdefault((int(sx // cell), int(sy // cell)),
                              []).append((lid, sx, sy))
        tol = self.LINK_GAP_M * CM
        succ = {}
        for lid, poly in lanes.items():
            ex, ey = poly[-1]
            cx, cy = int(ex // cell), int(ey // cell)
            links = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for b, sx, sy in starts.get((cx + dx, cy + dy), []):
                        if b == lid:
                            continue
                        d = math.dist((sx, sy), (ex, ey))
                        if d < tol:
                            links.append((d, b))
            succ[lid] = [b for _d, b in sorted(links)]
        self._lanes, self._grid, self._succ = lanes, grid, succ

    def _sample_lane(self, pts: list) -> list:
        """Cubic-hermite samples along one lane (positions + tangents)."""
        out = []
        for i in range(len(pts) - 1):
            p0, p1 = pts[i]["pos"], pts[i + 1]["pos"]
            m0 = pts[i].get("tanOut") or {}
            m1 = pts[i + 1].get("tanIn") or {}
            x0, y0 = float(p0["X"]), float(p0["Y"])
            x1, y1 = float(p1["X"]), float(p1["Y"])
            mx0, my0 = float(m0.get("X", 0)), float(m0.get("Y", 0))
            mx1, my1 = float(m1.get("X", 0)), float(m1.get("Y", 0))
            n = self.SUBDIVIDE
            for k in range(n):
                t = k / n
                h00 = 2 * t**3 - 3 * t**2 + 1
                h10 = t**3 - 2 * t**2 + t
                h01 = -2 * t**3 + 3 * t**2
                h11 = t**3 - t**2
                out.append((h00 * x0 + h10 * mx0 + h01 * x1 + h11 * mx1,
                            h00 * y0 + h10 * my0 + h01 * y1 + h11 * my1))
        last = pts[-1]["pos"]
        out.append((float(last["X"]), float(last["Y"])))
        return out

    def _refresh_route(self):
        route = self.bridge.route()
        ids = set()
        for p in route.get("Paths", []):
            ids.update(int(i) for i in p.get("PathLanes", []))
        self._route_set = ids & set(self._lanes or {})

    # -- locating the bus & building the local path --------------------------------
    def _locate(self, px: float, py: float, yaw_rad: float):
        """(lane_id, point_index) of the best lane under the bus:
        close by AND pointing the way the bus points; route lanes win."""
        cell = self.GRID_M * CM
        cx, cy = int(px // cell), int(py // cell)
        cand = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cand.extend(self._grid.get((cx + dx, cy + dy), []))
        fx, fy = math.cos(yaw_rad), math.sin(yaw_rad)
        best, best_score = None, None
        for lid, idx in cand:
            poly = self._lanes[lid]
            x, y = poly[idx]
            d_m = math.dist((x, y), (px, py)) / CM
            if d_m > 25.0:
                continue
            j = min(idx, len(poly) - 2)
            sx, sy = poly[j + 1][0] - poly[j][0], poly[j + 1][1] - poly[j][1]
            norm = math.hypot(sx, sy)
            if norm < 1e-6:
                continue
            align = (sx * fx + sy * fy) / norm  # cos(angle to heading)
            if align < math.cos(math.radians(self.ALIGN_MAX_DEG)):
                continue
            score = (2.0 if lid in self._route_set else 0.0) \
                + align - d_m / 10.0
            if best_score is None or score > best_score:
                best, best_score = (lid, idx), score
        return best

    def _branch_score(self, lid: int) -> float:
        """How much walking into this lane keeps us on the route."""
        if lid in self._route_set:
            return 2.0
        if any(s in self._route_set for s in self._succ.get(lid, [])):
            return 1.0
        return 0.0

    def _turn_angle(self, in_dir: tuple, lid: int) -> float:
        poly = self._lanes[lid]
        dx, dy = poly[-1][0] - poly[0][0], poly[-1][1] - poly[0][1]
        n = math.hypot(dx, dy)
        if n < 1e-6:
            return 0.0
        cosv = max(-1.0, min(1.0, (in_dir[0] * dx + in_dir[1] * dy) / n))
        return math.degrees(math.acos(cosv))

    def _build_path(self, px: float, py: float, yaw_rad: float) -> bool:
        loc = self._locate(px, py, yaw_rad)
        if loc is None:
            with self._lock:
                self._path, self._cum = [], []
            return False
        lid, idx = loc
        on_route = lid in self._route_set
        path = list(self._lanes[lid][idx:])
        length = sum(math.dist(path[i - 1], path[i])
                     for i in range(1, len(path)))
        seen = {lid}
        cur = lid
        while length < self.PATH_LENGTH_M * CM:
            succs = [s for s in self._succ.get(cur, []) if s not in seen]
            if not succs:
                break
            a, b = path[-2] if len(path) >= 2 else path[-1], path[-1]
            in_dir = (b[0] - a[0], b[1] - a[1])
            # stay on the route if any arm of the junction is on it;
            # otherwise take the straightest arm
            succs.sort(key=lambda s: (-self._branch_score(s),
                                      self._turn_angle(in_dir, s)))
            nxt = succs[0]
            # never take a near-U-turn unless it is genuinely the route
            if (self._turn_angle(in_dir, nxt) > 120.0
                    and nxt not in self._route_set):
                break
            seg = self._lanes[nxt]
            length += (math.dist(path[-1], seg[0])
                       + sum(math.dist(seg[i - 1], seg[i])
                             for i in range(1, len(seg))))
            path.extend(seg)
            seen.add(nxt)
            cur = nxt
        dedup = [p for i, p in enumerate(path)
                 if i == 0 or p != path[i - 1]]
        cum = [0.0]
        for i in range(1, len(dedup)):
            cum.append(cum[-1] + math.dist(dedup[i - 1], dedup[i]))
        with self._lock:
            self._path, self._cum = dedup, cum
            self._near_i = 0
            self._path_on_route = on_route
        return True

    # -- control loop -------------------------------------------------------------
    def _loop(self):
        period = 1.0 / self.LOOP_HZ
        prev = time.monotonic()
        while not self._stop.wait(period):
            if not self._active:
                continue
            if not self.ap.engaged:     # human/GUI released the autopilot
                self.stop(disengage=False)
                continue
            now = time.monotonic()
            dt, prev = now - prev, now
            try:
                if self._lanes is None:
                    self.ap._say("loading roadmap ...")
                    self._load_roadmap()
                    self.ap._say(f"roadmap: {len(self._lanes)} lanes")
                if now - self._route_at >= self.ROUTE_REFRESH_S:
                    self._route_at = now
                    self._refresh_route()
                t = self.bridge.read()
            except BridgeError:
                continue
            if not t.in_vehicle:
                continue
            self._drive_tick(t, dt, now)

    def _drive_tick(self, t, dt: float, now: float):
        loc = t.player.get("Location") or {}
        px, py = float(loc.get("X", 0)), float(loc.get("Y", 0))
        yaw = math.radians(t.heading_deg)

        # keep the local path fresh and under the bus
        stale = now - self._path_at >= self.PATH_REBUILD_S
        drifted = True
        if len(self._path) >= 2:
            i = self._nearest_index(px, py)
            drifted = math.dist((px, py), self._path[i]) / CM > self.OFF_PATH_M
            near_end = self._cum[-1] - self._cum[i] < 60.0 * CM
            stale = stale or near_end
        if stale or drifted:
            self._path_at = now
            if not self._build_path(px, py, yaw):
                if self.mode != "lost":
                    self.ap._say("no lane under the bus - steering released")
                self.mode = "lost"
                self._apply_steer(0.0, dt)
                self.ap.external_cap_kmh = None
                return

        self.mode = "follow" if self._path_on_route else "roam"
        i = self._nearest_index(px, py)

        speed_ms = max(0.0, t.speed_kmh / 3.6)
        look_m = min(self.LOOKAHEAD_MAX_M,
                     max(self.LOOKAHEAD_MIN_M,
                         self.LOOKAHEAD_GAIN_S * speed_ms))
        gx, gy = self._point_ahead(i, look_m * CM)

        # goal in vehicle frame (UE: yaw clockwise-positive, deg, 0 = +X)
        dx, dy = gx - px, gy - py
        fwd = dx * math.cos(yaw) + dy * math.sin(yaw)
        right = -dx * math.sin(yaw) + dy * math.cos(yaw)
        alpha = math.atan2(right, max(1e-6, fwd))  # + = goal to the right
        # pure pursuit: demanded curvature (1/m), positive = turn right
        kappa = 2.0 * math.sin(alpha) / look_m
        self._apply_steer(max(-1.0, min(1.0, self.STEER_GAIN * kappa)), dt)

        # curve speed cap from path curvature ahead
        k_max = self._max_curvature(i, self.CURVE_SCAN_M * CM)
        cap = None
        if k_max > 1e-4:
            cap = max(12.0, math.sqrt(self.CURVE_LAT_ACCEL / k_max) * 3.6)
        # end of known path (dead end / route terminus): ease right down
        remaining_m = (self._cum[-1] - self._cum[i]) / CM
        if remaining_m < 25.0:
            cap = min(cap or 99.0, max(5.0, remaining_m * 0.8))
        self.ap.external_cap_kmh = cap

    def _apply_steer(self, target: float, dt: float):
        max_step = self.STEER_RATE * max(dt, 1e-3)
        cur = self._steer
        cur += max(-max_step, min(max_step, target - cur))
        self._steer = cur
        pad = self.ap._ensure_pad()
        if pad is not None:
            pad.set_controls(steer=cur)

    # -- polyline helpers ---------------------------------------------------------
    def _nearest_index(self, px: float, py: float) -> int:
        path = self._path
        best_i, best_d = self._near_i, float("inf")
        lo = max(0, self._near_i - 10)
        hi = min(len(path), self._near_i + 200)
        for i in range(lo, hi):
            d = (path[i][0] - px) ** 2 + (path[i][1] - py) ** 2
            if d < best_d:
                best_i, best_d = i, d
        self._near_i = best_i
        return best_i

    def _point_ahead(self, i: int, dist_cm: float) -> tuple:
        path, cum = self._path, self._cum
        target = cum[i] + dist_cm
        j = i
        while j < len(cum) - 1 and cum[j] < target:
            j += 1
        return path[j]

    def _max_curvature(self, i: int, scan_cm: float) -> float:
        """Max |curvature| (1/m) of the path within the scan window."""
        path, cum = self._path, self._cum
        end = cum[i] + scan_cm
        k_max, j = 0.0, i + 1
        while j < len(path) - 1 and cum[j] < end:
            ax, ay = path[j - 1]
            bx, by = path[j]
            cx, cy = path[j + 1]
            v1x, v1y = bx - ax, by - ay
            v2x, v2y = cx - bx, cy - by
            l1, l2 = math.hypot(v1x, v1y), math.hypot(v2x, v2y)
            if l1 > 1.0 and l2 > 1.0:
                cross = v1x * v2y - v1y * v2x
                sin_turn = max(-1.0, min(1.0, cross / (l1 * l2)))
                # turn angle over the mean segment length -> curvature
                k = abs(math.asin(sin_turn)) / (((l1 + l2) / 2) / CM)
                k_max = max(k_max, k)
            j += 1
        return k_max
