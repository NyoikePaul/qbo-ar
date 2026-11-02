# QuickBooks Desktop Enterprise 14.0 — qbXML Engine

## Scope

This package provides the first Desktop integration layer for the A/R toolkit.

Target:
- QuickBooks Desktop Enterprise 14.0
- qbXML 13.0
- Python standard-library XML processing

The existing `qbo_ar` package remains the independent QuickBooks Online adapter.

## Initial operations

| Operation | Request |
|---|---|
| Read customer records | CustomerQueryRq |
| Read invoice records | InvoiceQueryRq |
| Record customer receipt | ReceivePaymentAddRq |
| Parse QuickBooks result | QBXMLMsgsRs |

## Customer receipt rules

1. The customer must be identified by its QuickBooks ListID.
2. Each invoice allocation must use the QuickBooks invoice TxnID.
3. Allocation amounts must be positive.
4. Total invoice allocations must not exceed the received amount.
5. Any excess remains an unapplied customer payment.
6. The M-Pesa transaction reference is preserved as RefNumber.
7. The request builder does not transmit or post transactions itself.

## Important integration boundary

The qbXML codec only builds and parses XML.

A Windows-hosted QuickBooks Web Connector service is still required to:
- authenticate the connector;
- provide pending qbXML requests;
- receive QuickBooks responses;
- persist request and response state;
- retry safely after connection failures.

No live QuickBooks transaction is posted by the unit tests.

## Compatibility

The request version is pinned to qbXML 13.0 for the Enterprise 14.0 target.

Before production use, verify the supported request fields against the installed Desktop SDK and the actual company file.
