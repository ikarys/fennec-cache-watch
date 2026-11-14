"""Usage tap: transparent proxy in front of the model that records per-call usage and latency.

Sits between the perf harness proxy and cache-hunter:
  fennec -> harness proxy -> tap -> cache-hunter -> vLLM
Appends one JSON line per chat/completions call to TAP_OUT (usage, latency, message count).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("TAP_UPSTREAM", "http://127.0.0.1:8787/v1")
OUT = os.environ.get("TAP_OUT", "perf-tap.jsonl")
_lock = threading.Lock()


def _record(entry: dict) -> None:
    with _lock:
        with open(OUT, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args: object) -> None:
        pass

    def _forward(self, method: str, body: bytes | None) -> None:
        url = UPSTREAM.rstrip("/") + self.path.removeprefix("/v1")
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        started = time.perf_counter()
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except Exception as exc:  # noqa: BLE001 - report one clear message
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode())
            return

        self.send_response(resp.status)
        content_type = resp.headers.get("Content-Type", "application/octet-stream")
        self.send_header("Content-Type", content_type)
        self.end_headers()
        raw = resp.read()
        self.wfile.write(raw)

        if method == "POST" and resp.status == 200:
            usage = None
            if "text/event-stream" in content_type:
                for line in raw.decode("utf-8", errors="replace").splitlines():
                    if line.startswith("data: ") and "usage" in line:
                        try:
                            payload = json.loads(line[6:])
                        except json.JSONDecodeError:
                            continue
                        if isinstance(payload.get("usage"), dict):
                            usage = payload["usage"]
            else:
                try:
                    usage = json.loads(raw).get("usage")
                except json.JSONDecodeError:
                    usage = None
            if usage is not None:
                msg_count = None
                if body:
                    try:
                        msg_count = len(json.loads(body).get("messages", []))
                    except json.JSONDecodeError:
                        msg_count = None
                _record(
                    {
                        "t": time.time(),
                        "total_ms": (time.perf_counter() - started) * 1000.0,
                        "prompt_tokens": usage.get("prompt_tokens"),
                        "completion_tokens": usage.get("completion_tokens"),
                        "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
                        "msg_count": msg_count,
                    }
                )

    def do_GET(self) -> None:
        self._forward("GET", None)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        self._forward("POST", self.rfile.read(length) if length else b"")


def main() -> None:
    port = int(os.environ.get("TAP_PORT", "8899"))
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    server.daemon_threads = True
    print(f"usage tap on :{port} -> {UPSTREAM}, logging to {OUT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
