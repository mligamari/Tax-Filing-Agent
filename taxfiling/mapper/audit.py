"""The audit log: one JSON line per model request, holding exactly what was sent.

For a cloud or local model the request is captured inside the HTTP client after the
boundary check passes, just before it is handed to the network: its URL, every header
and the exact bytes of its body. So the log shows what actually went over the wire, not
what the program meant to send. (If the network then fails, the entry has no response
after it.) The one value left out is the credential (the API key), which is replaced by
a placeholder. A blocked request is logged with its reason and the host only, never
with its path, headers or body.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path

from .boundary import CREDENTIAL_HEADERS

CREDENTIAL_PLACEHOLDER = "(credential, not logged)"


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

    def sent(self, provider: str, url: str, headers: dict[str, str], body: bytes) -> None:
        self._write({
            "event": "request_sent", "provider": provider, "url": url,
            "headers": {name: CREDENTIAL_PLACEHOLDER if name.lower() in CREDENTIAL_HEADERS else value
                        for name, value in headers.items()},
            "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body),
            "body": body.decode("utf-8"),
        })

    def received(self, provider: str, text: str) -> None:
        self._write({"event": "response_received", "provider": provider, "text": text})

    def blocked(self, provider: str, url: str, reason: str) -> None:
        self._write({"event": "request_blocked", "provider": provider, "url": url, "reason": reason})

    def offline(self, provider: str, payload_sha256: str, note: str) -> None:
        self._write({"event": "no_request", "provider": provider, "payload_sha256": payload_sha256, "note": note})
