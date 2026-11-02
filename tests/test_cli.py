import io
import json
from datetime import date

import pytest

from qbo_ar import cli

TODAY = date(2026, 11, 1)

INVOICES = [
    {"Id": "1", "DocNumber": "CO-2026-00446", "Balance": 45000, "TotalAmt": 45000,
     "DueDate": "2026-10-10", "TxnDate": "2026-09-10",
     "CustomerRef": {"value": "A", "name": "Lydiah Chemutai"}},
    {"Id": "2", "DocNumber": "CO-2026-00447", "Balance": 1000, "TotalAmt": 1000,
     "DueDate": "2026-12-01", "TxnDate": "2026-10-20",
     "CustomerRef": {"value": "B", "name": "African Daisy"}},
]


class FakeQBO:
    def __init__(self, report_total="46000.00"):
        self.report_total, self.posts, self.sends = report_total, [], []

    def query(self, sql):
        entity = sql.split("FROM ")[1].split()[0]
        data = {
            "Invoice": INVOICES,
            "CreditMemo": [],
            "Payment": [],
            "Customer": [
                {"Id": "A", "DisplayName": "Lydiah", "PrimaryEmailAddr": {"Address": "l@x.co"}},
                {"Id": "B", "DisplayName": "Daisy"},
            ],
        }[entity]
        return {"QueryResponse": {entity: data}}

    def report(self, name, **params):
        return {"Rows": {"Row": [{"group": "GrandTotal",
                                  "Summary": {"ColData": [{"value": "TOTAL"},
                                                          {"value": self.report_total}]}}]}}

    def post(self, endpoint, json=None, **kw):
        self.posts.append((endpoint, json))
        return {"Payment": {"Id": "900"}}

    def request(self, method, endpoint, **kw):
        self.sends.append(endpoint)
        return {"Invoice": {"Id": endpoint.split("/")[1]}}


@pytest.fixture(autouse=True)
def isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "DEFAULT_LOG_DIR", tmp_path)


def run(argv, client=None, capsys=None):
    code = cli.main(argv, client=client or FakeQBO(), today=TODAY)
    return code, capsys.readouterr()


def test_aging_table(capsys):
    code, out = run(["aging"], capsys=capsys)
    assert code == 0
    assert "Lydiah Chemutai" in out.out and "46,000.00" in out.out and "Net A/R" in out.out


def test_aging_json_with_tie_out_ok(capsys):
    code, out = run(["aging", "--json", "--tie-out"], capsys=capsys)
    data = json.loads(out.out)
    assert code == 0 and data["grand_total"] == "46000.00" and data["tie_out"]["ok"] is True


def test_aging_tie_out_mismatch_exits_2(capsys):
    code, out = run(["aging", "--tie-out"], client=FakeQBO("40000.00"), capsys=capsys)
    assert code == 2 and "MISMATCH" in out.out


def test_open_lists_days_overdue(capsys):
    code, out = run(["open"], capsys=capsys)
    assert code == 0 and "CO-2026-00446" in out.out and "22" in out.out


def test_dunning_is_dry_run_by_default(capsys):
    client = FakeQBO()
    code, out = run(["dunning"], client=client, capsys=capsys)
    assert code == 0 and "would_send" in out.out and "Dry run" in out.out
    assert client.sends == []


def test_dunning_send_and_csv(capsys, tmp_path):
    client, csv_path = FakeQBO(), tmp_path / "r.csv"
    code, out = run(["dunning", "--send", "--csv", str(csv_path)], client=client, capsys=capsys)
    assert code == 0 and client.sends == ["invoice/1/send"]
    assert "Lydiah Chemutai" in csv_path.read_text()


STATEMENT = """Receipt No.,Completion Time,Details,Paid In,Withdrawn,Account No.
UJ1AAA,2026-10-30 09:15:00,Pay Bill,"45,000.00",,co-2026-00446
"""


def statement_file(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text(STATEMENT)
    return str(f)


def test_reconcile_dry_run_then_apply_then_rerun(capsys, tmp_path):
    client, path = FakeQBO(), statement_file(tmp_path)
    code, out = run(["reconcile", path], client=client, capsys=capsys)
    assert code == 0 and "matched" in out.out and "Dry run: 1" in out.out and client.posts == []
    code, out = run(["reconcile", path, "--apply", "--deposit-account", "35"],
                    client=client, capsys=capsys)
    assert "Posted 1" in out.out and client.posts[0][1]["DepositToAccountRef"] == {"value": "35"}
    code, out = run(["reconcile", path, "--apply"], client=client, capsys=capsys)
    assert "duplicate" in out.out and len(client.posts) == 1


def test_reconcile_column_mapping(capsys, tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("TransID,Time,Amt,Ref\nX1,2026-10-30,45000,CO-2026-00446\n")
    code, out = run(["reconcile", str(f), "--map", "txn_id=TransID", "--map", "date=Time",
                     "--map", "amount=Amt", "--map", "reference=Ref"], capsys=capsys)
    assert code == 0 and "matched" in out.out


def test_metrics(capsys):
    code, out = run(["metrics", "--days", "90"], capsys=capsys)
    assert code == 0 and "DSO (90d)" in out.out and "Lydiah Chemutai" in out.out


def test_errors_return_exit_1_without_traceback(capsys, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    code, out = run(["reconcile", str(bad)], capsys=capsys)
    assert code == 1 and "missing columns" in out.err


def test_missing_config_is_a_clean_error(capsys, monkeypatch):
    for k in ("QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REALM_ID"):
        monkeypatch.delenv(k, raising=False)
    code = cli.main(["open"], today=TODAY)
    assert code == 1 and "QBO_CLIENT_ID" in capsys.readouterr().err


def test_table_helper_alignment():
    text = cli._table(["Name", "Amt"], [["a", "1.00"], ["longer", "22.50"]])
    assert text.splitlines()[0].startswith("Name  ")
    assert io.StringIO(text).readlines()[-1].rstrip().endswith("22.50")
