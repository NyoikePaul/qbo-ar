# QBWC Durable Request Queue

## Purpose

Persist qbXML requests before they are handed to QuickBooks Web Connector.

## Job lifecycle

`pending -> in_flight -> completed`

A failed request can return to `pending` while retry attempts remain.
Non-retryable failures and exhausted retries become `failed`.

## Controls

- SQLite transaction used when claiming jobs.
- FIFO ordering by creation time.
- Company-file filtering.
- Optional idempotency key.
- Conflicting idempotency-key reuse is rejected.
- Request and response XML are retained.
- Attempt counts and error details are persisted.
- Queue counts are available for monitoring.

## Integration boundary

The queue does not itself communicate with QuickBooks.
The SOAP Web Connector service will use this queue to return requests
and record responses.

## Storage

Use a persistent local database path, not a temporary directory.
Protect the database and backups because request and response XML may
contain customer and accounting information.
