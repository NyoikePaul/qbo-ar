from types import SimpleNamespace

from qbo_ar import dunning
from qbo_ar.util import JsonlLog


class Order:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append(event)


def reminder(inv_id="9", tier="firm", internal=False):
    return SimpleNamespace(
        customer_id="5",
        tier=SimpleNamespace(name=tier, internal=internal),
        invoices=[SimpleNamespace(id=inv_id)],
    )


def test_attempt_is_audited_before_the_send(monkeypatch, tmp_path):
    order = Order()
    monkeypatch.setattr(dunning, "send_invoice", lambda *a, **k: order.events.append("send"))
    dunning.send_reminders(
        None, [reminder()], JsonlLog(tmp_path / "d.jsonl"), None, dry_run=False, audit=order
    )
    assert order.events == ["dunning_attempt", "send", "dunning_decision"]


def test_dry_run_audits_a_decision_but_never_an_attempt(monkeypatch, tmp_path):
    order = Order()
    monkeypatch.setattr(dunning, "send_invoice", lambda *a, **k: order.events.append("send"))
    dunning.send_reminders(
        None, [reminder()], JsonlLog(tmp_path / "d.jsonl"), None, dry_run=True, audit=order
    )
    assert order.events == ["dunning_decision"]
