import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

import pytest

from qbd_ar.qbxml import (
    QBXML_VERSION,
    PaymentApplication,
    QBXMLError,
    build_customer_query,
    build_envelope,
    build_invoice_query,
    build_receive_payment_add,
    parse_responses,
)


def parse_request(xml: str) -> ET.Element:
    return ET.fromstring(xml)


def test_qbxml_version_is_enterprise_14_compatible():
    assert QBXML_VERSION == "13.0"


def test_customer_query_builds_valid_envelope():
    xml = build_customer_query("customer-001", list_id="CUST-001")

    root = parse_request(xml)
    request = root.find(".//CustomerQueryRq")

    assert request is not None
    assert request.attrib["requestID"] == "customer-001"
    assert request.findtext("ListID") == "CUST-001"
    assert request.findtext("MaxReturned") is None
    assert request.findtext("IncludeRetElement") == "ListID"


def test_customer_query_supports_full_name():
    xml = build_customer_query(
        "customer-002", full_name="African Daisy Limited"
    )

    request = parse_request(xml).find(".//CustomerQueryRq")

    assert request is not None
    assert request.findtext("FullName") == "African Daisy Limited"


def test_customer_query_rejects_two_identifiers():
    with pytest.raises(QBXMLError):
        build_customer_query(
            "r1", list_id="C1", full_name="Customer One"
        )


def test_invoice_query_supports_date_range():
    xml = build_invoice_query(
        "invoice-001",
        txn_date_from=date(2026, 1, 1),
        txn_date_to=date(2026, 1, 31),
    )

    request = parse_request(xml).find(".//InvoiceQueryRq")

    assert request is not None
    assert request.findtext("TxnDateRangeFilter/FromTxnDate") == "2026-01-01"
    assert request.findtext("TxnDateRangeFilter/ToTxnDate") == "2026-01-31"
    assert request.findtext("IncludeLineItems") == "true"


def test_invoice_query_rejects_invalid_date_range():
    with pytest.raises(QBXMLError):
        build_invoice_query(
            "r1",
            txn_date_from=date(2026, 2, 1),
            txn_date_to=date(2026, 1, 1),
        )


def test_receive_payment_builds_explicit_invoice_allocation():
    xml = build_receive_payment_add(
        "payment-001",
        customer_list_id="CUST-001",
        total_amount=Decimal("15000.00"),
        txn_date=date(2026, 11, 3),
        ref_number="QK7ABC123",
        applications=[
            PaymentApplication("INV-001", Decimal("10000.00")),
            PaymentApplication("INV-002", Decimal("3000.00")),
        ],
        memo="M-Pesa collection",
    )

    root = parse_request(xml)
    request = root.find(".//ReceivePaymentAddRq")
    payment = root.find(".//ReceivePaymentAdd")

    assert request is not None
    assert payment is not None
    assert request.attrib["requestID"] == "payment-001"
    assert payment.findtext("CustomerRef/ListID") == "CUST-001"
    assert payment.findtext("TotalAmount") == "15000.00"
    assert payment.findtext("RefNumber") == "QK7ABC123"
    assert payment.findtext("Memo") == "M-Pesa collection"

    allocations = payment.findall("AppliedToTxnAdd")
    assert len(allocations) == 2
    assert allocations[0].findtext("TxnID") == "INV-001"
    assert allocations[0].findtext("PaymentAmount") == "10000.00"
    assert allocations[1].findtext("TxnID") == "INV-002"
    assert allocations[1].findtext("PaymentAmount") == "3000.00"


def test_receive_payment_allows_unapplied_balance():
    xml = build_receive_payment_add(
        "payment-002",
        customer_list_id="CUST-001",
        total_amount=Decimal("1000.00"),
        txn_date=date(2026, 11, 3),
        ref_number="MPESA001",
        applications=[
            PaymentApplication("INV-001", Decimal("600.00"))
        ],
    )

    payment = parse_request(xml).find(".//ReceivePaymentAdd")

    assert payment is not None
    assert payment.findtext("TotalAmount") == "1000.00"
    assert payment.findtext(
        "AppliedToTxnAdd/PaymentAmount"
    ) == "600.00"


def test_receive_payment_rejects_overallocation():
    with pytest.raises(QBXMLError):
        build_receive_payment_add(
            "payment-003",
            customer_list_id="CUST-001",
            total_amount=Decimal("1000.00"),
            txn_date=date(2026, 11, 3),
            ref_number="MPESA002",
            applications=[
                PaymentApplication("INV-001", Decimal("1100.00"))
            ],
        )


def test_receive_payment_rejects_duplicate_invoice_ids():
    with pytest.raises(QBXMLError):
        build_receive_payment_add(
            "payment-004",
            customer_list_id="CUST-001",
            total_amount=Decimal("1000.00"),
            txn_date=date(2026, 11, 3),
            ref_number="MPESA003",
            applications=[
                PaymentApplication("INV-001", Decimal("300.00")),
                PaymentApplication("INV-001", Decimal("200.00")),
            ],
        )


def test_receive_payment_rejects_zero_amount():
    with pytest.raises(QBXMLError):
        build_receive_payment_add(
            "payment-005",
            customer_list_id="CUST-001",
            total_amount=Decimal("0"),
            txn_date=date(2026, 11, 3),
            ref_number="MPESA004",
        )


def test_envelope_rejects_empty_requests():
    with pytest.raises(QBXMLError):
        build_envelope([])


def test_envelope_rejects_invalid_error_policy():
    with pytest.raises(QBXMLError):
        build_envelope([ET.Element("CustomerQueryRq")], on_error="ignore")


def test_parser_reads_successful_response():
    xml = """<?xml version="1.0"?>
    <QBXML>
      <QBXMLMsgsRs>
        <CustomerQueryRs requestID="customer-001"
          statusCode="0" statusSeverity="Info"
          statusMessage="Status OK">
          <CustomerRet><ListID>CUST-001</ListID></CustomerRet>
        </CustomerQueryRs>
      </QBXMLMsgsRs>
    </QBXML>"""

    results = parse_responses(xml)

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].request_id == "customer-001"
    assert results[0].status_code == 0


def test_parser_preserves_quickbooks_error_status():
    xml = """<QBXML>
      <QBXMLMsgsRs>
        <ReceivePaymentAddRs requestID="payment-001"
          statusCode="3120" statusSeverity="Error"
          statusMessage="Object cannot be found">
        </ReceivePaymentAddRs>
      </QBXMLMsgsRs>
    </QBXML>"""

    result = parse_responses(xml)[0]

    assert result.ok is False
    assert result.status_code == 3120
    assert result.status_severity == "Error"


def test_parser_rejects_invalid_xml():
    with pytest.raises(QBXMLError):
        parse_responses("<QBXML>")


def test_parser_rejects_missing_response_section():
    with pytest.raises(QBXMLError):
        parse_responses("<QBXML/>")


def test_parser_rejects_missing_status_fields():
    with pytest.raises(QBXMLError):
        parse_responses(
            "<QBXML><QBXMLMsgsRs><CustomerQueryRs/></QBXMLMsgsRs></QBXML>"
        )
