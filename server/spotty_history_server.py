#!/usr/bin/env python3
"""Spotty Server runtime with diagnostics, polite polling, and local play history."""

import threading
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

import spotty_history as history
import spotty_instrumented as base


HISTORY_PAGE_FILE = Path(__file__).resolve().with_name("history.html")
_original_fresh_player_response = base._fresh_player_response


def _history_page():
    try:
        return HISTORY_PAGE_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        return "<h1>Spotty playback history</h1><p>History page unavailable: {}</p>".format(exc)


def _cached_device_id():
    with base._policy_lock:
        cache = base._player_cache
        if isinstance(cache, dict):
            return cache.get("device_id") or ""
    return ""


def _history_fresh_player_response():
    status, response = _original_fresh_player_response()
    if status == 200 and isinstance(response, dict):
        if response.get("active"):
            history.observe_player(response)
        else:
            history.observe_inactive()
    return status, response


# All authoritative player refreshes now also feed the local history logger.
base._fresh_player_response = _history_fresh_player_response


class HistorySpottyHandler(base.InstrumentedSpottyHandler):
    def send_json(self, status, value):
        command = getattr(self, "_history_command", None)
        if command and 200 <= status < 300 and isinstance(value, dict) and value.get("ok"):
            path, cached_device_id = command
            history.note_command_success(path, value, cached_device_id)
            self._history_command = None
        super().send_json(status, value)

    def do_GET(self):
        parsed = self.parsed_url()
        if parsed.path == "/history":
            self.send_html(200, _history_page())
            return
        if parsed.path == "/api/history":
            query = urllib.parse.parse_qs(parsed.query)
            limit = query.get("limit", [500])[0]
            self.send_json(200, history.snapshot(limit))
            return
        super().do_GET()

    def do_POST(self):
        parsed = self.parsed_url()
        if parsed.path in {
            "/api/play",
            "/api/playpause",
            "/api/next",
            "/api/previous",
            "/api/transfer",
        }:
            self._history_command = (parsed.path, _cached_device_id())
        super().do_POST()


if __name__ == "__main__":
    print("Spotty Server (history + diagnostics)")
    print("  Listening: http://{}:{}".format(base.core.HOST, base.core.PORT))
    print("  Dashboard: http://127.0.0.1:{}/".format(base.core.PORT))
    print("  History:   http://127.0.0.1:{}/history".format(base.core.PORT))
    print("  Metrics:   http://127.0.0.1:{}/api/metrics".format(base.core.PORT))
    print("  Token file: {}".format(base.core.TOKEN_FILE))
    print()
    print("LAN clients may use this server without authentication. Keep it on a trusted network.")

    server = ThreadingHTTPServer((base.core.HOST, base.core.PORT), HistorySpottyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Spotty Server.")
    finally:
        server.server_close()
