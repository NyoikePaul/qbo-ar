import pytest

from qbd_ar.web_connector import server


class FakeServer:
    seen: dict = {}

    def __init__(self, host, port, app):
        FakeServer.seen = {"host": host, "port": port, "app": app}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def serve_forever(self):
        raise KeyboardInterrupt


def _clear(monkeypatch):
    for name in ("QBWC_USERNAME", "QBWC_PASSWORD", "QBWC_HOST", "QBWC_PORT",
                 "QBWC_COMPANY_FILE", "QBWC_QUEUE_DB", "QBWC_SESSION_DB"):
        monkeypatch.delenv(name, raising=False)


def test_main_refuses_to_start_without_credentials(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("QBWC_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert "QBWC_USERNAME" in str(exc.value)


def test_main_serves_on_loopback_by_default(monkeypatch, tmp_path, capsys):
    _clear(monkeypatch)
    monkeypatch.setenv("QBWC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QBWC_USERNAME", "test-user")
    monkeypatch.setenv("QBWC_PASSWORD", "test-password")
    monkeypatch.setattr(server, "make_server", FakeServer)
    server.main()
    assert FakeServer.seen["host"] == "127.0.0.1"
    assert FakeServer.seen["port"] == 8000
    assert callable(FakeServer.seen["app"])
    out = capsys.readouterr().out
    assert "Development server only" in out and "stopped" in out
