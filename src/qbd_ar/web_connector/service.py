"""QuickBooks Web Connector callback implementation.

Implements the QBWC application callbacks independently of the
SOAP transport so they can be tested without QuickBooks installed.
"""

from __future__ import annotations

import hmac
from typing import Any

from qbd_ar.qbxml import QBXMLError, parse_responses

from .queue import QBWCRequestQueue
from .sessions import SessionStore


class QBWCService:
    """Business logic for the QuickBooks Web Connector protocol."""

    SERVER_VERSION = "0.3.0"

    def __init__(
        self,
        queue: QBWCRequestQueue,
        sessions: SessionStore,
        *,
        username: str,
        password: str,
        company_file: str = "",
    ) -> None:
        if not username or not password:
            raise ValueError("QBWC username and password are required")

        self.queue = queue
        self.sessions = sessions
        self.username = username
        self.password = password
        self.company_file = company_file

    def authenticate(
        self,
        strUserName: str,
        strPassword: str,
    ) -> list[str]:
        """Return [ticket, company file], or QBWC's standard rejection values."""
        valid_user = hmac.compare_digest(
            str(strUserName).encode(),
            self.username.encode(),
        )
        valid_password = hmac.compare_digest(
            str(strPassword).encode(),
            self.password.encode(),
        )

        if not (valid_user and valid_password):
            return ["", "nvu"]

        if self.queue.pending_count(
            company_file=self.company_file or None
        ) == 0:
            return ["", "none"]

        ticket = self.sessions.create(
            self.username,
            self.company_file,
        )

        return [ticket, self.company_file]

    def clientVersion(self, strVersion: str) -> str:
        """Empty string means the client may continue."""
        return ""

    def serverVersion(self) -> str:
        return self.SERVER_VERSION

    def _session(self, ticket: str) -> dict[str, Any]:
        return self.sessions.get_active(ticket)

    def sendRequestXML(
        self,
        ticket: str,
        strHCPResponse: str,
        strCompanyFileName: str,
        qbXMLCountry: str,
        qbXMLMajorVers: int,
        qbXMLMinorVers: int,
    ) -> str:
        """Return the next queued qbXML request, or empty string when done."""
        session = self._session(ticket)

        active_job_id = session["current_job_id"]

        # A repeated callback must not claim another job while the
        # previous request is still awaiting its QuickBooks response.
        if active_job_id:
            active_job = self.queue.get(active_job_id)

            if active_job and active_job.state == "in_flight":
                return active_job.request_xml

            self.sessions.clear_current_job(ticket)

        company_file = (
            session["company_file"]
            or strCompanyFileName
            or ""
        )

        if company_file and not session["company_file"]:
            self.sessions.set_company_file(ticket, company_file)

        job = self.queue.claim_next(
            company_file=company_file or None
        )

        if job is None:
            return ""

        self.sessions.set_current_job(ticket, job.job_id)
        return job.request_xml

    def receiveResponseXML(
        self,
        ticket: str,
        response: str,
        hresult: str,
        message: str,
    ) -> int:
        """Persist the QuickBooks response and return session completion percentage."""
        session = self._session(ticket)
        job_id = session["current_job_id"]

        if not job_id:
            self.sessions.set_error(
                ticket,
                "QuickBooks returned a response without an active request.",
            )
            return -1

        if hresult:
            error = f"QuickBooks HRESULT {hresult}: {message or 'No message'}"

            self.queue.fail(
                job_id,
                error_code=hresult,
                error_message=error,
                retryable=False,
                response_xml=response or None,
            )

            self.sessions.clear_current_job(ticket)
            self.sessions.set_error(ticket, error)
            return -1

        try:
            results = parse_responses(response)
        except QBXMLError as exc:
            error = f"Invalid QuickBooks response: {exc}"

            self.queue.fail(
                job_id,
                error_code="INVALID_QBXML_RESPONSE",
                error_message=error,
                retryable=False,
                response_xml=response or None,
            )

            self.sessions.clear_current_job(ticket)
            self.sessions.set_error(ticket, error)
            return -1

        errors = [
            result
            for result in results
            if result.status_code != 0
            and result.status_severity.lower() == "error"
        ]

        if errors:
            first = errors[0]
            error = (
                f"QuickBooks error {first.status_code}: "
                f"{first.status_message}"
            )

            self.queue.fail(
                job_id,
                error_code=str(first.status_code),
                error_message=error,
                retryable=False,
                response_xml=response,
            )

            self.sessions.clear_current_job(ticket)
            self.sessions.set_error(ticket, error)
            return -1

        self.queue.complete(job_id, response)
        self.sessions.clear_current_job(ticket, completed=True)

        company_file = session["company_file"]
        remaining = self.queue.pending_count(
            company_file=company_file or None
        )

        return 100 if remaining == 0 else 50

    def getLastError(self, ticket: str) -> str:
        """Return the last service error to QBWC."""
        session = self.sessions.get(ticket)

        if session is None:
            return "Unknown Web Connector session."

        return session["last_error"] or "Unspecified integration error."

    def connectionError(
        self,
        ticket: str,
        hresult: str,
        message: str,
    ) -> str:
        """Handle a failed QuickBooks connection without unsafe automatic reposting."""
        session = self.sessions.get(ticket)

        if session is None or session["state"] != "active":
            return "done"

        error = f"QuickBooks connection error {hresult}: {message}"

        job_id = session["current_job_id"]

        if job_id:
            self.queue.fail(
                job_id,
                error_code=hresult or "CONNECTION_ERROR",
                error_message=error,
                retryable=False,
            )
            self.sessions.clear_current_job(ticket)

        self.sessions.set_error(ticket, error)
        return "done"

    def closeConnection(self, ticket: str) -> str:
        """Close a session and prevent abandoned requests being silently reused."""
        session = self.sessions.get(ticket)

        if session is None:
            return "Session already closed."

        job_id = session["current_job_id"]

        if job_id and session["state"] == "active":
            self.queue.fail(
                job_id,
                error_code="SESSION_CLOSED",
                error_message="Web Connector session closed before response.",
                retryable=False,
            )

        self.sessions.close(ticket)
        return "Session closed."
