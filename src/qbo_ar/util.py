"""Small shared helpers: money maths, idempotency keys and an append-only log."""

from __future__ import annotations

import hashlib
import json
import os
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

CENT = Decimal("0.01")


def to_decimal(value: object) -> Decimal:
    """Convert JSON numbers/strings to Decimal without float artefacts."""
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def request_id_for(*parts: object) -> str:
    """Deterministic idempotency key (QuickBooks `requestid` allows up to 50 chars)."""
    digest = hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()
    return digest[:32]


class JsonlLog:
    """Append-only JSONL log used to make operations idempotent across runs."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._keys: set[str] | None = None

    def _load(self) -> set[str]:
        if self._keys is None:
            self._keys = set()
            if self.path.exists():
                for line in self.path.read_text().splitlines():
                    if line.strip():
                        self._keys.add(json.loads(line)["key"])
        return self._keys

    def seen(self, key: str) -> bool:
        return key in self._load()

    def add(self, key: str, **extra: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            fh.write(json.dumps({"key": key, **extra}, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        self._load().add(key)
