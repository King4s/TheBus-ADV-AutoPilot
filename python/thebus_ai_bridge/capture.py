"""Screenshot helper for vision-based agents.

Grabs The Bus window client area as PNG bytes. Requires the optional
``mss`` dependency (``pip install thebus-ai-bridge[vision]``).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

_TITLES = ("The Bus",)

_user32 = ctypes.windll.user32
_EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def find_game_window() -> int | None:
    """HWND of the running game's top-level window, or None."""
    found: list[int] = []

    def cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        if any(t in buf.value for t in _TITLES):
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(_EnumWindowsProc(cb), 0)
    return found[0] if found else None


def game_client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, width, height) of the window's client area in screen px."""
    rect = wt.RECT()
    _user32.GetClientRect(hwnd, ctypes.byref(rect))
    pt = wt.POINT(0, 0)
    _user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top


def screenshot_png(path: str | None = None) -> bytes:
    """PNG of the game window (full primary monitor if window not found).

    Returns the PNG bytes; also writes them to ``path`` when given."""
    try:
        import mss
        import mss.tools
    except ImportError as e:
        raise ImportError(
            "screenshot needs the optional 'mss' package: "
            "pip install thebus-ai-bridge[vision]") from e

    hwnd = find_game_window()
    with mss.mss() as sct:
        if hwnd:
            left, top, width, height = game_client_rect(hwnd)
            if width <= 0 or height <= 0:  # minimized
                region = sct.monitors[1]
            else:
                region = {"left": left, "top": top,
                          "width": width, "height": height}
        else:
            region = sct.monitors[1]
        shot = sct.grab(region)
        png = mss.tools.to_png(shot.rgb, shot.size)
    if path:
        with open(path, "wb") as f:
            f.write(png)
    return png
