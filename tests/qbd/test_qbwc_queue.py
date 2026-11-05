from datetime import date
from decimal import Decimal

import pytest

from qbd_ar.qbxml import PaymentApplication, build_receive_payment_add
from qbd_ar.web_connector import (
    IdempotencyConflict,
    QBWCRequestQueue,
    QueueError,
)


def request_xml() -> str:
    from qbd_ar.qbxml import build_customer_query

    return build_customer_query("customer-001", list_id="CUST-001")


def response_xml() -> str:
    return """<?xml version="1.0"?>
    <QBXML>
      <QBXMLMsgsRs>
        <CustomerQueryRs requestID="customer-001"
          statusCode="0" statusSeverity="Info"
          statusMessage="Status OK">
          <CustomerRet><ListID>CUST-001</ListID></CustomerRet>
        </CustomerQueryRs>
      </QBXMLMsgsRs>
    </QBXML>"""


def test_enqueue_persists_pending_job(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    job = queue.enqueue(request_xml(), company_file="company.qbw")

    assert job.state == "pending"
    assert job.company_file == "company.qbw"
    assert job.attempts == 0
    assert queue.get(job.job_id) == job


def test_idempotent_enqueue_returns_existing_job(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    xml = request_xml()

    first = queue.enqueue(xml, idempotency_key="customer-sync-001")
    second = queue.enqueue(xml, idempotency_key="customer-sync-001")

    assert first.job_id == second.job_id
    assert queue.counts()["pending"] == 1


def test_idempotency_key_cannot_be_reused_for_different_payload(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    queue.enqueue(request_xml(), idempotency_key="sync-001")

    with pytest.raises(IdempotencyConflict):
        queue.enqueue(
            request_xml(),
            company_file="different-company.qbw",
            idempotency_key="sync-001",
        )


def test_claim_next_moves_job_to_flight(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    created = queue.enqueue(request_xml())

    claimed = queue.claim_next()

    assert claimed is not None
    assert claimed.job_id == created.job_id
    assert claimed.state == "in_flight"
    assert claimed.attempts == 1


def test_claim_next_does_not_claim_same_job_twice(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    queue.enqueue(request_xml())

    first = queue.claim_next()
    second = queue.claim_next()

    assert first is not None
    assert second is None


def test_claim_next_respects_company_file(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    queue.enqueue(request_xml(), company_file="company-a.qbw")
    job_b = queue.enqueue(request_xml(), company_file="company-b.qbw")

    claimed = queue.claim_next(company_file="company-b.qbw")

    assert claimed is not None
    assert claimed.job_id == job_b.job_id


def test_complete_stores_response(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    job = queue.enqueue(request_xml())
    queue.claim_next()

    completed = queue.complete(job.job_id, response_xml())

    assert completed.state == "completed"
    assert completed.response_xml == response_xml()
    assert queue.counts()["completed"] == 1


def test_complete_rejects_job_not_in_flight(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    job = queue.enqueue(request_xml())

    with pytest.raises(QueueError):
        queue.complete(job.job_id, response_xml())


def test_retryable_failure_returns_job_to_pending(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    job = queue.enqueue(request_xml(), max_attempts=2)
    queue.claim_next()

    failed = queue.fail(
        job.job_id,
        error_code="CONNECTION_TIMEOUT",
        error_message="QuickBooks did not respond",
        retryable=True,
    )

    assert failed.state == "pending"
    assert failed.attempts == 1
    assert failed.error_code == "CONNECTION_TIMEOUT"


def test_failure_exhaustion_moves_job_to_failed(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    job = queue.enqueue(request_xml(), max_attempts=2)

    queue.claim_next()
    queue.fail(
        job.job_id,
        error_code="TIMEOUT",
        error_message="First attempt",
    )

    queue.claim_next()
    failed = queue.fail(
        job.job_id,
        error_code="TIMEOUT",
        error_message="Second attempt",
    )

    assert failed.state == "failed"
    assert failed.attempts == 2
    assert queue.claim_next() is None


def test_non_retryable_failure_is_final(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    job = queue.enqueue(request_xml())
    queue.claim_next()

    failed = queue.fail(
        job.job_id,
        error_code="INVALID_REQUEST",
        error_message="Invalid qbXML request",
        retryable=False,
    )

    assert failed.state == "failed"


def test_queue_rejects_invalid_xml(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    with pytest.raises(QueueError):
        queue.enqueue("<invalid>")


def test_queue_rejects_non_qbxml_root(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    with pytest.raises(QueueError):
        queue.enqueue("<Envelope/>")


def test_queue_rejects_invalid_attempt_limit(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    with pytest.raises(QueueError):
        queue.enqueue(request_xml(), max_attempts=0)


def test_queue_rejects_blank_idempotency_key(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    with pytest.raises(QueueError):
        queue.enqueue(request_xml(), idempotency_key="  ")


def test_queue_counts_all_states(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    # Enqueue in the order the jobs should be claimed.
    in_flight = queue.enqueue(request_xml())
    completed = queue.enqueue(request_xml())
    pending = queue.enqueue(request_xml())

    first = queue.claim_next()
    second = queue.claim_next()

    assert first is not None
    assert second is not None
    assert first.job_id == in_flight.job_id
    assert second.job_id == completed.job_id

    queue.complete(in_flight.job_id, response_xml())

    queue.fail(
        completed.job_id,
        error_code="TEST",
        error_message="Expected failure",
        retryable=False,
    )

    counts = queue.counts()

    assert counts == {
        "pending": 1,
        "in_flight": 0,
        "completed": 1,
        "failed": 1,
    }
    assert queue.get(pending.job_id).state == "pending"

def test_payment_request_can_be_queued(tmp_path):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")

    xml = build_receive_payment_add(
        "mpesa-payment-001",
        customer_list_id="CUST-001",
        total_amount=Decimal("15000.00"),
        txn_date=date(2026, 11, 3),
        ref_number="QK7ABC123",
        applications=[
            PaymentApplication("INV-001", Decimal("15000.00"))
        ],
    )

    job = queue.enqueue(
        xml,
        company_file="PlanTech.qbw",
        idempotency_key="mpesa:QK7ABC123",
    )

    assert job.state == "pending"
    assert "ReceivePaymentAddRq" in job.request_xml
