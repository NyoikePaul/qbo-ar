"""HTTP client for the QuickBooks Online Accounting API.

Handles: bearer auth with one forced refresh on 401, client-side rate limiting
(QBO allows 500 requests/min per company), retries with exponential backoff and jitter,
`Retry-After`, idempotency via `requestid`, and structured errors that keep the
`intuit_tid` needed for Intuit support tickets.
"""

from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import requests

from .config import Settings

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_REQUEST_ID_LEN = 50


class QBOError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        *,
        code: str | None = None,
        detail: str | None = None,
        fault_type: str | None = None,
        intuit_tid: str | None = None,
    ) -> None:
        super().__init__(f"[{status}] {message}" + (f" (code {code})" if code else ""))
        self.status = status
        self.message = message
        self.code = code
        self.detail = detail
        self.fault_type = fault_type
        self.intuit_tid = intuit_tid

    @property
    def is_stale_object(self) -> bool:
        """Error 5010: the SyncToken is out of date; re-read the entity and retry."""
        return self.code == "5010"


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` per `period` seconds."""

    def __init__(
        self,
        max_calls: int = 450,
        period: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.max_calls, self.period, self.clock, self.sleep = max_calls, period, clock, sleep
        self._hits: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            now = self.clock()
            while self._hits and now - self._hits[0] >= self.period:
                self._hits.popleft()
            if len(self._hits) < self.max_calls:
                self._hits.append(now)
                return
            self.sleep(self.period - (now - self._hits[0]))


def parse_error(resp: Any) -> QBOError:
    """Build a QBOError from either the `Fault` or lowercase `fault` response shapes."""
    tid = resp.headers.get("intuit_tid")
    try:
        body = resp.json() or {}
    except ValueError:
        body = {}
    fault = body.get("Fault") or body.get("fault") or {}
    errors = fault.get("Error") or fault.get("error") or []
    first = errors[0] if errors else {}
    message = first.get("Message") or first.get("message") or resp.text[:200] or "Request failed"
    return QBOError(
        resp.status_code,
        message,
        code=first.get("code") or first.get("errorCode"),
        detail=first.get("Detail") or first.get("detail"),
        fault_type=fault.get("type"),
        intuit_tid=tid,
    )


class QBOClient:
    def __init__(
        self,
        settings: Settings,
        tokens: Any,
        session: Any | None = None,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.settings = settings
        self.tokens = tokens
        self.session = session or requests.Session()
        self.limiter = limiter or RateLimiter()
        self.sleep = sleep
        self.rng = rng

    # -- low level ---------------------------------------------------------------------

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after and retry_after.replace(".", "", 1).isdigit():
            return float(retry_after)
        return min(30.0, 0.5 * 2**attempt) * (0.5 + self.rng() / 2)

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        data: Any = None,
        headers: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if request_id and len(request_id) > MAX_REQUEST_ID_LEN:
            raise ValueError(f"request_id longer than {MAX_REQUEST_ID_LEN} characters")
        query = {"minorversion": self.settings.minor_version, **(params or {})}
        if request_id:
            query["requestid"] = request_id
        url = f"{self.settings.company_url}/{endpoint.lstrip('/')}"
        # A POST without a requestid may already have been applied when a call times out or
        # returns 5xx, so only idempotent calls are retried on those. 429 is always safe.
        replay_safe = method.upper() in {"GET", "HEAD"} or request_id is not None
        refreshed = False
        max_retries = self.settings.max_retries

        for attempt in range(max_retries + 1):
            self.limiter.acquire()
            hdrs = {
                "Authorization": f"Bearer {self.tokens.get_access_token()}",
                "Accept": "application/json",
                **(headers or {}),
            }
            try:
                resp = self.session.request(
                    method, url, params=query, json=json, data=data,
                    headers=hdrs, timeout=self.settings.timeout,
                )
            except requests.RequestException as exc:
                if replay_safe and attempt < max_retries:
                    self.sleep(self._backoff(attempt, None))
                    continue
                raise QBOError(0, f"Network error: {exc}") from exc

            if resp.status_code == 401 and not refreshed:
                refreshed = True
                self.tokens.get_access_token(force_refresh=True)
                continue
            retryable = resp.status_code == 429 or (
                resp.status_code in RETRY_STATUSES and replay_safe
            )
            if retryable and attempt < max_retries:
                self.sleep(self._backoff(attempt, resp.headers.get("Retry-After")))
                continue
            if resp.status_code >= 400:
                raise parse_error(resp)
            return resp.json() if resp.content else {}
        raise QBOError(0, "Retries exhausted")  # pragma: no cover

    # -- conveniences ------------------------------------------------------------------

    def get(self, endpoint: str, **kwargs: Any) -> dict[str, Any]:
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, json: Any = None, **kwargs: Any) -> dict[str, Any]:
        return self.request("POST", endpoint, json=json, **kwargs)

    def query(self, sql: str) -> dict[str, Any]:
        return self.get("query", params={"query": sql})

    def report(self, name: str, **params: Any) -> dict[str, Any]:
        return self.get(f"reports/{name}", params=params)
