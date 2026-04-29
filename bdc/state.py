"""
SQLite manifest for tracking BDC release discovery and download state.

Tables:
    as_of_dates  — one row per (data_type, as_of_date) pair discovered via listAsOfDates.
    files        — one row per file entry retrieved from listAvailabilityData / listChallengeData.

File statuses (files table):
    pending      — queued but not yet attempted
    downloading  — in progress (cleared to 'pending' on restart if incomplete)
    downloaded   — file saved and MD5 verified
    ingested     — loaded into DuckDB
    error        — failed after max retries
"""

import sqlite3
import os
import logging
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS as_of_dates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    data_type   TEXT    NOT NULL,
    as_of_date  TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(data_type, as_of_date)
);

CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id       TEXT    NOT NULL UNIQUE,
    data_type     TEXT    NOT NULL,
    as_of_date    TEXT    NOT NULL,
    file_name     TEXT,
    file_size     INTEGER,
    md5           TEXT,
    category      TEXT,
    subcategory   TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending',
    error_count   INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    local_path    TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_files_status      ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_as_of_date  ON files(as_of_date, data_type);
"""


class Manifest:
    """Thread-safe SQLite manifest. One instance per process is expected."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._init_db()
        self._reset_stale_downloads()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _reset_stale_downloads(self) -> None:
        """Reset any files left in 'downloading' state from a previous crashed run."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE files SET status='pending', updated_at=datetime('now') "
                "WHERE status='downloading'"
            )
            if cur.rowcount:
                logger.warning(
                    "Reset %d stale 'downloading' file(s) to 'pending'.", cur.rowcount
                )

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # as_of_dates
    # ------------------------------------------------------------------

    def get_known_dates(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT data_type, as_of_date, status FROM as_of_dates ORDER BY as_of_date DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_date(self, data_type: str, as_of_date: str) -> bool:
        """Insert a date if not already known. Returns True if it was new."""
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO as_of_dates(data_type, as_of_date) VALUES(?,?)",
                (data_type, as_of_date),
            )
            return cur.rowcount == 1

    def mark_date_complete(self, data_type: str, as_of_date: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE as_of_dates SET status='complete', updated_at=datetime('now') "
                "WHERE data_type=? AND as_of_date=?",
                (data_type, as_of_date),
            )

    # ------------------------------------------------------------------
    # files
    # ------------------------------------------------------------------

    def upsert_file(self, file_meta: dict) -> bool:
        """Insert a file record if not already present. Returns True if new."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO files
                    (file_id, data_type, as_of_date, file_name, file_size, md5,
                     category, subcategory)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_meta["file_id"],
                    file_meta["data_type"],
                    file_meta["as_of_date"],
                    file_meta.get("file_name"),
                    file_meta.get("file_size"),
                    file_meta.get("md5"),
                    file_meta.get("category"),
                    file_meta.get("subcategory"),
                ),
            )
            return cur.rowcount == 1

    def get_pending_files(self, as_of_date: Optional[str] = None, data_type: Optional[str] = None) -> list[dict]:
        query = "SELECT * FROM files WHERE status IN ('pending', 'error') AND error_count < 5"
        params: list = []
        if as_of_date:
            query += " AND as_of_date=?"
            params.append(as_of_date)
        if data_type:
            query += " AND data_type=?"
            params.append(data_type)
        query += " ORDER BY id"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_downloaded_files(self, as_of_date: Optional[str] = None) -> list[dict]:
        query = "SELECT * FROM files WHERE status='downloaded'"
        params: list = []
        if as_of_date:
            query += " AND as_of_date=?"
            params.append(as_of_date)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def mark_downloading(self, file_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE files SET status='downloading', updated_at=datetime('now') WHERE file_id=?",
                (file_id,),
            )

    def mark_downloaded(self, file_id: str, local_path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE files SET status='downloaded', local_path=?, updated_at=datetime('now') "
                "WHERE file_id=?",
                (local_path, file_id),
            )

    def mark_ingested(self, file_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE files SET status='ingested', updated_at=datetime('now') WHERE file_id=?",
                (file_id,),
            )

    def mark_error(self, file_id: str, message: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE files
                SET status='error',
                    error_count = error_count + 1,
                    error_message = ?,
                    updated_at = datetime('now')
                WHERE file_id=?
                """,
                (message, file_id),
            )

    # ------------------------------------------------------------------
    # Status summary
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        with self._conn() as conn:
            dates = conn.execute(
                "SELECT data_type, as_of_date, status FROM as_of_dates ORDER BY as_of_date DESC"
            ).fetchall()
            counts = conn.execute(
                "SELECT status, COUNT(*) as n FROM files GROUP BY status"
            ).fetchall()
        return {
            "dates": [dict(r) for r in dates],
            "file_counts": {r["status"]: r["n"] for r in counts},
        }
