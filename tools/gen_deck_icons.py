"""Generate all PNG assets for the Stream Deck .sdPlugin (run at build time).

Flat dark keys with bold text. Writes into streamdeck_plugin/images/.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "streamdeck_plugin" / "images"

DARK, DIM, WHITE = "#101218", "#9aa0aa", "#e8e8e8"
GREEN, BLUE, YELLOW, RED = "#1f6f33", "#1f4d8a", "#8a6d1f", "#8a1f1f"


def font(size: int):
    for name in ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(f"C:\\Windows\\Fonts\\{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()


def key(name: str, size: int, text: str, bg: str, fg: str = WHITE,
        ring: str | None = None):
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    if ring:
        m = size // 12
        d.rounded_rectangle([m, m, size - m, size - m], radius=size // 8,
                            outline=ring, width=max(2, size // 36))
    lines = text.split("\n")
    f = font(int(size * (0.30 if len(max(lines, key=len)) <= 3 else 0.18)))
    d.multiline_text((size / 2, size / 2), text, font=f, fill=fg,
                     anchor="mm", align="center", spacing=size // 24)
    img.save(OUT / f"{name}.png")


def asset(name: str, text: str, bg: str, fg: str = WHITE,
          ring: str | None = None):
    key(name, 72, text, bg, fg, ring)
    key(f"{name}@2x", 144, text, bg, fg, ring)


def icon(name: str, size: int, text: str, fg: str = WHITE):
    """Action-list icon: transparent background, glyph only."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(int(size * 0.55 if len(text) <= 2 else size * 0.38))
    d.text((size / 2, size / 2), text, font=f, fill=fg, anchor="mm")
    img.save(OUT / f"{name}.png")


def action_icon(name: str, text: str):
    icon(name, 28, text)
    icon(f"{name}@2x", 56, text)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # key states
    asset("ap_off", "ENGAGE", DARK, WHITE, GREEN)
    asset("ap_on", "RELEASE", RED, WHITE)
    asset("feat_off", "", DARK, DIM)
    asset("feat_on", "", GREEN, WHITE)
    asset("button", "", DARK, DIM)
    asset("button_on", "", GREEN, WHITE)
    asset("speed", "", DARK, WHITE, BLUE)
    # action-list + plugin icons
    action_icon("autopilot_action", "AP")
    action_icon("feature_action", "⚙")
    action_icon("button_action", "⏻")
    action_icon("speed_action", "km")
    action_icon("category", "🚌")
    icon("plugin", 72, "🚌")
    icon("plugin@2x", 144, "🚌")
    print(f"wrote icons -> {OUT}")


if __name__ == "__main__":
    main()
