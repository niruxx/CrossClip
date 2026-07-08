"""CrossClip entry point.

Run with:
    python main.py               # show the window
    python main.py --minimized   # start hidden in the system tray (used by autostart)
"""
from __future__ import annotations

import argparse
import sys
import threading

import flet as ft

from crossclip import config, database, tray, utils
from crossclip.monitor import ClipboardMonitor
from crossclip.tray import Tray
from crossclip.ui.app import CrossClipApp

IS_MACOS = sys.platform == "darwin"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CrossClip clipboard manager")
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start hidden in the system tray instead of showing the window",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config.ensure_dirs()

    settings = config.Settings.load()
    db = database.Database()
    signal = utils.ChangeSignal()
    monitor = ClipboardMonitor(db, settings, on_change=signal.notify)
    monitor.start()

    icon_path = tray.build_icon_file(config.DATA_DIR / "icon.ico")
    start_minimized = args.minimized or settings.start_minimized

    tray_ready = threading.Event()
    tray_holder: dict[str, Tray] = {}

    def on_tray_ready(tray_obj: Tray) -> None:
        tray_holder["tray"] = tray_obj
        tray_ready.set()

    def run_page(page: ft.Page) -> None:
        app = CrossClipApp(
            page,
            db,
            settings,
            monitor,
            signal,
            start_minimized=start_minimized,
            icon_path=icon_path,
            on_tray_ready=on_tray_ready,
        )
        app.build()

    def run_flet() -> None:
        try:
            ft.run(run_page, assets_dir=str(config.DATA_DIR))
        finally:
            monitor.stop()
            db.close()
            tray_obj = tray_holder.get("tray")
            if tray_obj is not None:
                tray_obj.stop()

    if IS_MACOS:
        # AppKit's run loop (which pystray's tray icon needs) must live on
        # the main thread, and it's a blocking call - so Flet, which also
        # wants to run its own loop, gets pushed to a background thread
        # instead. The tray is only constructed once Flet's page loads
        # (inside CrossClipApp.build), so we wait for that handoff before
        # blocking the main thread on it.
        flet_thread = threading.Thread(target=run_flet, daemon=True, name="CrossClipFlet")
        flet_thread.start()
        if tray_ready.wait(timeout=15):
            tray_holder["tray"].run_blocking()
        flet_thread.join()
    else:
        run_flet()


if __name__ == "__main__":
    main()
