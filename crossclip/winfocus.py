"""Windows-only helper to force the app window to the real OS foreground.

Windows blocks background processes from stealing focus outright (the
"foreground lock timeout"), which can otherwise leave a freshly-launched
window sitting behind whatever the user was last using even after it calls
SetForegroundWindow. Tapping Alt resets that lock for the calling process -
a standard, widely-used workaround - after which SetForegroundWindow works
as expected.

The actual window is drawn by the Flet desktop runner (a separate flet.exe
process), not our own Python process, so it's found by its title rather
than a handle we already have.
"""
from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"


def force_foreground(title: str) -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        import win32con
        import win32gui
    except ImportError:
        return

    try:
        hwnd = win32gui.FindWindow(None, title)
        if not hwnd:
            return

        user32 = ctypes.windll.user32
        user32.keybd_event(win32con.VK_MENU, 0, 0, 0)
        user32.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
