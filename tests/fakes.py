"""Test doubles: an HTTP session that replays queued responses, and a static token source."""

from __future__ import annotations

import json as jsonlib
from typing import Any

import requests


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None, headers: dict | None = None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = b"" if body is None else jsonlib.dumps(body).encode()
        self.text = self.content.decode()

    def json(self) -> Any:
        return self._body


class FakeSession:
    """Replays queued responses (or raises queued exceptions) and records every call."""

    def __init__(self, *queue: Any):
        self.queue = list(queue)
        self.calls: list[dict] = []

    def _next(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self.queue:
            raise AssertionError(f"Unexpected call: {method} {url}")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        return self._next(method, url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        return self._next("POST", url, **kwargs)


class StaticTokens:
    """Token source that never talks to Intuit."""

    def __init__(self, token: str = "tok"):
        self.token = token
        self.refreshes = 0

    def get_access_token(self, force_refresh: bool = False) -> str:
        if force_refresh:
            self.refreshes += 1
            self.token = f"{self.token}+"
        return self.token


class FakeClock:
    def __init__(self, now: float = 1_000.0):
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


NETWORK_ERROR = requests.ConnectionError("boom")
