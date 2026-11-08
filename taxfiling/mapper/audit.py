"""The audit log: one JSON line per model request, holding the exact bytes that were sent.

For a cloud or local model the request body is captured inside the HTTP client after
the boundary check passes, so the log shows what actually went over the wire, not
what the program meant to send. HTTP headers (where the API key travels) are never
logged. A blocked request is logged with the reason only, never with its body.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path


class AuditLog:
    def __init__(self, path: str | Path | None):
        self.path = Path(path) if path else None
        self.entries: list[dict] = []

    def _write(self, entry: dict) -> None:
        entry = {"time": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), **entry}
        self.entries.append(entry)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def sent(self, provider: str, url: str, body: bytes) -> None:
        self._write({
            "event": "request_sent", "provider": provider, "url": url,
            "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body),
            "body": body.decode("utf-8"),
        })

    def received(self, provider: str, text: str) -> None:
        self._write({"event": "response_received", "provider": provider, "text": text})

    def blocked(self, provider: str, url: str, reason: str) -> None:
        self._write({"event": "request_blocked", "provider": provider, "url": url, "reason": reason})

    def offline(self, provider: str, payload_sha256: str, note: str) -> None:
        self._write({"event": "no_request", "provider": provider, "payload_sha256": payload_sha256, "note": note})
