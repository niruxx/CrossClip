"""Small shared helpers: hashing, formatting, and cross-thread change signals."""
from __future__ import annotations

import hashlib
import threading
import time


def hash_text(text: str) -> str:
    return "t:" + hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def hash_bytes(data: bytes) -> str:
    return "i:" + hashlib.sha256(data).hexdigest()


def human_time(ts: float) -> str:
    delta = time.time() - ts
    if delta < 5:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    days = int(delta // 86400)
    if days < 7:
        return f"{days}d ago"
    return time.strftime("%b %d, %Y", time.localtime(ts))


def truncate(text: str, limit: int = 400) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def preview_line(text: str, limit: int = 140) -> str:
    collapsed = " ".join(text.split())
    return truncate(collapsed, limit)


class ChangeSignal:
    """A thread-safe "something changed" flag.

    The clipboard monitor runs on a background thread and calls notify();
    the UI's async poll loop calls consume() to check-and-clear it.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def notify(self) -> None:
        self._event.set()

    def consume(self) -> bool:
        if self._event.is_set():
            self._event.clear()
            return True
        return False
