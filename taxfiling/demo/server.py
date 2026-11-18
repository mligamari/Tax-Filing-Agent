"""A small web server for the demo screen, on this computer only.

Standard library only (no web framework to install). The safeguards:

* it listens on 127.0.0.1, so no other computer on the network can reach it;
* every request must name this computer in its Host header (127.0.0.1 or localhost),
  which stops a web page on another site from reaching it through a DNS trick;
* every API call must carry a random key that is made when the demo starts and is only
  written into the page this server serves. Another web site open in the same browser
  cannot read that page, so it cannot call the API;
* the page may load nothing from the internet (Content-Security-Policy), and the server
  makes no outbound connection unless you choose Claude and press Send;
* uploads and finished files live in a temporary folder that is deleted when the demo stops.
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from .app import MAX_UPLOAD_BYTES, OUTPUTS, SAMPLES, YEARS, Demo, DemoError, to_json

STATIC = Path(__file__).with_name("static")
HOST = "127.0.0.1"
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".svg": "image/svg+xml"}


def make_handler(demo: Demo, key: str, port_ref: list[int]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "taxfiling-demo"
        sys_version = ""

        def log_message(self, fmt, *args):  # keep the terminal quiet; nothing here is worth logging
            pass

        # --- helpers ----------------------------------------------------------------

        def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(status, to_json(obj), "application/json")

        def _error(self, message: str, status: int = 400) -> None:
            self._json({"error": message}, status)

        def _host_ok(self) -> bool:
            port = port_ref[0]
            return self.headers.get("Host", "") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _key_ok(self, query: dict) -> bool:
            given = self.headers.get("X-Demo-Key") or (query.get("key") or [""])[0]
            return hmac.compare_digest(given.encode(), key.encode())

        def _body(self, limit: int) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise DemoError("That request is too large.")
            return self.rfile.read(length)

        def _guard(self) -> dict | None:
            url = urlsplit(self.path)
            if not self._host_ok():
                self._error("This demo only answers requests addressed to 127.0.0.1 or localhost.", 403)
                return None
            return {"path": url.path, "query": parse_qs(url.query)}

        # --- routes -----------------------------------------------------------------

        def do_GET(self):
            req = self._guard()
            if req is None:
                return
            path, query = req["path"], req["query"]
            if path in ("/", "/index.html"):
                html = (STATIC / "index.html").read_text(encoding="utf-8").replace("__DEMO_KEY__", key)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                target = STATIC / name
                if "/" in name or name.startswith(".") or not target.is_file() or target.suffix not in STATIC_TYPES:
                    return self._error("not found", 404)
                return self._send(200, target.read_bytes(), STATIC_TYPES[target.suffix])
            if not self._key_ok(query):
                return self._error("missing or wrong demo key; reload the page", 403)
            if path == "/api/options":
                return self._json({
                    "samples": [{"key": k, "title": v["title"], "detail": v["detail"], "year": v["year"]}
                                for k, v in SAMPLES.items()],
                    "outputs": [{"key": k, **v} for k, v in OUTPUTS.items()],
                    "years": list(YEARS),
                })
            if path == "/api/download":
                try:
                    name, data = demo.download((query.get("session") or [""])[0], (query.get("file") or [""])[0])
                except DemoError as exc:
                    return self._error(str(exc), 404)
                disposition = f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}"
                return self._send(200, data, "text/plain; charset=ascii", {"Content-Disposition": disposition})
            return self._error("not found", 404)

        def do_POST(self):
            req = self._guard()
            if req is None:
                return
            path, query = req["path"], req["query"]
            if not self._key_ok(query):
                return self._error("missing or wrong demo key; reload the page", 403)
            try:
                if path == "/api/upload":
                    data = self._body(MAX_UPLOAD_BYTES + 1)
                    return self._json(demo.open_upload(data, (query.get("name") or ["upload.xlsx"])[0]))
                if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                    return self._error("expected JSON", 415)
                body = json.loads(self._body(1_000_000) or b"{}")
                if path == "/api/sample":
                    return self._json(demo.open_sample(body.get("sample", "")))
                if path == "/api/propose":
                    return self._json(demo.propose(body.get("session", ""), body.get("ai", "none")))
                if path == "/api/build":
                    return self._json(demo.build(body.get("session", ""), body))
                return self._error("not found", 404)
            except DemoError as exc:
                return self._json({"error": str(exc), "audit": exc.audit}, 400)
            except json.JSONDecodeError:
                return self._error("expected JSON")
            except Exception as exc:  # a bug: say so on screen rather than leave the page waiting
                traceback.print_exc()
                return self._error(f"Unexpected problem ({type(exc).__name__}). Details are in the terminal.", 500)

        do_HEAD = do_GET

    return Handler


def serve(port: int = 8765, open_browser: bool = True, ready: threading.Event | None = None,
          server_ref: list | None = None) -> None:
    """Run until Ctrl+C. port 0 picks a free port (used by the tests)."""
    demo = Demo()
    key = secrets.token_urlsafe(24)
    port_ref = [port]
    httpd = ThreadingHTTPServer((HOST, port), make_handler(demo, key, port_ref))
    port_ref[0] = httpd.server_address[1]
    url = f"http://127.0.0.1:{port_ref[0]}/"
    if server_ref is not None:
        server_ref.extend([httpd, key, port_ref[0]])
    print(f"Demo running at {url}  (this computer only; press Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    if ready is not None:
        ready.set()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping the demo and deleting its temporary files.")
    finally:
        httpd.server_close()
        demo.close()
