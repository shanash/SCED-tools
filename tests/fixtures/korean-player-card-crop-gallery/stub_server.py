"""Threaded HTTP stub server for build-korean-card-crops.py Tier-2 tests.

Serves fixture atlases plus the special paths the retry-policy / size-cap /
content-type tests need (design §5.7 / §6 step 12):

  /atlas/<name>          -> a fixture atlas (PNG bytes), Content-Type image/png
  /404                   -> 404 (non-429 4xx: must NOT be retried)
  /truncated             -> half an atlas's bytes (a decode error downstream)
  /flaky5xx              -> 500 on the first N calls, then 200 + valid atlas
  /always5xx             -> 500 on EVERY call (retry-exhaustion boundary)
  /retry_after_429       -> 429 + an absurd `Retry-After: 9999` on the first
                            call, then 200 + valid atlas (ceiling-cap test)
  /sharedback/           -> extension-less trailing-slash path, served with a
                            selectable Content-Type (image/png /
                            image/jpeg;charset=binary / absent) for the
                            guess_ext Content-Type fallback test

Per-path request counts and server-side request timestamps are recorded for the
retry-policy / ceiling-cap assertions. Test scaffolding only — no production
dependency.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubState:
    """Mutable, thread-safe shared state for a running StubServer instance."""

    def __init__(self):
        self.lock = threading.Lock()
        self.request_counts: dict[str, int] = {}
        self.request_times: dict[str, list[float]] = {}
        # The valid atlas bytes the dynamic paths serve once they "succeed".
        self.atlas_bytes: bytes = b""
        # /flaky5xx: number of leading 500s before the 200.
        self.flaky_fail_count: int = 1
        # /sharedback/ Content-Type to emit (None => header omitted entirely).
        self.sharedback_content_type: str | None = "image/png"

    def record(self, path: str) -> int:
        with self.lock:
            self.request_counts[path] = self.request_counts.get(path, 0) + 1
            self.request_times.setdefault(path, []).append(time.monotonic())
            return self.request_counts[path]

    def count(self, path: str) -> int:
        with self.lock:
            return self.request_counts.get(path, 0)

    def times(self, path: str) -> list[float]:
        with self.lock:
            return list(self.request_times.get(path, []))


def _make_handler(state: StubState, atlases: dict[str, bytes]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence noisy stderr logging
            return

        def _send_bytes(self, body: bytes, content_type: str | None,
                        status: int = 200):
            self.send_response(status)
            if content_type is not None:
                self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
            path = self.path
            count = state.record(path)

            if path == "/404":
                self.send_response(404)
                self.end_headers()
                return

            if path == "/truncated":
                full = state.atlas_bytes or next(iter(atlases.values()), b"")
                half = full[: max(1, len(full) // 2)]
                # Declare the full length but send only half -> truncated body.
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(full)))
                self.end_headers()
                self.wfile.write(half)
                return

            if path == "/flaky5xx":
                if count <= state.flaky_fail_count:
                    self.send_response(500)
                    self.end_headers()
                    return
                self._send_bytes(state.atlas_bytes, "image/png")
                return

            if path == "/always5xx":
                self.send_response(500)
                self.end_headers()
                return

            if path == "/retry_after_429":
                if count == 1:
                    self.send_response(429)
                    self.send_header("Retry-After", "9999")
                    self.end_headers()
                    return
                self._send_bytes(state.atlas_bytes, "image/png")
                return

            if path == "/sharedback/":
                self._send_bytes(state.atlas_bytes,
                                 state.sharedback_content_type)
                return

            if path.startswith("/atlas/"):
                name = path[len("/atlas/"):]
                body = atlases.get(name)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self._send_bytes(body, "image/png")
                return

            self.send_response(404)
            self.end_headers()

    return Handler


class StubServer:
    """A context-manager threaded HTTP server exposing the special paths."""

    def __init__(self, atlases: dict[str, bytes] | None = None):
        self.state = StubState()
        self._atlases = atlases or {}
        if self._atlases:
            # Default the dynamic-path atlas bytes to the first fixture atlas.
            self.state.atlas_bytes = next(iter(self._atlases.values()))
        handler = _make_handler(self.state, self._atlases)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def __enter__(self) -> "StubServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)
