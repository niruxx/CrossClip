"""Paths and persistent user settings for CrossClip."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "CrossClip"


def _data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(__import__("os").environ.get("LOCALAPPDATA", Path.home()))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(__import__("os").environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


DATA_DIR = _data_dir()
IMAGES_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "history.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
LOG_PATH = DATA_DIR / "crossclip.log"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)


DEFAULT_SEED_COLOR = "#6750A4"  # Material You baseline purple

SEED_COLOR_CHOICES = {
    "Purple": "#6750A4",
    "Indigo": "#4F5B93",
    "Teal": "#00696D",
    "Green": "#3C6939",
    "Amber": "#7C5800",
    "Rose": "#984061",
    "Blue": "#2E5FA3",
}


@dataclass
class Settings:
    theme_mode: str = "system"  # system | light | dark
    seed_color: str = DEFAULT_SEED_COLOR
    max_history_items: int = 300
    launch_on_boot: bool = False
    start_minimized: bool = False
    monitor_paused: bool = False
    capture_images: bool = True
    poll_interval_ms: int = 400

    def save(self) -> None:
        ensure_dirs()
        SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "Settings":
        ensure_dirs()
        if not SETTINGS_PATH.exists():
            settings = cls()
            settings.save()
            return settings
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)
