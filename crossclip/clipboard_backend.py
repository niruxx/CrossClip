"""Low-level clipboard access, isolated behind a small backend interface.

Windows gets a native win32 implementation (cheap change detection via the
clipboard sequence number, Unicode text, and DIB image read/write). macOS and
Linux share a pyperclip + Pillow base for text and image reads (less
efficient - no cheap change token, so the monitor just polls content every
tick - but portable), with a platform-specific image *write* on top since
neither pyperclip nor Pillow can put an image back on the clipboard:
AppleScript/osascript on macOS (ships with the OS), and wl-copy/xclip on
Linux (common but not guaranteed to be installed).
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Optional

from PIL import Image, ImageGrab

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

if IS_WINDOWS:
    import win32clipboard
    import win32con


class ClipboardUnavailable(Exception):
    """Raised when the OS clipboard could not be locked for access."""


class ClipboardBackend:
    def get_change_token(self) -> Optional[int]:
        """Cheap value that changes whenever the clipboard content changes.

        Returns None if the platform has no such mechanism, in which case
        the caller must fall back to polling content directly.
        """
        return None

    def read_text(self) -> Optional[str]:
        raise NotImplementedError

    def read_image(self) -> Optional[Image.Image]:
        raise NotImplementedError

    def write_text(self, text: str) -> None:
        raise NotImplementedError

    def write_image(self, image: Image.Image) -> None:
        raise NotImplementedError


class WindowsClipboardBackend(ClipboardBackend):
    """Windows clipboard access.

    OpenClipboard()/CloseClipboard() form a systemwide lock keyed to the
    calling thread. The monitor thread polls the clipboard continuously
    while the UI thread may write to it at any time (copy-back), so every
    method here is serialized behind one lock to avoid one call's
    Open/Close pair interleaving with another's mid-flight.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def get_change_token(self) -> Optional[int]:
        try:
            return win32clipboard.GetClipboardSequenceNumber()
        except Exception:
            return None

    def _open(self, retries: int = 6, delay: float = 0.03) -> None:
        last_exc: Optional[Exception] = None
        for _ in range(retries):
            try:
                win32clipboard.OpenClipboard()
                return
            except Exception as exc:  # clipboard is often briefly locked by other apps
                last_exc = exc
                time.sleep(delay)
        raise ClipboardUnavailable(str(last_exc))

    def read_text(self) -> Optional[str]:
        with self._lock:
            try:
                self._open()
            except ClipboardUnavailable:
                return None
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    return data or None
                return None
            except Exception:
                return None
            finally:
                win32clipboard.CloseClipboard()

    def read_image(self) -> Optional[Image.Image]:
        # Pillow already knows how to pull CF_DIB/CF_BITMAP off the Windows
        # clipboard correctly (palettes, bit depths, etc.) - no need to
        # hand-roll BITMAPFILEHEADER math here. It does its own internal
        # Open/CloseClipboard, so it still needs to go through our lock.
        with self._lock:
            try:
                grabbed = ImageGrab.grabclipboard()
            except Exception:
                return None
            return grabbed if isinstance(grabbed, Image.Image) else None

    def write_text(self, text: str) -> None:
        with self._lock:
            try:
                self._open()
            except ClipboardUnavailable:
                return
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()

    def write_image(self, image: Image.Image) -> None:
        with self._lock:
            self._write_image_locked(image)

    def _write_image_locked(self, image: Image.Image) -> None:
        try:
            self._open()
        except ClipboardUnavailable:
            return
        try:
            output = io.BytesIO()
            image.convert("RGB").save(output, "BMP")
            dib = output.getvalue()[14:]  # strip the 14-byte BITMAPFILEHEADER
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_DIB, dib)
        finally:
            win32clipboard.CloseClipboard()


class GenericClipboardBackend(ClipboardBackend):
    """macOS/Linux fallback built on pyperclip + Pillow."""

    def __init__(self) -> None:
        import pyperclip

        self._pyperclip = pyperclip

    def read_text(self) -> Optional[str]:
        try:
            text = self._pyperclip.paste()
        except Exception:
            return None
        return text or None

    def read_image(self) -> Optional[Image.Image]:
        try:
            grabbed = ImageGrab.grabclipboard()
        except Exception:
            return None
        return grabbed if isinstance(grabbed, Image.Image) else None

    def write_text(self, text: str) -> None:
        try:
            self._pyperclip.copy(text)
        except Exception:
            pass

    def write_image(self, image: Image.Image) -> None:
        raise NotImplementedError(
            "Copying images back to the clipboard isn't supported on this platform."
        )


class MacClipboardBackend(GenericClipboardBackend):
    """Adds image write support via AppleScript, which ships with macOS."""

    def write_image(self, image: Image.Image) -> None:
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        try:
            with os.fdopen(fd, "wb") as fh:
                image.save(fh, format="PNG")
            script = f'set the clipboard to (read (POSIX file "{tmp_path}") as «class PNGf»)'
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class LinuxClipboardBackend(GenericClipboardBackend):
    """Adds image write support via wl-copy (Wayland) or xclip (X11)."""

    def write_image(self, image: Image.Image) -> None:
        command = _linux_clipboard_write_command()
        if command is None:
            raise NotImplementedError(
                "Copying images to the clipboard needs 'wl-clipboard' (Wayland) "
                "or 'xclip' (X11) installed."
            )
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        subprocess.run(command, input=buf.getvalue(), check=True)


def _linux_clipboard_write_command() -> Optional[list[str]]:
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        return ["wl-copy", "--type", "image/png"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard", "-t", "image/png"]
    if shutil.which("wl-copy"):
        return ["wl-copy", "--type", "image/png"]
    return None


def create_backend() -> ClipboardBackend:
    if IS_WINDOWS:
        return WindowsClipboardBackend()
    if IS_MACOS:
        return MacClipboardBackend()
    if IS_LINUX:
        return LinuxClipboardBackend()
    return GenericClipboardBackend()
