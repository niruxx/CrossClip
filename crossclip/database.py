"""SQLite-backed storage for clipboard history."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('text', 'image')),
    content TEXT,
    image_path TEXT,
    thumb_path TEXT,
    width INTEGER,
    height INTEGER,
    content_hash TEXT NOT NULL UNIQUE,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clip_items_updated ON clip_items(pinned DESC, updated_at DESC);
"""


@dataclass
class ClipItem:
    id: int
    type: str
    content: Optional[str]
    image_path: Optional[str]
    thumb_path: Optional[str]
    width: Optional[int]
    height: Optional[int]
    content_hash: str
    pinned: bool
    created_at: float
    updated_at: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ClipItem":
        return cls(
            id=row["id"],
            type=row["type"],
            content=row["content"],
            image_path=row["image_path"],
            thumb_path=row["thumb_path"],
            width=row["width"],
            height=row["height"],
            content_hash=row["content_hash"],
            pinned=bool(row["pinned"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class Database:
    """Thread-safe wrapper around a single SQLite connection."""

    def __init__(self, db_path: Path = config.DB_PATH):
        config.ensure_dirs()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writes -----------------------------------------------------------

    def add_or_bump(
        self,
        item_type: str,
        content_hash: str,
        *,
        content: Optional[str] = None,
        image_path: Optional[str] = None,
        thumb_path: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> tuple[int, bool]:
        """Insert a new item, or bump an existing one (by hash) to the top.

        Returns (item_id, is_new).
        """
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "SELECT id FROM clip_items WHERE content_hash = ?", (content_hash,)
            )
            existing = cur.fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE clip_items SET updated_at = ? WHERE id = ?",
                    (now, existing["id"]),
                )
                self._conn.commit()
                return existing["id"], False

            cur = self._conn.execute(
                """
                INSERT INTO clip_items
                    (type, content, image_path, thumb_path, width, height,
                     content_hash, pinned, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    item_type,
                    content,
                    image_path,
                    thumb_path,
                    width,
                    height,
                    content_hash,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return cur.lastrowid, True

    def toggle_pin(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE clip_items SET pinned = 1 - pinned WHERE id = ?", (item_id,)
            )
            self._conn.commit()

    def delete_item(self, item_id: int) -> Optional[ClipItem]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM clip_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not row:
                return None
            self._conn.execute("DELETE FROM clip_items WHERE id = ?", (item_id,))
            self._conn.commit()
            return ClipItem.from_row(row)

    def clear_history(self, keep_pinned: bool = True) -> list[ClipItem]:
        with self._lock:
            query = "SELECT * FROM clip_items"
            if keep_pinned:
                query += " WHERE pinned = 0"
            rows = self._conn.execute(query).fetchall()
            self._conn.execute(query.replace("SELECT *", "DELETE"))
            self._conn.commit()
            return [ClipItem.from_row(r) for r in rows]

    def purge_excess(self, max_items: int) -> list[ClipItem]:
        """Delete oldest, unpinned rows beyond max_items. Returns deleted rows."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM clip_items WHERE pinned = 0
                ORDER BY updated_at DESC
                LIMIT -1 OFFSET ?
                """,
                (max_items,),
            ).fetchall()
            if rows:
                ids = [r["id"] for r in rows]
                placeholders = ",".join("?" * len(ids))
                self._conn.execute(
                    f"DELETE FROM clip_items WHERE id IN ({placeholders})", ids
                )
                self._conn.commit()
            return [ClipItem.from_row(r) for r in rows]

    # -- reads --------------------------------------------------------------

    def list_items(self, search: str = "") -> list[ClipItem]:
        with self._lock:
            if search:
                rows = self._conn.execute(
                    """
                    SELECT * FROM clip_items
                    WHERE type = 'image' OR content LIKE ?
                    ORDER BY pinned DESC, updated_at DESC
                    """,
                    (f"%{search}%",),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM clip_items ORDER BY pinned DESC, updated_at DESC"
                ).fetchall()
            return [ClipItem.from_row(r) for r in rows]

    def get_item(self, item_id: int) -> Optional[ClipItem]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM clip_items WHERE id = ?", (item_id,)
            ).fetchone()
            return ClipItem.from_row(row) if row else None

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM clip_items").fetchone()[0]


def new_image_filename(suffix: str = ".png") -> str:
    return f"{uuid.uuid4().hex}{suffix}"
