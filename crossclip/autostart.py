"""Enable/disable launching CrossClip in the background when the user logs in.

Each OS has its own mechanism: a per-user registry Run key on Windows, a
LaunchAgent plist on macOS, and an XDG autostart .desktop file on Linux (the
convention followed by GNOME, KDE, XFCE, and most other desktop
environments). All three just point at `main.py --minimized`.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .config import APP_NAME

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

LAUNCH_AGENT_LABEL = "com.crossclip.app"
LAUNCH_AGENT_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"

DESKTOP_AUTOSTART_PATH = Path.home() / ".config" / "autostart" / "crossclip.desktop"

MACOS_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>Label</key>
\t<string>{label}</string>
\t<key>ProgramArguments</key>
\t<array>
{program_args}
\t</array>
\t<key>RunAtLoad</key>
\t<true/>
</dict>
</plist>
"""

LINUX_DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name={name}
Comment=Clipboard manager and history tracker
Exec={exec_line}
Terminal=false
X-GNOME-Autostart-enabled=true
"""


def _entry_point() -> tuple[str, list[str]]:
    """Returns (executable, args) that relaunch the app hidden in the tray."""
    if getattr(sys, "frozen", False):
        return sys.executable, ["--minimized"]

    entry = str(Path(__file__).resolve().parent.parent / "main.py")
    if IS_WINDOWS:
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        interpreter = str(pythonw) if pythonw.exists() else sys.executable
    else:
        interpreter = sys.executable
    return interpreter, [entry, "--minimized"]


def _quote_shell_like(parts: list[str]) -> str:
    def quote(part: str) -> str:
        if not part or any(c in part for c in " \t\"'"):
            return '"' + part.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return part

    return " ".join(quote(p) for p in parts)


def is_supported() -> bool:
    return IS_WINDOWS or IS_MACOS or IS_LINUX


def toggle_label() -> str:
    if IS_MACOS:
        return "Open at Login"
    if IS_LINUX:
        return "Start automatically on login"
    return "Start with Windows"


def is_enabled() -> bool:
    if IS_WINDOWS:
        return _windows_is_enabled()
    if IS_MACOS:
        return LAUNCH_AGENT_PATH.exists()
    if IS_LINUX:
        return DESKTOP_AUTOSTART_PATH.exists()
    return False


def set_enabled(enabled: bool) -> bool:
    """Returns True on success."""
    if IS_WINDOWS:
        return _windows_set_enabled(enabled)
    if IS_MACOS:
        return _macos_set_enabled(enabled)
    if IS_LINUX:
        return _linux_set_enabled(enabled)
    return False


# -- Windows: HKCU Run key --------------------------------------------------


def _windows_is_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def _windows_set_enabled(enabled: bool) -> bool:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                interpreter, args = _entry_point()
                command = subprocess.list2cmdline([interpreter, *args])
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# -- macOS: LaunchAgent -------------------------------------------------------


def _macos_set_enabled(enabled: bool) -> bool:
    try:
        if enabled:
            interpreter, args = _entry_point()
            arg_lines = "\n".join(
                f"\t\t<string>{a}</string>" for a in [interpreter, *args]
            )
            LAUNCH_AGENT_PATH.parent.mkdir(parents=True, exist_ok=True)
            LAUNCH_AGENT_PATH.write_text(
                MACOS_PLIST_TEMPLATE.format(
                    label=LAUNCH_AGENT_LABEL, program_args=arg_lines
                ),
                encoding="utf-8",
            )
        else:
            LAUNCH_AGENT_PATH.unlink(missing_ok=True)
        return True
    except OSError:
        return False


# -- Linux: XDG autostart .desktop entry -------------------------------------


def _linux_set_enabled(enabled: bool) -> bool:
    try:
        if enabled:
            interpreter, args = _entry_point()
            exec_line = _quote_shell_like([interpreter, *args])
            DESKTOP_AUTOSTART_PATH.parent.mkdir(parents=True, exist_ok=True)
            DESKTOP_AUTOSTART_PATH.write_text(
                LINUX_DESKTOP_TEMPLATE.format(name=APP_NAME, exec_line=exec_line),
                encoding="utf-8",
            )
        else:
            DESKTOP_AUTOSTART_PATH.unlink(missing_ok=True)
        return True
    except OSError:
        return False
