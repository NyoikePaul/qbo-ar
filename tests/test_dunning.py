import io
from datetime import date
from decimal import Decimal

from qbo_ar.dunning import (
    DEFAULT_TIERS,
    build_reminders,
    render_email,
    send_reminders,
    tier_for,
    write_csv,
)
from qbo_ar.models import Customer, Invoice
from qbo_ar.util import JsonlLog

AS_OF = date(2026, 11, 1)


def inv(id_, cust, balance, due, name=None):
    return Invoice.from_api({
        "Id": id_, "DocNumber": f"INV-{id_}", "Balance": balance, "DueDate": due,
        "CustomerRef": {"value": cust, "name": name or f"Cust {cust}"},
    })


INVOICES = [
    inv("1", "A", 100, "2026-10-25"),   # 7 days   -> friendly
    inv("2", "B", 200, "2026-10-10"),   # 22 days  -> firm
    inv("3", "B", 50, "2026-10-30"),    # 2 days (same customer, newer)
    inv("4", "C", 900, "2026-09-15"),   # 47 days  -> final
    inv("5", "D", 500, "2026-08-01"),   # 92 days  -> escalate (internal)
    inv("6", "E", 100, "2026-12-01"),   # not yet due -> no reminder
]


def test_tier_selection_boundaries():
    assert tier_for(0) is None
    assert tier_for(1).name == "friendly" and tier_for(14).name == "friendly"
    assert tier_for(15).name == "firm" and tier_for(30).name == "final"
    assert tier_for(61).name == "escalate"


def test_one_reminder_per_customer_by_oldest_invoice_worst_first():
    reminders = build_reminders(INVOICES, AS_OF)
    assert [r.customer_id for r in reminders] == ["D", "C", "B", "A"]
    b = next(r for r in reminders if r.customer_id == "B")
    assert b.tier.name == "firm" and b.overdue_total == Decimal("250")
    assert [i.id for i in b.invoices] == ["2", "3"]


def test_min_balance_filters_small_debts():
    reminders = build_reminders(INVOICES, AS_OF, min_balance=Decimal("300"))
    assert {r.customer_id for r in reminders} == {"C", "D"}


def test_render_email_mentions_each_invoice_and_total():
    r = next(x for x in build_reminders(INVOICES, AS_OF) if x.customer_id == "B")
    subject, body = render_email(r, "PlanTech")
    assert "[FIRM]" in subject and "250.00" in subject
    assert "INV-2" in body and "INV-3" in body and "PlanTech" in body


def test_csv_export():
    out = io.StringIO()
    write_csv(build_reminders(INVOICES, AS_OF), out)
    rows = out.getvalue().splitlines()
    assert rows[0].startswith("customer_id,customer,tier") and len(rows) == 5
    assert "INV-2;INV-3" in out.getvalue()


class SendRecorder:
    def __init__(self):
        self.calls = []

    def request(self, method, endpoint, **kw):
        self.calls.append((method, endpoint, kw.get("params")))
        return {"Invoice": {"Id": endpoint.split("/")[1]}}


CUSTOMERS = {
    "A": Customer("A", "A", "a@x.co", Decimal(0)),
    "B": Customer("B", "B", None, Decimal(0)),
    "C": Customer("C", "C", "c@x.co", Decimal(0)),
    "D": Customer("D", "D", "d@x.co", Decimal(0)),
}


def test_dry_run_sends_nothing(tmp_path):
    client, log = SendRecorder(), JsonlLog(tmp_path / "d.jsonl")
    out = send_reminders(client, build_reminders(INVOICES, AS_OF), log, CUSTOMERS)
    actions = {o.reminder.customer_id: o.action for o in out}
    assert actions == {"D": "skipped_internal", "C": "would_send", "B": "skipped_no_email",
                       "A": "would_send"}
    assert client.calls == []


def test_live_send_once_per_invoice_and_tier(tmp_path):
    client, log = SendRecorder(), JsonlLog(tmp_path / "d.jsonl")
    reminders = build_reminders(INVOICES, AS_OF)
    first = send_reminders(client, reminders, log, CUSTOMERS, dry_run=False)
    assert sum(o.action == "sent" for o in first) == 2
    assert ("POST", "invoice/4/send", {"sendTo": "c@x.co"}) in client.calls
    second = send_reminders(client, reminders, log, CUSTOMERS, dry_run=False)
    assert sum(o.action == "skipped_already_sent" for o in second) == 2
    assert len(client.calls) == 2


def test_tier_escalation_sends_again(tmp_path):
    client, log = SendRecorder(), JsonlLog(tmp_path / "d.jsonl")
    send_reminders(client, build_reminders([inv("1", "A", 100, "2026-10-25")], AS_OF), log,
                   CUSTOMERS, dry_run=False)
    later = date(2026, 11, 20)  # same invoice is now 26 days overdue -> firm tier
    out = send_reminders(client, build_reminders([inv("1", "A", 100, "2026-10-25")], later), log,
                         CUSTOMERS, dry_run=False)
    assert out[0].action == "sent" and len(client.calls) == 2


def test_default_tiers_are_ordered():
    days = [t.min_days_overdue for t in DEFAULT_TIERS]
    assert days == sorted(days)
