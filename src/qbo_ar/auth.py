"""OAuth 2.0 token handling for QuickBooks Online.

Intuit access tokens last about an hour and *refresh tokens rotate*: every refresh
may return a new refresh token and the old one stops working. The new token pair is
therefore persisted (atomically, mode 0600) before it is handed back to callers.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from .config import TOKEN_URL, Settings


class AuthError(RuntimeError):
    """Raised when tokens are missing or Intuit rejects a refresh."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    access_expires_at: float = 0.0
    refresh_expires_at: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tokens:
        return cls(**{k: data.get(k) for k in cls.__dataclass_fields__ if k in data})


class TokenStore:
    """JSON token file, written atomically with owner-only permissions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> Tokens | None:
        if not self.path.exists():
            return None
        return Tokens.from_dict(json.loads(self.path.read_text()))

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tokens-")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(asdict(tokens), fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class TokenManager:
    def __init__(
        self,
        settings: Settings,
        store: TokenStore | None = None,
        session: Any | None = None,
        clock: Callable[[], float] = time.time,
        skew: float = 60.0,
    ) -> None:
        self.settings = settings
        self.store = store or TokenStore(settings.token_path)
        self.session = session or requests.Session()
        self.clock = clock
        self.skew = skew
        self._lock = threading.Lock()

    def seed(self, refresh_token: str) -> None:
        """Store a refresh token obtained from the Intuit OAuth playground or consent flow."""
        self.store.save(Tokens(access_token="", refresh_token=refresh_token))

    def get_access_token(self, force_refresh: bool = False) -> str:
        with self._lock:
            tokens = self.store.load()
            if tokens is None:
                raise AuthError("No tokens stored. Run `qbo-ar auth seed --refresh-token ...`.")
            expired = self.clock() >= tokens.access_expires_at - self.skew
            if force_refresh or not tokens.access_token or expired:
                tokens = self._refresh(tokens)
            return tokens.access_token

    def _refresh(self, current: Tokens) -> Tokens:
        resp = self.session.post(
            TOKEN_URL,
            auth=(self.settings.client_id, self.settings.client_secret),
            headers={"Accept": "application/json"},
            data={"grant_type": "refresh_token", "refresh_token": current.refresh_token},
            timeout=self.settings.timeout,
        )
        if resp.status_code != 200:
            raise AuthError(f"Token refresh failed ({resp.status_code}): {resp.text[:200]}")
        body = resp.json()
        now = self.clock()
        refresh_ttl = body.get("x_refresh_token_expires_in")
        new = Tokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", current.refresh_token),
            access_expires_at=now + int(body.get("expires_in", 3600)),
            refresh_expires_at=now + int(refresh_ttl) if refresh_ttl else None,
        )
        self.store.save(new)  # persist before use: the old refresh token may now be dead
        return new
