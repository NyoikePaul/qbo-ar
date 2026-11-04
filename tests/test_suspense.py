import csv
from datetime import date
from decimal import Decimal

from qbo_ar.models import Invoice
from qbo_ar.reconcile import ApplySummary, MatchResult, StatementLine
from qbo_ar.suspense import PayerMap, learn_from_posted, split_payer, suggest, write_queue


def inv(id_, doc, balance, cust, name, due="2026-10-01"):
    return Invoice.from_api({
        "Id": id_, "DocNumber": doc, "Balance": balance, "DueDate": due,
        "CustomerRef": {"value": cust, "name": name},
    })


def line(txn, payer, amount="100", ref="x"):
    return StatementLine(txn, date(2026, 11, 4), Decimal(amount), ref, payer)


def unmatched(ln):
    return MatchResult(ln, "unmatched", None, "no matching reference")


INVS = [inv("1", "1001", 50, "5", "John Melton"), inv("2", "1002", 70, "6", "Red Rock Diner")]


def test_split_payer():
    assert split_payer("Pay Bill from 2547***411") == ("2547***411", "")
    expected = ("254722123456", "JOHN MELTON")
    assert split_payer("Pay Bill from 254722123456 - JOHN MELTON") == expected
    assert split_payer("Byron Otieno") == ("", "BYRON OTIENO")


def test_learned_payer_map_persists_and_suggests(tmp_path):
    PayerMap(tmp_path / "p.json").learn("tel:2547***411", "5", "John Melton")
    res = unmatched(line("U1", "Pay Bill from 2547***411"))
    s = suggest(res, INVS, PayerMap(tmp_path / "p.json"))
    assert (s.customer, s.invoice, s.score) == ("John Melton", "1001", 80)
    assert "1 prior" in s.why


def test_fuzzy_payer_name(tmp_path):
    res = unmatched(line("U2", "Pay Bill from JON MELTON"))
    s = suggest(res, INVS, PayerMap(tmp_path / "p.json"))
    assert s.customer == "John Melton" and 0 < s.score <= 75


def test_no_suggestion_for_unknown_payer(tmp_path):
    res = unmatched(line("U3", "Pay Bill from 2547***999"))
    assert suggest(res, INVS, PayerMap(tmp_path / "p.json")) is None


def test_write_queue_only_unmatched_and_probable(tmp_path):
    ok = MatchResult(line("A", "x"), "matched", INVS[0], "", 100)
    probable = MatchResult(line("B", "x"), "probable", INVS[0], "unique open invoice", 70)
    um = unmatched(line("C", "Pay Bill from JON MELTON"))
    n = write_queue(tmp_path / "q.csv", [ok, probable, um], INVS, PayerMap(tmp_path / "p.json"))
    rows = list(csv.DictReader((tmp_path / "q.csv").open()))
    assert n == 2 and [r["Txn"] for r in rows] == ["B", "C"]


def test_queue_neutralises_formula_text(tmp_path):
    res = unmatched(line("D", "=HYPERLINK(1)"))
    write_queue(tmp_path / "q.csv", [res], INVS, PayerMap(tmp_path / "p.json"))
    assert list(csv.DictReader((tmp_path / "q.csv").open()))[0]["Payer"].startswith("'=")


def test_learn_from_posted(tmp_path):
    res = MatchResult(line("A", "Pay Bill from 2547***411"), "matched", INVS[0], "", 100)
    pm = PayerMap(tmp_path / "p.json")
    learn_from_posted(pm, ApplySummary(posted=[(res, None)]))
    assert pm.get("tel:2547***411")["customer_id"] == "5"
