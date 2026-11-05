"""Persistent QBWC session state."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import time
from typing import Any


class SessionError(RuntimeError):
    """Invalid, expired or closed QBWC session."""


class SessionStore:
    """SQLite-backed Web Connector session storage."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)

        if self.database != ":memory:":
            path = Path(self.database).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.database = str(path)

        self._initialise()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")

        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _initialise(self) -> None:
        with self._db() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS qbwc_sessions (
                    ticket TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    company_file TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL CHECK (
                        state IN ('active', 'closed')
                    ),
                    current_job_id TEXT,
                    completed_jobs INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_qbwc_sessions_state
                ON qbwc_sessions(state, updated_at)
            """)

    def create(self, username: str, company_file: str = "") -> str:
        ticket = str(uuid.uuid4())
        now = time()

        with self._db() as db:
            db.execute("""
                INSERT INTO qbwc_sessions (
                    ticket, username, company_file, state,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'active', ?, ?)
            """, (ticket, username, company_file, now, now))

        return ticket

    def get(self, ticket: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM qbwc_sessions WHERE ticket = ?",
                (ticket,),
            ).fetchone()

        return dict(row) if row else None

    def get_active(self, ticket: str) -> dict[str, Any]:
        session = self.get(ticket)

        if session is None or session["state"] != "active":
            raise SessionError("Unknown or inactive Web Connector session")

        return session

    def set_company_file(self, ticket: str, company_file: str) -> None:
        with self._db() as db:
            cursor = db.execute("""
                UPDATE qbwc_sessions
                SET company_file = ?, updated_at = ?
                WHERE ticket = ? AND state = 'active'
            """, (company_file, time(), ticket))

            if cursor.rowcount != 1:
                raise SessionError("Session is no longer active")

    def set_current_job(self, ticket: str, job_id: str) -> None:
        with self._db() as db:
            cursor = db.execute("""
                UPDATE qbwc_sessions
                SET current_job_id = ?, updated_at = ?
                WHERE ticket = ? AND state = 'active'
            """, (job_id, time(), ticket))

            if cursor.rowcount != 1:
                raise SessionError("Session is no longer active")

    def clear_current_job(self, ticket: str, *, completed: bool = False) -> None:
        increment = 1 if completed else 0

        with self._db() as db:
            db.execute("""
                UPDATE qbwc_sessions
                SET current_job_id = NULL,
                    completed_jobs = completed_jobs + ?,
                    updated_at = ?
                WHERE ticket = ? AND state = 'active'
            """, (increment, time(), ticket))

    def set_error(self, ticket: str, message: str) -> None:
        with self._db() as db:
            db.execute("""
                UPDATE qbwc_sessions
                SET last_error = ?, updated_at = ?
                WHERE ticket = ?
            """, (message[:2000], time(), ticket))

    def close(self, ticket: str) -> None:
        with self._db() as db:
            db.execute("""
                UPDATE qbwc_sessions
                SET state = 'closed',
                    current_job_id = NULL,
                    updated_at = ?
                WHERE ticket = ?
            """, (time(), ticket))
