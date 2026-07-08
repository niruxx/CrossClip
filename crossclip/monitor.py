"""Background thread that watches the OS clipboard and records history."""
from __future__ import annotations

import io
import threading
from typing import Callable, Optional

from PIL import Image

from . import config, database, utils
from .clipboard_backend import create_backend
from .config import Settings
from .database import ClipItem, Database

MAX_TEXT_CHARS = 500_000
THUMB_MAX_DIM = 320


class ClipboardMonitor(threading.Thread):
    def __init__(self, db: Database, settings: Settings, on_change: Callable[[], None]):
        super().__init__(daemon=True, name="CrossClipMonitor")
        self.db = db
        self.settings = settings
        self.on_change = on_change
        self.backend = create_backend()
        self._stop_event = threading.Event()
        self._last_token: object = object()
        self._last_text_hash: Optional[str] = None
        self._last_image_hash: Optional[str] = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            interval = max(150, self.settings.poll_interval_ms) / 1000
            try:
                if not self.settings.monitor_paused:
                    self._poll_once()
            except Exception:
                pass
            self._stop_event.wait(interval)

    def _poll_once(self) -> None:
        token = self.backend.get_change_token()
        if token is not None and token == self._last_token:
            return
        self._last_token = token

        if self.settings.capture_images:
            image = self.backend.read_image()
            if image is not None:
                self._handle_image(image)
                return

        text = self.backend.read_text()
        if text:
            self._handle_text(text)

    def _handle_text(self, text: str) -> None:
        if not text.strip():
            return
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS]
        content_hash = utils.hash_text(text)
        if content_hash == self._last_text_hash:
            return
        self._last_text_hash = content_hash
        _, is_new = self.db.add_or_bump("text", content_hash, content=text)
        self._after_write(is_new)

    def _handle_image(self, image: Image.Image) -> None:
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        raw = buf.getvalue()
        content_hash = utils.hash_bytes(raw)
        if content_hash == self._last_image_hash:
            return
        self._last_image_hash = content_hash

        filename = database.new_image_filename(".png")
        thumb_filename = database.new_image_filename(".thumb.png")
        _, is_new = self.db.add_or_bump(
            "image",
            content_hash,
            image_path=filename,
            thumb_path=thumb_filename,
            width=image.width,
            height=image.height,
        )
        if is_new:
            config.ensure_dirs()
            (config.IMAGES_DIR / filename).write_bytes(raw)
            thumb = image.convert("RGB") if image.mode not in ("RGB", "RGBA") else image.copy()
            thumb.thumbnail((THUMB_MAX_DIM, THUMB_MAX_DIM))
            thumb.save(config.IMAGES_DIR / thumb_filename, format="PNG")
        self._after_write(is_new)

    def _after_write(self, is_new: bool) -> None:
        if is_new:
            removed = self.db.purge_excess(self.settings.max_history_items)
            for item in removed:
                delete_item_files(item)
        self.on_change()


def delete_item_files(item: ClipItem) -> None:
    """Remove the image/thumbnail files backing a clip item, if any."""
    for rel in (item.image_path, item.thumb_path):
        if rel:
            try:
                (config.IMAGES_DIR / rel).unlink(missing_ok=True)
            except OSError:
                pass
