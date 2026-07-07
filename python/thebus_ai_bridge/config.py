"""Persistent configuration: every autopilot feature and setting, one file.

Location:  %APPDATA%\\thebus-ai-bridge\\config.json   (override with the
THEBUS_AI_BRIDGE_CONFIG environment variable). Human-editable JSON:

    {
      "features": { "speed_control": true, "auto_doors": true, ... },
      "settings": { "max_speed_kmh": 55.0, "stop_min_dwell_s": 4.0, ... }
    }

Missing keys fall back to the defaults in autopilot.py; unknown keys are
ignored, so the file survives upgrades in both directions. The GUI, the
autopilot CLI and the MCP server all load it on start and save whenever
a toggle or setting changes.

Show it:  python -m thebus_ai_bridge config
"""
from __future__ import annotations

import json
import os
from dataclasses import fields
from pathlib import Path

from .autopilot import Features, Settings


def config_path() -> Path:
    env = os.environ.get("THEBUS_AI_BRIDGE_CONFIG")
    if env:
        return Path(env)
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "thebus-ai-bridge" / "config.json"


def _apply(obj, data: dict):
    for f in fields(obj):
        if f.name in data:
            # coerce to the default's type so a "true"/1/5 in hand-edited
            # JSON still lands as proper bool/float
            try:
                setattr(obj, f.name, type(getattr(obj, f.name))(data[f.name]))
            except (TypeError, ValueError):
                pass  # bad value: keep the default


def load() -> tuple[Features, Settings]:
    """Config from disk; silent defaults if the file is missing/broken."""
    feats, sets = Features(), Settings()
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return feats, sets
    _apply(feats, data.get("features", {}))
    _apply(sets, data.get("settings", {}))
    return feats, sets


def save(features: Features, settings: Settings):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "features": features.as_dict(),
        "settings": {f.name: getattr(settings, f.name)
                     for f in fields(settings)},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
