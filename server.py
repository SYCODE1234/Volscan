"""Local web server for the volatility scanner.

    .venv/bin/python server.py            # http://localhost:5340
    .venv/bin/python server.py --port N

Endpoints
    GET  /                 the app
    GET  /api/scan         latest scan results (404 if no scan yet)
    GET  /api/status       {"running": bool, "log": [...]}
    POST /api/rescan       start a new scan in the background
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import scan

ROOT = Path(__file__).resolve().parent
STATE = {"running": False, "log": [], "error": None}
LOCK = threading.Lock()


def _log(msg: str) -> None:
    print(msg, flush=True)
    with LOCK:
        STATE["log"].append(msg)
        STATE["log"] = STATE["log"][-50:]


def _scan_worker() -> None:
    try:
        scan.run_scan(log=_log)
    except Exception as exc:  # noqa: BLE001 - surface anything to the UI
        _log(f"ERROR: {exc}")
        with LOCK:
            STATE["error"] = str(exc)
    finally:
        with LOCK:
            STATE["running"] = False


def start_scan() -> bool:
    with LOCK:
        if STATE["running"]:
            return False
        STATE["running"] = True
        STATE["log"] = []
        STATE["error"] = None
    threading.Thread(target=_scan_worker, daemon=True).start()
    return True


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/api/scan":
            if not scan.SCAN_FILE.exists():
                self._json({"error": "no scan yet"}, 404)
                return
            body = scan.SCAN_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/status":
            with LOCK:
                self._json(dict(STATE))
            return
        if self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/rescan":
            started = start_scan()
            self._json({"started": started, "running": True})
            return
        self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):  # quieter default logging
        if "/api/status" not in (args[0] if args else ""):
            super().log_message(fmt, *args)


def main() -> None:
    port = 5340
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if not scan.SCAN_FILE.exists():
        print("No previous scan found; starting one now.")
        start_scan()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"volscan running at http://localhost:{port}  (type this in the address bar)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
