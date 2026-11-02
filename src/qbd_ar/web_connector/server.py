"""Local QBWC SOAP development server."""

from __future__ import annotations

import os
from pathlib import Path
from wsgiref.simple_server import make_server

from .queue import QBWCRequestQueue
from .service import QBWCService
from .sessions import SessionStore
from .soap import create_soap_application


def main() -> None:
    username = os.environ.get("QBWC_USERNAME", "")
    password = os.environ.get("QBWC_PASSWORD", "")

    if not username or not password:
        raise SystemExit(
            "Set QBWC_USERNAME and QBWC_PASSWORD before starting the service."
        )

    data_dir = Path(
        os.environ.get(
            "QBWC_DATA_DIR",
            str(Path.home() / ".qbd_ar"),
        )
    ).expanduser()

    queue_db = os.environ.get(
        "QBWC_QUEUE_DB",
        str(data_dir / "queue.sqlite3"),
    )
    session_db = os.environ.get(
        "QBWC_SESSION_DB",
        str(data_dir / "sessions.sqlite3"),
    )

    queue = QBWCRequestQueue(queue_db)
    sessions = SessionStore(session_db)

    service = QBWCService(
        queue,
        sessions,
        username=username,
        password=password,
        company_file=os.environ.get("QBWC_COMPANY_FILE", ""),
    )

    application = create_soap_application(service)

    host = os.environ.get("QBWC_HOST", "127.0.0.1")
    port = int(os.environ.get("QBWC_PORT", "8000"))

    print(f"QBWC development endpoint: http://localhost:{port}/qbwc")
    print(f"WSDL: http://localhost:{port}/qbwc?wsdl")
    print(f"Health: http://localhost:{port}/health")
    print("Development server only. Use HTTPS and a production WSGI server for deployment.")

    with make_server(host, port, application) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nQBWC server stopped.")


if __name__ == "__main__":
    main()
