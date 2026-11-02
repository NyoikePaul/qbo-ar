from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal

import pytest

from qbd_ar.qbxml import PaymentApplication, build_receive_payment_add
from qbd_ar.web_connector import (
    QBWCRequestQueue,
    QBWCService,
    SessionError,
    SessionStore,
    create_soap_application,
)

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
SERVICE_NS = "http://developer.intuit.com/"

SUCCESS_RESPONSE = """<?xml version="1.0"?>
<QBXML>
  <QBXMLMsgsRs>
    <CustomerQueryRs requestID="customer-001"
      statusCode="0" statusSeverity="Info"
      statusMessage="Status OK">
      <CustomerRet><ListID>CUST-001</ListID></CustomerRet>
    </CustomerQueryRs>
  </QBXMLMsgsRs>
</QBXML>"""

ERROR_RESPONSE = """<?xml version="1.0"?>
<QBXML>
  <QBXMLMsgsRs>
    <ReceivePaymentAddRs requestID="payment-001"
      statusCode="3120" statusSeverity="Error"
      statusMessage="Object cannot be found"/>
  </QBXMLMsgsRs>
</QBXML>"""


def request_xml() -> str:
    from qbd_ar.qbxml import build_customer_query

    return build_customer_query("customer-001", list_id="CUST-001")


def make_service(tmp_path, *, with_job=True):
    queue = QBWCRequestQueue(tmp_path / "queue.sqlite3")
    sessions = SessionStore(tmp_path / "sessions.sqlite3")

    if with_job:
        queue.enqueue(
            request_xml(),
            company_file="company.qbw",
            idempotency_key="test-customer-query",
        )

    service = QBWCService(
        queue,
        sessions,
        username="test-user",
        password="test-password",
    )

    return queue, sessions, service


def make_ticket(service: QBWCService) -> str:
    result = service.authenticate("test-user", "test-password")
    assert result[0]
    return result[0]


def test_authenticate_returns_ticket_when_work_exists(tmp_path):
    _, sessions, service = make_service(tmp_path)

    result = service.authenticate("test-user", "test-password")

    assert result[0]
    assert result[1] == ""
    assert sessions.get_active(result[0])["state"] == "active"


def test_authenticate_rejects_invalid_credentials(tmp_path):
    _, sessions, service = make_service(tmp_path)

    result = service.authenticate("test-user", "wrong-password")

    assert result == ["", "nvu"]


def test_authenticate_returns_none_when_no_work_exists(tmp_path):
    _, _, service = make_service(tmp_path, with_job=False)

    assert service.authenticate("test-user", "test-password") == ["", "none"]


def test_server_and_client_version_callbacks(tmp_path):
    _, _, service = make_service(tmp_path)

    assert service.serverVersion() == "0.3.0"
    assert service.clientVersion("2.3.0.0") == ""


def test_send_request_claims_queued_job(tmp_path):
    queue, _, service = make_service(tmp_path)
    ticket = make_ticket(service)

    xml = service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )

    assert xml == request_xml()
    assert queue.counts()["in_flight"] == 1


def test_repeated_send_request_returns_same_in_flight_job(tmp_path):
    _, _, service = make_service(tmp_path)
    ticket = make_ticket(service)

    first = service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )
    second = service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )

    assert first == second


def test_send_request_returns_empty_when_queue_is_drained(tmp_path):
    _, _, service = make_service(tmp_path, with_job=False)
    ticket = service.sessions.create("test-user")

    result = service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )

    assert result == ""


def test_receive_response_completes_job(tmp_path):
    queue, sessions, service = make_service(tmp_path)
    ticket = make_ticket(service)

    service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )

    result = service.receiveResponseXML(
        ticket, SUCCESS_RESPONSE, "", ""
    )

    assert result == 100
    assert queue.counts()["completed"] == 1
    assert sessions.get_active(ticket)["current_job_id"] is None


def test_receive_response_preserves_quickbooks_error(tmp_path):
    queue, sessions, service = make_service(tmp_path)
    ticket = make_ticket(service)

    service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )

    result = service.receiveResponseXML(
        ticket, ERROR_RESPONSE, "", ""
    )

    assert result == -1
    assert queue.counts()["failed"] == 1
    assert "3120" in service.getLastError(ticket)

    failed_jobs = queue.counts()
    assert failed_jobs["failed"] == 1


def test_receive_response_handles_hresult(tmp_path):
    queue, _, service = make_service(tmp_path)
    ticket = make_ticket(service)

    service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )

    result = service.receiveResponseXML(
        ticket, "", "0x80040400", "Invalid request XML"
    )

    assert result == -1
    assert queue.counts()["failed"] == 1


def test_receive_response_handles_invalid_xml(tmp_path):
    queue, _, service = make_service(tmp_path)
    ticket = make_ticket(service)

    service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )

    result = service.receiveResponseXML(
        ticket, "<broken", "", ""
    )

    assert result == -1
    assert queue.counts()["failed"] == 1


def test_receive_response_rejects_missing_active_job(tmp_path):
    _, sessions, service = make_service(tmp_path, with_job=False)
    ticket = sessions.create("test-user")

    result = service.receiveResponseXML(
        ticket, SUCCESS_RESPONSE, "", ""
    )

    assert result == -1


def test_close_connection_closes_session(tmp_path):
    _, sessions, service = make_service(tmp_path)
    ticket = make_ticket(service)

    result = service.closeConnection(ticket)

    assert result == "Session closed."
    assert sessions.get(ticket)["state"] == "closed"

    with pytest.raises(SessionError):
        sessions.get_active(ticket)


def test_connection_error_marks_active_job_failed(tmp_path):
    queue, _, service = make_service(tmp_path)
    ticket = make_ticket(service)

    service.sendRequestXML(
        ticket, "", "company.qbw", "US", 13, 0
    )

    result = service.connectionError(
        ticket, "0x80040401", "Could not access QuickBooks"
    )

    assert result == "done"
    assert queue.counts()["failed"] == 1


def test_payment_qbxml_is_supported_by_queue(tmp_path):
    queue, _, _ = make_service(tmp_path, with_job=False)

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
        company_file="company.qbw",
        idempotency_key="mpesa:QK7ABC123",
    )

    assert job.state == "pending"
    assert "ReceivePaymentAddRq" in job.request_xml


def call_wsgi(app, *, method="GET", path="/qbwc",
              query="", body=b"", soap_action=""):
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "HTTP_HOST": "localhost:8000",
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(body),
        "CONTENT_LENGTH": str(len(body)),
        "HTTP_SOAPACTION": soap_action,
    }

    output = b"".join(app(environ, start_response))
    return captured, output


def soap_request(operation, parameters=""):
    return f"""<?xml version="1.0"?>
    <soap:Envelope xmlns:soap="{SOAP_NS}"
                   xmlns:qbwc="{SERVICE_NS}">
      <soap:Body>
        <qbwc:{operation}>{parameters}</qbwc:{operation}>
      </soap:Body>
    </soap:Envelope>
    """.encode()


def test_wsdl_is_valid_xml_and_contains_all_callbacks(tmp_path):
    _, _, service = make_service(tmp_path)
    app = create_soap_application(service)

    response, body = call_wsgi(
        app, query="wsdl"
    )

    root = ET.fromstring(body)

    assert response["status"] == "200 OK"
    assert root.attrib["targetNamespace"] == SERVICE_NS

    operations = {
        node.attrib["name"]
        for node in root.findall(".//{http://schemas.xmlsoap.org/wsdl/}operation")
    }

    assert "authenticate" in operations
    assert "sendRequestXML" in operations
    assert "receiveResponseXML" in operations
    assert "closeConnection" in operations


def test_health_endpoint(tmp_path):
    _, _, service = make_service(tmp_path)
    app = create_soap_application(service)

    response, body = call_wsgi(
        app, path="/health"
    )

    assert response["status"] == "200 OK"
    assert b'"status":"ok"' in body


def test_soap_server_version_operation(tmp_path):
    _, _, service = make_service(tmp_path)
    app = create_soap_application(service)

    body = soap_request("serverVersion")
    response, output = call_wsgi(
        app,
        method="POST",
        body=body,
        soap_action=f"{SERVICE_NS}serverVersion",
    )

    root = ET.fromstring(output)
    result = root.find(
        f".//{{{SERVICE_NS}}}serverVersionResult"
    )

    assert response["status"] == "200 OK"
    assert result is not None
    assert result.text == "0.3.0"


def test_soap_authenticate_returns_array_of_strings(tmp_path):
    _, _, service = make_service(tmp_path)
    app = create_soap_application(service)

    body = soap_request(
        "authenticate",
        "<qbwc:strUserName>test-user</qbwc:strUserName>"
        "<qbwc:strPassword>test-password</qbwc:strPassword>",
    )

    response, output = call_wsgi(
        app,
        method="POST",
        body=body,
        soap_action=f"{SERVICE_NS}authenticate",
    )

    root = ET.fromstring(output)
    items = root.findall(
        f".//{{{SERVICE_NS}}}authenticateResult/"
        f"{{{SERVICE_NS}}}string"
    )

    assert response["status"] == "200 OK"
    assert len(items) == 2
    assert items[0].text


def test_soap_rejects_invalid_action(tmp_path):
    _, _, service = make_service(tmp_path)
    app = create_soap_application(service)

    response, output = call_wsgi(
        app,
        method="POST",
        body=soap_request("serverVersion"),
        soap_action=f"{SERVICE_NS}authenticate",
    )

    root = ET.fromstring(output)

    assert response["status"] == "500 Internal Server Error"
    assert root.find(f".//{{{SOAP_NS}}}Fault") is not None


def test_soap_rejects_non_soap_payload(tmp_path):
    _, _, service = make_service(tmp_path)
    app = create_soap_application(service)

    response, output = call_wsgi(
        app,
        method="POST",
        body=b"<NotSOAP/>",
    )

    root = ET.fromstring(output)

    assert response["status"] == "500 Internal Server Error"
    assert root.find(f".//{{{SOAP_NS}}}Fault") is not None


def test_soap_rejects_unknown_path(tmp_path):
    _, _, service = make_service(tmp_path)
    app = create_soap_application(service)

    response, _ = call_wsgi(
        app, path="/unknown"
    )

    assert response["status"] == "404 Not Found"
