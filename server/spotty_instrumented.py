#!/usr/bin/env python3
"""Run Spotty Server with lightweight Spotify Web API request accounting.

This keeps the production server implementation untouched while exposing
GET /api/metrics for diagnosing Spotify request volume and 429s.
"""

import threading
import time
from collections import Counter, deque
from http.server import ThreadingHTTPServer

import spotty_server as core


_metrics_lock = threading.Lock()
_recent = deque()
_totals = Counter()
_status_totals = Counter()
_started_at = time.time()
_original_spotify_request = core.spotify_request


def _trim_recent(now):
    cutoff = now - 60.0
    while _recent and _recent[0][0] < cutoff:
        _recent.popleft()


def instrumented_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
    key = "{} {}".format(method, path)
    started = time.time()
    status = "exception"

    try:
        result = _original_spotify_request(path, method, query, body, retry_auth)
        status = str(result[0])
        return result
    finally:
        finished = time.time()
        with _metrics_lock:
            _totals[key] += 1
            _status_totals[status] += 1
            _recent.append((finished, key, status))
            _trim_recent(finished)


core.spotify_request = instrumented_spotify_request


def metrics_snapshot():
    now = time.time()
    with _metrics_lock:
        _trim_recent(now)
        recent_by_endpoint = Counter(item[1] for item in _recent)
        recent_by_status = Counter(item[2] for item in _recent)
        return {
            "ok": True,
            "service": "spotty",
            "uptime_seconds": int(now - _started_at),
            "spotify_requests_last_60s": len(_recent),
            "last_60s_by_endpoint": dict(sorted(recent_by_endpoint.items())),
            "last_60s_by_status": dict(sorted(recent_by_status.items())),
            "total_requests_since_start": sum(_totals.values()),
            "total_by_endpoint": dict(sorted(_totals.items())),
            "total_by_status": dict(sorted(_status_totals.items())),
        }


class InstrumentedSpottyHandler(core.SpottyHandler):
    def do_GET(self):
        parsed = self.parsed_url()
        if parsed.path == "/api/metrics":
            self.send_json(200, metrics_snapshot())
            return
        super().do_GET()


if __name__ == "__main__":
    print("Spotty Server (instrumented)")
    print("  Listening: http://{}:{}".format(core.HOST, core.PORT))
    print("  Metrics:   http://127.0.0.1:{}/api/metrics".format(core.PORT))
    print("  Token file: {}".format(core.TOKEN_FILE))
    print()
    print("LAN clients may use this server without authentication. Keep it on a trusted network.")

    server = ThreadingHTTPServer((core.HOST, core.PORT), InstrumentedSpottyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Spotty Server.")
    finally:
        server.server_close()
