#!/usr/bin/env python3
"""Run Spotty Server with lightweight Spotify Web API request accounting.

This keeps the production server implementation untouched while exposing
GET /api/metrics for diagnosing Spotify request volume and 429s, plus a small
LAN status dashboard at GET /.
"""

import threading
import time
import urllib.parse
from collections import Counter, deque
from http.server import ThreadingHTTPServer
from pathlib import Path

import spotty_server as core


_metrics_lock = threading.Lock()
_recent = deque()
_totals = Counter()
_status_totals = Counter()
_started_at = time.time()
_original_spotify_request = core.spotify_request
STATUS_FILE = Path(__file__).resolve().with_name("status.html")


def _trim_recent(now):
    cutoff = now - 60.0
    while _recent and _recent[0][0] < cutoff:
        _recent.popleft()


def instrumented_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
    key = "{} {}".format(method, path)
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


def status_page():
    try:
        return STATUS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return "<h1>Spotty Server</h1><p>Dashboard unavailable: {}</p>".format(exc)


class InstrumentedSpottyHandler(core.SpottyHandler):
    def do_GET(self):
        parsed = self.parsed_url()
        if parsed.path == "/":
            self.send_html(200, status_page())
            return
        if parsed.path == "/api/metrics":
            self.send_json(200, metrics_snapshot())
            return
        super().do_GET()

    def do_POST(self):
        parsed = self.parsed_url()

        # Spotify's Transfer Playback endpoint can return 404 when there is no
        # current playback session to transfer. If the caller explicitly asked
        # to transfer-and-play, fall back to Start/Resume Playback targeted at
        # that same Spotify Connect device. This preserves the generic server
        # contract without putting any preferred-device policy here.
        if parsed.path == "/api/transfer":
            query = urllib.parse.parse_qs(parsed.query)
            device_id = query.get("device_id", [None])[0]
            raw_play = query.get("play", [None])[0]
            wants_play = raw_play is not None and raw_play.strip().lower() in ("true", "1")

            if device_id and wants_play:
                try:
                    status, raw, _headers = core.spotify_request(
                        "/me/player",
                        "PUT",
                        body={"device_ids": [device_id], "play": True},
                    )
                    if 200 <= status < 300:
                        self.send_json(200, {
                            "ok": True,
                            "device_id": device_id,
                            "play": True,
                            "method": "transfer",
                        })
                        return

                    if status == 404:
                        play_status, play_raw, _headers = core.spotify_request(
                            "/me/player/play",
                            "PUT",
                            query={"device_id": device_id},
                        )
                        if 200 <= play_status < 300:
                            self.send_json(200, {
                                "ok": True,
                                "device_id": device_id,
                                "play": True,
                                "method": "targeted_play",
                                "transfer_status": 404,
                            })
                            return
                        _status, data = core.api_result(play_status, play_raw)
                        self.send_json(502, data)
                        return

                    _status, data = core.api_result(status, raw)
                    self.send_json(502, data)
                    return
                except Exception as exc:
                    self.send_json(503, {"ok": False, "error": str(exc)})
                    return

        super().do_POST()


if __name__ == "__main__":
    print("Spotty Server (instrumented)")
    print("  Listening: http://{}:{}".format(core.HOST, core.PORT))
    print("  Dashboard: http://127.0.0.1:{}/".format(core.PORT))
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
