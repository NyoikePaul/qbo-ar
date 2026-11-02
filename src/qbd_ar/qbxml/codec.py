"""QuickBooks Desktop qbXML 13.0 request and response codec.

Targets QuickBooks Desktop Enterprise 14.0.
Uses the standard library XML implementation.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

QBXML_VERSION = "13.0"
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


class QBXMLError(ValueError):
    """Invalid qbXML request or response."""


@dataclass(frozen=True)
class PaymentApplication:
    """An invoice allocation within a customer receipt."""

    txn_id: str
    amount: Decimal


@dataclass(frozen=True)
class QBXMLResponse:
    """Normalised QuickBooks response status."""

    request_id: str | None
    response_type: str
    status_code: int
    status_severity: str
    status_message: str
    body: ET.Element

    @property
    def ok(self) -> bool:
        return self.status_code == 0


def _required(value: str, field: str) -> str:
    result = str(value).strip()
    if not result:
        raise QBXMLError(f"{field} is required")
    return result


def _money(value: Decimal | str | int, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise QBXMLError(f"{field} must be a valid decimal") from None

    if not amount.is_finite():
        raise QBXMLError(f"{field} must be finite")

    amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)

    if amount < ZERO:
        raise QBXMLError(f"{field} cannot be negative")

    return amount


def _money_text(value: Decimal, field: str) -> str:
    amount = _money(value, field)
    return format(amount, ".2f")


def _append_text(parent: ET.Element, tag: str, value: Any) -> ET.Element:
    node = ET.SubElement(parent, tag)
    node.text = str(value)
    return node


def _append_ref(
    parent: ET.Element,
    tag: str,
    *,
    list_id: str | None = None,
    full_name: str | None = None,
) -> ET.Element:
    if bool(list_id) == bool(full_name):
        raise QBXMLError(
            f"{tag} requires exactly one of list_id or full_name"
        )

    node = ET.SubElement(parent, tag)
    child_tag = "ListID" if list_id else "FullName"
    child_value = list_id if list_id else full_name
    _append_text(node, child_tag, _required(child_value, tag))
    return node


def build_envelope(
    requests: Iterable[ET.Element],
    *,
    on_error: str = "stopOnError",
) -> str:
    """Wrap one or more request nodes in a qbXML envelope."""
    if on_error not in {"stopOnError", "continueOnError"}:
        raise QBXMLError("Unsupported on_error policy")

    request_list = list(requests)
    if not request_list:
        raise QBXMLError("At least one qbXML request is required")

    root = ET.Element("QBXML")
    messages = ET.SubElement(
        root, "QBXMLMsgsRq", {"onError": on_error}
    )

    for request in request_list:
        messages.append(request)

    body = ET.tostring(root, encoding="unicode")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<?qbxml version="{QBXML_VERSION}"?>\n'
        f"{body}"
    )


def build_customer_query(
    request_id: str,
    *,
    list_id: str | None = None,
    full_name: str | None = None,
    max_returned: int | None = None,
) -> str:
    """Build a CustomerQueryRq."""
    request_id = _required(request_id, "request_id")

    if list_id and full_name:
        raise QBXMLError("Use list_id or full_name, not both")

    request = ET.Element(
        "CustomerQueryRq", {"requestID": request_id}
    )

    if list_id:
        _append_text(request, "ListID", list_id)
    elif full_name:
        _append_text(request, "FullName", full_name)

    if max_returned is not None:
        if max_returned < 1:
            raise QBXMLError("max_returned must be positive")
        _append_text(request, "MaxReturned", max_returned)

    for field in (
        "ListID",
        "FullName",
        "IsActive",
        "Balance",
        "Phone",
        "Email",
        "CurrencyRef",
    ):
        _append_text(request, "IncludeRetElement", field)

    return build_envelope([request])


def build_invoice_query(
    request_id: str,
    *,
    txn_date_from: date | None = None,
    txn_date_to: date | None = None,
    max_returned: int | None = None,
) -> str:
    """Build an InvoiceQueryRq with optional transaction date filters."""
    request_id = _required(request_id, "request_id")

    if txn_date_from and txn_date_to and txn_date_from > txn_date_to:
        raise QBXMLError("txn_date_from cannot exceed txn_date_to")

    request = ET.Element(
        "InvoiceQueryRq", {"requestID": request_id}
    )

    if txn_date_from or txn_date_to:
        date_filter = ET.SubElement(request, "TxnDateRangeFilter")

        if txn_date_from:
            _append_text(
                date_filter, "FromTxnDate", txn_date_from.isoformat()
            )

        if txn_date_to:
            _append_text(
                date_filter, "ToTxnDate", txn_date_to.isoformat()
            )

    if max_returned is not None:
        if max_returned < 1:
            raise QBXMLError("max_returned must be positive")
        _append_text(request, "MaxReturned", max_returned)

    _append_text(request, "IncludeLineItems", "true")

    for field in (
        "TxnID",
        "RefNumber",
        "TxnDate",
        "DueDate",
        "CustomerRef",
        "TotalAmount",
        "BalanceRemaining",
        "CurrencyRef",
    ):
        _append_text(request, "IncludeRetElement", field)

    return build_envelope([request])


def build_receive_payment_add(
    request_id: str,
    *,
    customer_list_id: str,
    total_amount: Decimal,
    txn_date: date,
    ref_number: str,
    applications: Sequence[PaymentApplication] = (),
    payment_method_list_id: str | None = None,
    deposit_account_list_id: str | None = None,
    memo: str | None = None,
) -> str:
    """Build a ReceivePaymentAddRq with explicit invoice allocations.

    Any amount not allocated to invoices remains unapplied in QuickBooks.
    This function builds XML only; it does not transmit or post anything.
    """
    request_id = _required(request_id, "request_id")
    customer_list_id = _required(customer_list_id, "customer_list_id")
    ref_number = _required(ref_number, "ref_number")

    total = _money(total_amount, "total_amount")
    if total <= ZERO:
        raise QBXMLError("total_amount must be greater than zero")

    ids: set[str] = set()
    allocated = ZERO

    for application in applications:
        txn_id = _required(application.txn_id, "application txn_id")
        amount = _money(application.amount, "application amount")

        if amount <= ZERO:
            raise QBXMLError("application amount must be positive")

        if txn_id in ids:
            raise QBXMLError(f"Duplicate invoice transaction ID: {txn_id}")

        ids.add(txn_id)
        allocated += amount

    if allocated > total:
        raise QBXMLError(
            "Invoice allocations cannot exceed the received amount"
        )

    request = ET.Element(
        "ReceivePaymentAddRq", {"requestID": request_id}
    )
    payment = ET.SubElement(request, "ReceivePaymentAdd")

    _append_ref(
        payment, "CustomerRef", list_id=customer_list_id
    )
    _append_text(payment, "TxnDate", txn_date.isoformat())
    _append_text(payment, "RefNumber", ref_number)
    _append_text(payment, "TotalAmount", _money_text(total, "total_amount"))

    if payment_method_list_id:
        _append_ref(
            payment,
            "PaymentMethodRef",
            list_id=payment_method_list_id,
        )

    if deposit_account_list_id:
        _append_ref(
            payment,
            "DepositToAccountRef",
            list_id=deposit_account_list_id,
        )

    if memo:
        _append_text(payment, "Memo", memo)

    for application in applications:
        applied = ET.SubElement(payment, "AppliedToTxnAdd")
        _append_text(applied, "TxnID", application.txn_id)
        _append_text(
            applied,
            "PaymentAmount",
            _money_text(application.amount, "application amount"),
        )

    return build_envelope([request])


def parse_responses(xml: str | bytes) -> list[QBXMLResponse]:
    """Parse response status records from QBXMLMsgsRs."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise QBXMLError(f"Invalid QuickBooks XML response: {exc}") from exc

    messages = root.find(".//QBXMLMsgsRs")
    if messages is None:
        raise QBXMLError("Response does not contain QBXMLMsgsRs")

    results: list[QBXMLResponse] = []

    for node in list(messages):
        code = node.attrib.get("statusCode")
        severity = node.attrib.get("statusSeverity")
        message = node.attrib.get("statusMessage")

        if code is None or severity is None or message is None:
            raise QBXMLError(
                f"Response {node.tag} is missing status attributes"
            )

        try:
            status_code = int(code)
        except ValueError:
            raise QBXMLError(
                f"Invalid status code in {node.tag}: {code!r}"
            ) from None

        results.append(
            QBXMLResponse(
                request_id=node.attrib.get("requestID"),
                response_type=node.tag,
                status_code=status_code,
                status_severity=severity,
                status_message=message,
                body=node,
            )
        )

    if not results:
        raise QBXMLError("QuickBooks returned no response records")

    return results
