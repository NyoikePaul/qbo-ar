"""Append-only audit trail: one JSON object per line, UTC timestamps, a run id.

Records what the tool decided and did. Secrets are redacted and free-text payer details are
never logged by callers, because statements can contain personal data.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = ("token", "secret", "password", "authorization", "api_key", "apikey")
REDACTED = "***"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: REDACTED if any(s in str(k).lower() for s in SENSITIVE_KEYS) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class AuditLog:
    def __init__(
        self,
        path: str | Path,
        run_id: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.run_id = run_id or uuid.uuid4().hex
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def record(self, event: str, **fields: Any) -> dict[str, Any]:
        entry = {
            **redact(fields),
            "ts": self._clock().isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "event": event,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(entry, default=str, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry
