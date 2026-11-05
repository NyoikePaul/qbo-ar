"""SOAP 1.1 WSGI transport and WSDL for QuickBooks Web Connector."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any
from xml.sax.saxutils import quoteattr

import defusedxml.ElementTree as DET

SERVICE_NS = "http://developer.intuit.com/"
SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
WSDL_NS = "http://schemas.xmlsoap.org/wsdl/"
WSDL_SOAP_NS = "http://schemas.xmlsoap.org/wsdl/soap/"
XSD_NS = "http://www.w3.org/2001/XMLSchema"

ET.register_namespace("soap", SOAP_NS)


OPERATIONS: dict[str, tuple[list[tuple[str, str]], str]] = {
    "authenticate": (
        [("strUserName", "string"), ("strPassword", "string")],
        "tns:ArrayOfString",
    ),
    "clientVersion": (
        [("strVersion", "string")],
        "xsd:string",
    ),
    "serverVersion": (
        [],
        "xsd:string",
    ),
    "sendRequestXML": (
        [
            ("ticket", "string"),
            ("strHCPResponse", "string"),
            ("strCompanyFileName", "string"),
            ("qbXMLCountry", "string"),
            ("qbXMLMajorVers", "int"),
            ("qbXMLMinorVers", "int"),
        ],
        "xsd:string",
    ),
    "receiveResponseXML": (
        [
            ("ticket", "string"),
            ("response", "string"),
            ("hresult", "string"),
            ("message", "string"),
        ],
        "xsd:int",
    ),
    "getLastError": (
        [("ticket", "string")],
        "xsd:string",
    ),
    "connectionError": (
        [
            ("ticket", "string"),
            ("hresult", "string"),
            ("message", "string"),
        ],
        "xsd:string",
    ),
    "closeConnection": (
        [("ticket", "string")],
        "xsd:string",
    ),
}


def render_wsdl(endpoint: str) -> bytes:
    """Generate a document/literal SOAP 1.1 WSDL."""
    schema: list[str] = [
        f'<xsd:schema targetNamespace="{SERVICE_NS}" '
        'elementFormDefault="qualified">',
        """
        <xsd:complexType name="ArrayOfString">
          <xsd:sequence>
            <xsd:element name="string" type="xsd:string"
              minOccurs="0" maxOccurs="unbounded" nillable="true"/>
          </xsd:sequence>
        </xsd:complexType>
        """,
    ]

    for name, (parameters, result_type) in OPERATIONS.items():
        param_xml = "".join(
            f'<xsd:element name="{param}" type="xsd:{kind}" '
            'minOccurs="0"/>'
            for param, kind in parameters
        )

        schema.append(
            f'<xsd:element name="{name}"><xsd:complexType>'
            f'<xsd:sequence>{param_xml}</xsd:sequence>'
            f'</xsd:complexType></xsd:element>'
        )

        schema.append(
            f'<xsd:element name="{name}Response"><xsd:complexType>'
            '<xsd:sequence>'
            f'<xsd:element name="{name}Result" type="{result_type}" '
            'minOccurs="0"/>'
            '</xsd:sequence></xsd:complexType></xsd:element>'
        )

    schema.append("</xsd:schema>")

    messages: list[str] = []
    port_operations: list[str] = []
    binding_operations: list[str] = []

    for name in OPERATIONS:
        messages.extend([
            f'<wsdl:message name="{name}SoapIn">'
            f'<wsdl:part name="parameters" element="tns:{name}"/>'
            '</wsdl:message>',
            f'<wsdl:message name="{name}SoapOut">'
            f'<wsdl:part name="parameters" element="tns:{name}Response"/>'
            '</wsdl:message>',
        ])

        port_operations.append(
            f'<wsdl:operation name="{name}">'
            f'<wsdl:input message="tns:{name}SoapIn"/>'
            f'<wsdl:output message="tns:{name}SoapOut"/>'
            '</wsdl:operation>'
        )

        binding_operations.append(
            f'<wsdl:operation name="{name}">'
            f'<soap:operation soapAction="{SERVICE_NS}{name}" '
            'style="document"/>'
            '<wsdl:input><soap:body use="literal"/></wsdl:input>'
            '<wsdl:output><soap:body use="literal"/></wsdl:output>'
            '</wsdl:operation>'
        )

    document = f"""<?xml version="1.0" encoding="utf-8"?>
<wsdl:definitions
 xmlns:wsdl="{WSDL_NS}"
 xmlns:soap="{WSDL_SOAP_NS}"
 xmlns:xsd="{XSD_NS}"
 xmlns:tns="{SERVICE_NS}"
 targetNamespace="{SERVICE_NS}"
 name="QBWebConnectorSvc">

 <wsdl:types>
  {''.join(schema)}
 </wsdl:types>

 {''.join(messages)}

 <wsdl:portType name="QBWebConnectorSvcSoap">
  {''.join(port_operations)}
 </wsdl:portType>

 <wsdl:binding name="QBWebConnectorSvcSoap"
   type="tns:QBWebConnectorSvcSoap">
  <soap:binding transport="http://schemas.xmlsoap.org/soap/http"
    style="document"/>
  {''.join(binding_operations)}
 </wsdl:binding>

 <wsdl:service name="QBWebConnectorSvc">
  <wsdl:port name="QBWebConnectorSvcSoap"
    binding="tns:QBWebConnectorSvcSoap">
   <soap:address location={quoteattr(endpoint)}/>
  </wsdl:port>
 </wsdl:service>
</wsdl:definitions>
"""

    return document.encode("utf-8")


def _soap_envelope(operation: str, result: Any) -> bytes:
    envelope = ET.Element(f"{{{SOAP_NS}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP_NS}}}Body")

    response = ET.SubElement(
        body,
        f"{{{SERVICE_NS}}}{operation}Response",
    )

    result_node = ET.SubElement(
        response,
        f"{{{SERVICE_NS}}}{operation}Result",
    )

    if isinstance(result, (list, tuple)):
        for item in result:
            child = ET.SubElement(
                result_node,
                f"{{{SERVICE_NS}}}string",
            )
            child.text = str(item)
    else:
        result_node.text = "" if result is None else str(result)

    return ET.tostring(
        envelope,
        encoding="utf-8",
        xml_declaration=True,
    )


def _soap_fault(message: str) -> bytes:
    envelope = ET.Element(f"{{{SOAP_NS}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP_NS}}}Body")
    fault = ET.SubElement(body, f"{{{SOAP_NS}}}Fault")

    ET.SubElement(fault, "faultcode").text = "soap:Server"
    ET.SubElement(fault, "faultstring").text = message[:1000]

    return ET.tostring(
        envelope,
        encoding="utf-8",
        xml_declaration=True,
    )


log = logging.getLogger(__name__)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class QBWCSOAPApplication:
    """Small SOAP 1.1 WSGI application for local QBWC integration."""

    MAX_BODY = 5 * 1024 * 1024

    def __init__(self, service: Any) -> None:
        self.service = service

    @staticmethod
    def _response(start_response: Any, status: str, body: bytes,
                  content_type: str = "text/xml; charset=utf-8") -> list[bytes]:
        start_response(status, [
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ])
        return [body]

    def _dispatch(self, operation: str, values: dict[str, str]) -> Any:
        if operation not in OPERATIONS:
            raise ValueError(f"Unsupported QBWC operation: {operation}")

        if operation == "sendRequestXML":
            return self.service.sendRequestXML(
                values.get("ticket", ""),
                values.get("strHCPResponse", ""),
                values.get("strCompanyFileName", ""),
                values.get("qbXMLCountry", ""),
                int(values.get("qbXMLMajorVers", "0") or 0),
                int(values.get("qbXMLMinorVers", "0") or 0),
            )

        if operation == "receiveResponseXML":
            return self.service.receiveResponseXML(
                values.get("ticket", ""),
                values.get("response", ""),
                values.get("hresult", ""),
                values.get("message", ""),
            )

        parameters = {
            "authenticate": ("strUserName", "strPassword"),
            "clientVersion": ("strVersion",),
            "serverVersion": (),
            "getLastError": ("ticket",),
            "connectionError": ("ticket", "hresult", "message"),
            "closeConnection": ("ticket",),
        }

        args = [
            values.get(name, "")
            for name in parameters[operation]
        ]

        return getattr(self.service, operation)(*args)

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "/")
        method = environ.get("REQUEST_METHOD", "GET").upper()

        if method == "GET" and path == "/health":
            return self._response(
                start_response,
                "200 OK",
                b'{"status":"ok","service":"qbwc"}',
                "application/json",
            )

        if method == "GET" and path == "/qbwc":
            endpoint = (
                f"{environ.get('wsgi.url_scheme', 'http')}://"
                f"{environ.get('HTTP_HOST', environ.get('SERVER_NAME', 'localhost'))}"
                f"{path}"
            )

            if environ.get("QUERY_STRING", "").lower() == "wsdl":
                return self._response(
                    start_response,
                    "200 OK",
                    render_wsdl(endpoint),
                )

            return self._response(
                start_response,
                "200 OK",
                b"QuickBooks Web Connector SOAP endpoint. Use ?wsdl.",
                "text/plain; charset=utf-8",
            )

        if method != "POST" or path != "/qbwc":
            return self._response(
                start_response,
                "404 Not Found",
                b"Not found",
                "text/plain; charset=utf-8",
            )

        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)

            if length <= 0 or length > self.MAX_BODY:
                raise ValueError("Invalid SOAP request size")

            raw = environ["wsgi.input"].read(length)
            if len(raw) != length:
                raise ValueError("Incomplete SOAP request body")

            root = DET.fromstring(raw)

            if root.tag != f"{{{SOAP_NS}}}Envelope":
                raise ValueError("Expected a SOAP 1.1 Envelope")

            body = root.find(f"{{{SOAP_NS}}}Body")
            if body is None or len(body) != 1:
                raise ValueError("SOAP Body must contain one operation")

            operation_node = body[0]
            if not operation_node.tag.startswith(f"{{{SERVICE_NS}}}"):
                raise ValueError("Unexpected SOAP operation namespace")

            operation = _local_name(operation_node.tag)

            action = environ.get("HTTP_SOAPACTION", "").strip().strip('"')
            expected_action = f"{SERVICE_NS}{operation}"

            if action and action != expected_action:
                raise ValueError("SOAPAction does not match the operation")

            values = {
                _local_name(child.tag): child.text or ""
                for child in list(operation_node)
            }

            result = self._dispatch(operation, values)
            output = _soap_envelope(operation, result)

            return self._response(start_response, "200 OK", output)

        except (ValueError, ET.ParseError) as exc:
            output = _soap_fault(str(exc))
            return self._response(start_response, "500 Internal Server Error", output)
        except Exception:
            log.exception("Unhandled error while serving a SOAP request")
            output = _soap_fault("Internal server error")
            return self._response(start_response, "500 Internal Server Error", output)


def create_soap_application(service: Any) -> QBWCSOAPApplication:
    return QBWCSOAPApplication(service)
