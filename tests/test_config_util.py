from decimal import Decimal

import pytest

from qbo_ar.config import ConfigError, Settings
from qbo_ar.util import JsonlLog, quantize_money, request_id_for, to_decimal

ENV = {"QBO_CLIENT_ID": "id", "QBO_CLIENT_SECRET": "secret", "QBO_REALM_ID": "123"}


def test_settings_from_env_defaults():
    s = Settings.from_env(ENV)
    assert s.environment == "sandbox"
    assert s.company_url == "https://sandbox-quickbooks.api.intuit.com/v3/company/123"
    assert s.minor_version == 75


def test_settings_production_url():
    s = Settings.from_env({**ENV, "QBO_ENVIRONMENT": "production"})
    assert s.company_url.startswith("https://quickbooks.api.intuit.com/")


def test_settings_missing_vars_listed():
    with pytest.raises(ConfigError, match="QBO_CLIENT_SECRET"):
        Settings.from_env({"QBO_CLIENT_ID": "x", "QBO_REALM_ID": "1"})


def test_settings_rejects_unknown_environment():
    with pytest.raises(ConfigError):
        Settings.from_env({**ENV, "QBO_ENVIRONMENT": "staging"})


def test_to_decimal_avoids_float_noise():
    assert to_decimal(0.1) + to_decimal(0.2) == Decimal("0.3")
    assert to_decimal(None) == Decimal("0")


def test_quantize_half_up():
    assert quantize_money(Decimal("2.675")) == Decimal("2.68")


def test_request_id_is_stable_and_short():
    a = request_id_for("payment", "42", "QGH123")
    assert a == request_id_for("payment", "42", "QGH123")
    assert a != request_id_for("payment", "42", "QGH124")
    assert len(a) <= 50


def test_jsonl_log_persists_between_instances(tmp_path):
    path = tmp_path / "log.jsonl"
    log = JsonlLog(path)
    assert not log.seen("a")
    log.add("a", payment_id="9")
    assert log.seen("a")
    assert JsonlLog(path).seen("a")
