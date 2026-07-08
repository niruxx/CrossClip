"""System tray icon so CrossClip can keep monitoring while hidden."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Callable, Optional

import pystray
from PIL import Image, ImageDraw

from .config import APP_NAME

IS_MACOS = sys.platform == "darwin"


def _build_icon_image(color: tuple[int, int, int] = (103, 80, 164)) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 6, size - 8, size - 4), radius=10, fill=color)
    draw.rounded_rectangle((14, 12, size - 14, size - 8), radius=6, fill=(255, 255, 255, 255))
    draw.rounded_rectangle((size // 2 - 9, 0, size // 2 + 9, 13), radius=5, fill=color)
    return img


def build_icon_file(path: Path, color: tuple[int, int, int] = (103, 80, 164)) -> Path:
    """Write a multi-size .ico to `path` (for the window titlebar), if missing."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        image = _build_icon_image(color)
        image.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    return path


class Tray:
    def __init__(
        self,
        *,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
        is_paused: Callable[[], bool],
        on_toggle_pause: Callable[[], None],
        is_autostart: Callable[[], bool],
        on_toggle_autostart: Callable[[], None],
        autostart_label: str = "Start automatically",
    ):
        self._on_show = on_show
        self._on_quit = on_quit
        self._is_paused = is_paused
        self._on_toggle_pause = on_toggle_pause
        self._is_autostart = is_autostart
        self._on_toggle_autostart = on_toggle_autostart
        self._autostart_label = autostart_label
        self.icon = pystray.Icon(
            APP_NAME,
            icon=_build_icon_image(),
            title=APP_NAME,
            menu=self._build_menu(),
        )

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(f"Show {APP_NAME}", self._show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Pause monitoring",
                self._toggle_pause,
                checked=lambda item: self._is_paused(),
            ),
            pystray.MenuItem(
                self._autostart_label,
                self._toggle_autostart,
                checked=lambda item: self._is_autostart(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit CrossClip", self._quit),
        )

    def _show(self, icon=None, item=None) -> None:
        self._on_show()

    def _toggle_pause(self, icon=None, item=None) -> None:
        self._on_toggle_pause()

    def _toggle_autostart(self, icon=None, item=None) -> None:
        self._on_toggle_autostart()

    def _quit(self, icon=None, item=None) -> None:
        self._on_quit()
        self.icon.stop()

    def start(self) -> Optional[threading.Thread]:
        """Start the tray icon's event loop.

        macOS requires AppKit's run loop to live on the main thread, so on
        that platform this is a no-op - the caller must instead invoke
        `run_blocking()` from the main thread (see main.py).
        """
        if IS_MACOS:
            return None
        thread = threading.Thread(target=self.icon.run, daemon=True, name="CrossClipTray")
        thread.start()
        return thread

    def run_blocking(self) -> None:
        """macOS only: block the calling thread running the tray's native
        event loop. Must be called from the main thread."""
        self.icon.run(setup=lambda icon: None)

    def notify(self, message: str, title: str = APP_NAME) -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
