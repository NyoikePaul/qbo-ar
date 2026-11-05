"""Persistent SQLite request queue for QuickBooks Web Connector."""

from __future__ import annotations

import sqlite3
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

JobState = Literal["pending", "in_flight", "completed", "failed"]


class QueueError(RuntimeError):
    """Queue operation failed."""


class IdempotencyConflict(QueueError):
    """Idempotency key was reused with a different request."""


@dataclass(frozen=True)
class QBWCJob:
    job_id: str
    company_file: str
    request_xml: str
    state: JobState
    attempts: int
    max_attempts: int
    created_at: float
    updated_at: float
    response_xml: str | None
    error_code: str | None
    error_message: str | None


class QBWCRequestQueue:
    """Durable, idempotent queue for QBWC requests."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)

        if self.database != ":memory:":
            path = Path(self.database).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            self.database = str(path)

        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 10000")

        if self.database != ":memory:":
            db.execute("PRAGMA journal_mode = WAL")

        return db

    def _initialise(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS qbwc_jobs (
                    job_id TEXT PRIMARY KEY,
                    idempotency_key TEXT UNIQUE,
                    company_file TEXT NOT NULL,
                    request_xml TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (
                        state IN ('pending','in_flight','completed','failed')
                    ),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    response_xml TEXT,
                    error_code TEXT,
                    error_message TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_qbwc_pending
                ON qbwc_jobs(state, created_at)
            """)

    @staticmethod
    def _validate_xml(xml: str) -> None:
        if not xml or not xml.strip():
            raise QueueError("XML payload cannot be empty")

        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise QueueError(f"Invalid XML: {exc}") from exc

        if root.tag != "QBXML":
            raise QueueError("Expected QBXML root element")

    @staticmethod
    def _from_row(row: sqlite3.Row | None) -> QBWCJob | None:
        if row is None:
            return None

        return QBWCJob(
            job_id=row["job_id"],
            company_file=row["company_file"],
            request_xml=row["request_xml"],
            state=row["state"],
            attempts=row["attempts"],
            max_attempts=row["max_attempts"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            response_xml=row["response_xml"],
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    def enqueue(
        self,
        request_xml: str,
        *,
        company_file: str = "",
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> QBWCJob:
        self._validate_xml(request_xml)

        if max_attempts < 1:
            raise QueueError("max_attempts must be at least 1")

        if idempotency_key is not None:
            idempotency_key = idempotency_key.strip()
            if not idempotency_key:
                raise QueueError("idempotency_key cannot be blank")

        now = time.time()
        job_id = str(uuid.uuid4())

        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")

            if idempotency_key:
                existing = db.execute(
                    "SELECT * FROM qbwc_jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()

                if existing:
                    if (
                        existing["request_xml"] != request_xml
                        or existing["company_file"] != company_file
                    ):
                        raise IdempotencyConflict(
                            "Idempotency key belongs to another request"
                        )

                    result = self._from_row(existing)
                    assert result is not None
                    return result

            db.execute("""
                INSERT INTO qbwc_jobs (
                    job_id, idempotency_key, company_file, request_xml,
                    state, attempts, max_attempts, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?)
            """, (
                job_id, idempotency_key, company_file,
                request_xml, max_attempts, now, now,
            ))

            row = db.execute(
                "SELECT * FROM qbwc_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

        result = self._from_row(row)
        assert result is not None
        return result

    def get(self, job_id: str) -> QBWCJob | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM qbwc_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

        return self._from_row(row)

    def claim_next(
        self, *, company_file: str | None = None
    ) -> QBWCJob | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")

            if company_file is None:
                row = db.execute("""
                    SELECT * FROM qbwc_jobs
                    WHERE state = 'pending'
                    ORDER BY created_at, rowid
                    LIMIT 1
                """).fetchone()
            else:
                row = db.execute("""
                    SELECT * FROM qbwc_jobs
                    WHERE state = 'pending' AND company_file = ?
                    ORDER BY created_at, rowid
                    LIMIT 1
                """, (company_file,)).fetchone()

            if row is None:
                return None

            db.execute("""
                UPDATE qbwc_jobs
                SET state = 'in_flight',
                    attempts = attempts + 1,
                    updated_at = ?
                WHERE job_id = ? AND state = 'pending'
            """, (time.time(), row["job_id"]))

            updated = db.execute(
                "SELECT * FROM qbwc_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()

        return self._from_row(updated)

    def complete(self, job_id: str, response_xml: str) -> QBWCJob:
        self._validate_xml(response_xml)

        with self._connect() as db:
            cursor = db.execute("""
                UPDATE qbwc_jobs
                SET state = 'completed',
                    response_xml = ?,
                    error_code = NULL,
                    error_message = NULL,
                    updated_at = ?
                WHERE job_id = ? AND state = 'in_flight'
            """, (response_xml, time.time(), job_id))

            if cursor.rowcount != 1:
                raise QueueError("Job does not exist or is not in flight")

            row = db.execute(
                "SELECT * FROM qbwc_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

        result = self._from_row(row)
        assert result is not None
        return result

    def fail(
        self,
        job_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool = True,
        response_xml: str | None = None,
    ) -> QBWCJob:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")

            row = db.execute(
                "SELECT * FROM qbwc_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

            if row is None or row["state"] != "in_flight":
                raise QueueError("Job does not exist or is not in flight")

            retry = retryable and row["attempts"] < row["max_attempts"]
            state = "pending" if retry else "failed"

            db.execute("""
                UPDATE qbwc_jobs
                SET state = ?, error_code = ?,
                    error_message = ?, response_xml = COALESCE(?, response_xml),
                    updated_at = ?
                WHERE job_id = ?
            """, (
                state, error_code, error_message, response_xml,
                time.time(), job_id,
            ))

            updated = db.execute(
                "SELECT * FROM qbwc_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()

        result = self._from_row(updated)
        assert result is not None
        return result


    def pending_count(self, *, company_file: str | None = None) -> int:
        """Count pending jobs, optionally scoped to a company file."""
        with self._connect() as db:
            if company_file is None:
                row = db.execute(
                    "SELECT COUNT(*) AS total FROM qbwc_jobs WHERE state = 'pending'"
                ).fetchone()
            else:
                row = db.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM qbwc_jobs
                    WHERE state = 'pending' AND company_file = ?
                    """,
                    (company_file,),
                ).fetchone()

        return int(row["total"])

    def counts(self) -> dict[str, int]:
        result = {
            "pending": 0,
            "in_flight": 0,
            "completed": 0,
            "failed": 0,
        }

        with self._connect() as db:
            rows = db.execute("""
                SELECT state, COUNT(*) AS total
                FROM qbwc_jobs GROUP BY state
            """).fetchall()

        for row in rows:
            result[row["state"]] = row["total"]

        return result
