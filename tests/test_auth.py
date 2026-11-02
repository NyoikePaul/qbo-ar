import stat

import pytest
from fakes import FakeClock, FakeResponse, FakeSession

from qbo_ar.auth import AuthError, TokenManager, Tokens, TokenStore
from qbo_ar.config import Settings

SETTINGS = Settings("cid", "secret", "123")


def manager(tmp_path, session, clock=None):
    clock = clock or FakeClock()
    return TokenManager(SETTINGS, TokenStore(tmp_path / "t.json"), session, clock), clock


def ok(access="A2", refresh="R2"):
    return FakeResponse(
        200,
        {"access_token": access, "refresh_token": refresh, "expires_in": 3600,
         "x_refresh_token_expires_in": 8_640_000},
    )


def test_missing_tokens_raises(tmp_path):
    mgr, _ = manager(tmp_path, FakeSession())
    with pytest.raises(AuthError, match="auth seed"):
        mgr.get_access_token()


def test_seed_then_refresh_persists_rotated_token(tmp_path):
    session = FakeSession(ok())
    mgr, _ = manager(tmp_path, session)
    mgr.seed("R1")
    assert mgr.get_access_token() == "A2"
    call = session.calls[0]
    assert call["data"] == {"grant_type": "refresh_token", "refresh_token": "R1"}
    assert call["auth"] == ("cid", "secret")
    assert mgr.store.load().refresh_token == "R2"


def test_valid_token_is_reused_without_network(tmp_path):
    session = FakeSession(ok())
    mgr, _ = manager(tmp_path, session)
    mgr.seed("R1")
    mgr.get_access_token()
    assert mgr.get_access_token() == "A2"
    assert len(session.calls) == 1


def test_token_refreshed_inside_skew_window(tmp_path):
    session = FakeSession(ok("A2", "R2"), ok("A3", "R3"))
    mgr, clock = manager(tmp_path, session)
    mgr.seed("R1")
    mgr.get_access_token()
    clock.now += 3600 - 30  # within the 60s skew of expiry
    assert mgr.get_access_token() == "A3"


def test_force_refresh(tmp_path):
    session = FakeSession(ok("A2", "R2"), ok("A3", "R3"))
    mgr, _ = manager(tmp_path, session)
    mgr.seed("R1")
    mgr.get_access_token()
    assert mgr.get_access_token(force_refresh=True) == "A3"


def test_refresh_failure_does_not_overwrite_stored_token(tmp_path):
    session = FakeResponse(400, {"error": "invalid_grant"})
    mgr, _ = manager(tmp_path, FakeSession(session))
    mgr.seed("R1")
    with pytest.raises(AuthError, match="400"):
        mgr.get_access_token()
    assert mgr.store.load().refresh_token == "R1"


def test_token_file_is_owner_only(tmp_path):
    store = TokenStore(tmp_path / "sub" / "t.json")
    store.save(Tokens("a", "r"))
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
