import pytest
import requests
from fakes import NETWORK_ERROR, FakeClock, FakeResponse, FakeSession, StaticTokens

from qbo_ar.client import QBOClient, QBOError, RateLimiter
from qbo_ar.config import Settings

SETTINGS = Settings("cid", "secret", "123", max_retries=3)


def make(*queue, tokens=None):
    session = FakeSession(*queue)
    clock = FakeClock()
    client = QBOClient(
        SETTINGS, tokens or StaticTokens(), session,
        RateLimiter(clock=clock, sleep=clock.sleep), sleep=clock.sleep, rng=lambda: 1.0,
    )
    return client, session, clock


def test_get_sends_auth_minorversion_and_url():
    client, session, _ = make(FakeResponse(200, {"ok": True}))
    assert client.get("invoice/7") == {"ok": True}
    call = session.calls[0]
    assert call["url"].endswith("/v3/company/123/invoice/7")
    assert call["params"]["minorversion"] == 75
    assert call["headers"]["Authorization"] == "Bearer tok"


def test_retries_429_using_retry_after():
    client, _, clock = make(
        FakeResponse(429, {}, {"Retry-After": "7"}), FakeResponse(200, {"a": 1})
    )
    assert client.get("x") == {"a": 1}
    assert clock.slept == [7.0]


def test_retries_5xx_on_get_with_backoff():
    client, session, clock = make(
        FakeResponse(503, {}), FakeResponse(503, {}), FakeResponse(200, {})
    )
    client.get("x")
    assert len(session.calls) == 3
    assert clock.slept[1] > clock.slept[0]


def test_post_without_request_id_is_not_retried_on_5xx():
    client, session, _ = make(FakeResponse(500, {"Fault": {"Error": [{"Message": "boom"}]}}))
    with pytest.raises(QBOError):
        client.post("payment", {"a": 1})
    assert len(session.calls) == 1


def test_post_with_request_id_is_retried_and_sends_id():
    client, session, _ = make(FakeResponse(502, {}), FakeResponse(200, {"Payment": {"Id": "1"}}))
    out = client.post("payment", {"a": 1}, request_id="abc")
    assert out["Payment"]["Id"] == "1"
    assert all(c["params"]["requestid"] == "abc" for c in session.calls)


def test_429_is_retried_even_for_plain_post():
    client, session, _ = make(FakeResponse(429, {}), FakeResponse(200, {}))
    client.post("invoice", {})
    assert len(session.calls) == 2


def test_401_forces_one_token_refresh_then_succeeds():
    tokens = StaticTokens()
    client, session, _ = make(FakeResponse(401, {}), FakeResponse(200, {"ok": 1}), tokens=tokens)
    assert client.get("x") == {"ok": 1}
    assert tokens.refreshes == 1
    assert session.calls[1]["headers"]["Authorization"] == "Bearer tok+"


def test_second_401_surfaces_as_error():
    client, _, _ = make(FakeResponse(401, {}), FakeResponse(401, {}))
    with pytest.raises(QBOError) as exc:
        client.get("x")
    assert exc.value.status == 401


def test_validation_fault_is_parsed_with_tid():
    body = {"Fault": {"Error": [{"Message": "Stale Object Error", "Detail": "d", "code": "5010"}],
                      "type": "ValidationFault"}}
    client, _, _ = make(FakeResponse(400, body, {"intuit_tid": "tid-1"}))
    with pytest.raises(QBOError) as exc:
        client.post("invoice", {})
    err = exc.value
    assert err.is_stale_object and err.intuit_tid == "tid-1" and err.fault_type == "ValidationFault"


def test_network_error_retried_for_get_only():
    client, _, _ = make(NETWORK_ERROR, FakeResponse(200, {"ok": 1}))
    assert client.get("x") == {"ok": 1}
    client2, _, _ = make(NETWORK_ERROR)
    with pytest.raises(QBOError, match="Network error"):
        client2.post("invoice", {})


def test_gives_up_after_max_retries():
    client, session, _ = make(*[FakeResponse(503, {}) for _ in range(4)])
    with pytest.raises(QBOError):
        client.get("x")
    assert len(session.calls) == 4


def test_rejects_overlong_request_id():
    client, _, _ = make()
    with pytest.raises(ValueError):
        client.post("payment", {}, request_id="x" * 51)


def test_rate_limiter_blocks_until_window_clears():
    clock = FakeClock(0.0)
    limiter = RateLimiter(max_calls=2, period=60, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        limiter.acquire()
    assert clock.slept == [60.0]


def test_requests_exception_type_is_covered():
    assert isinstance(NETWORK_ERROR, requests.RequestException)
