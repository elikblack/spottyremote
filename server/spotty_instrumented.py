#!/usr/bin/env python3
"""Run Spotty Server with metrics, caching, and polite Spotify backoff.

This wrapper keeps the production server implementation generic while adding:
- GET /api/metrics for request accounting and rate-limit diagnostics
- a small LAN status dashboard at GET /
- shared player-state caching so multiple hardware clients do not multiply polls
- adaptive idle backoff when Spotify reports no active playback session
- global Spotify 429 cooldown handling using Retry-After when available
"""

import json
import math
import threading
import time
import urllib.parse
from collections import Counter, deque
from http.server import ThreadingHTTPServer
from pathlib import Path

import spotty_server as core


# Player polling policy. Hardware may continue polling the LAN server frequently;
# these values only control how often the server refreshes authoritative state
# from Spotify.
PLAYER_TTL_PLAYING = 5.0
PLAYER_TTL_PAUSED = 15.0
PLAYER_IDLE_TTLS = (15.0, 30.0, 60.0, 120.0, 300.0)
DEFAULT_429_COOLDOWN = 60.0
DEFAULT_QUOTA_COOLDOWN = 300.0

_metrics_lock = threading.Lock()
_policy_lock = threading.Lock()
_player_refresh_lock = threading.Lock()

_recent = deque()
_totals = Counter()
_status_totals = Counter()
_started_at = time.time()
_started_monotonic = time.monotonic()
_original_spotify_request = core.spotify_request
STATUS_FILE = Path(__file__).resolve().with_name("status.html")

_player_cache = None
_player_cache_at = 0.0
_player_next_refresh_at = 0.0
_player_idle_streak = 0
_player_cache_hits = 0

_spotify_cooldown_until = 0.0
_spotify_cooldown_reason = ""
_spotify_suppressed_requests = 0
_spotify_first_429_at = 0.0
_spotify_last_429_at = 0.0
_spotify_last_429_reason = ""
_spotify_last_retry_after = 0


def _trim_recent(now):
    cutoff = now - 60.0
    while _recent and _recent[0][0] < cutoff:
        _recent.popleft()


def _metric_key(method, path):
    """Collapse variable Spotify IDs so the metrics table stays readable."""
    parts = [part for part in path.split("/") if part]
    normalized = path
    if len(parts) == 2 and parts[0] in ("tracks", "episodes"):
        normalized = "/{}/:id".format(parts[0])
    elif len(parts) >= 3 and parts[0] == "playlists" and parts[2] == "items":
        normalized = "/playlists/:id/items"
    return "{} {}".format(method, normalized)


def _header_value(headers, name):
    wanted = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() == wanted:
            return value
    return None


def _parse_retry_after(headers):
    raw = _header_value(headers, "Retry-After")
    try:
        return max(0, int(float(raw)))
    except (TypeError, ValueError):
        return 0


def _extract_rate_limit_reason(raw):
    parsed = core.decode_json(raw)
    if not isinstance(parsed, dict):
        return ""
    error = parsed.get("error")
    if isinstance(error, dict):
        reason = error.get("reason")
        if isinstance(reason, str):
            return reason
    reason = parsed.get("reason")
    return reason if isinstance(reason, str) else ""


def _note_rate_limit(raw, headers):
    global _spotify_cooldown_until
    global _spotify_cooldown_reason
    global _spotify_first_429_at
    global _spotify_last_429_at
    global _spotify_last_429_reason
    global _spotify_last_retry_after

    now_mono = time.monotonic()
    now_wall = time.time()
    reason = _extract_rate_limit_reason(raw)
    retry_after = _parse_retry_after(headers)
    if retry_after <= 0:
        retry_after = int(
            DEFAULT_QUOTA_COOLDOWN if reason == "QUOTA_EXCEEDED" else DEFAULT_429_COOLDOWN
        )

    with _policy_lock:
        # Add a one-second cushion so we do not land exactly on Spotify's edge.
        _spotify_cooldown_until = max(
            _spotify_cooldown_until,
            now_mono + retry_after + 1.0,
        )
        _spotify_cooldown_reason = reason or "rate_limited"
        if not _spotify_first_429_at:
            _spotify_first_429_at = now_wall
        _spotify_last_429_at = now_wall
        _spotify_last_429_reason = reason or "rate_limited"
        _spotify_last_retry_after = retry_after


def _cooldown_snapshot(now_mono=None):
    now_mono = time.monotonic() if now_mono is None else now_mono
    with _policy_lock:
        remaining = max(0.0, _spotify_cooldown_until - now_mono)
        return remaining, _spotify_cooldown_reason


def _synthetic_rate_limit_response(remaining, reason):
    retry_after = max(1, int(math.ceil(remaining)))
    payload = {
        "error": {
            "status": 429,
            "message": "Spotty Server is honoring Spotify rate-limit cooldown",
            "reason": reason or "rate_limited",
        }
    }
    return 429, json.dumps(payload, separators=(",", ":")).encode("utf-8"), {
        "Retry-After": str(retry_after)
    }


def instrumented_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
    global _spotify_suppressed_requests

    now_mono = time.monotonic()
    remaining, reason = _cooldown_snapshot(now_mono)
    if remaining > 0:
        with _policy_lock:
            _spotify_suppressed_requests += 1
        return _synthetic_rate_limit_response(remaining, reason)

    key = _metric_key(method, path)
    status = "exception"
    result = None

    try:
        result = _original_spotify_request(path, method, query, body, retry_auth)
        status = str(result[0])
        if result[0] == 429:
            _note_rate_limit(result[1], result[2])
        return result
    finally:
        finished = time.time()
        with _metrics_lock:
            _totals[key] += 1
            _status_totals[status] += 1
            _recent.append((finished, key, status))
            _trim_recent(finished)


core.spotify_request = instrumented_spotify_request


def _idle_ttl(streak):
    index = min(max(1, streak), len(PLAYER_IDLE_TTLS)) - 1
    return PLAYER_IDLE_TTLS[index]


def _cache_metadata(now_mono=None):
    now_mono = time.monotonic() if now_mono is None else now_mono
    with _policy_lock:
        cache = _player_cache
        cache_age = max(0.0, now_mono - _player_cache_at) if cache is not None else 0.0
        refresh_in = max(0.0, _player_next_refresh_at - now_mono) if cache is not None else 0.0
        if cache is None:
            state = "unknown"
        elif not cache.get("active"):
            state = "inactive"
        elif cache.get("is_playing"):
            state = "playing"
        else:
            state = "paused"
        return {
            "state": state,
            "age_seconds": int(cache_age),
            "refresh_in_seconds": int(math.ceil(refresh_in)),
            "idle_streak": _player_idle_streak,
            "hits_since_start": _player_cache_hits,
        }


def _cached_player_response(now_mono=None, allow_expired=False):
    global _player_cache_hits

    now_mono = time.monotonic() if now_mono is None else now_mono
    with _policy_lock:
        if _player_cache is None:
            return None
        if not allow_expired and now_mono >= _player_next_refresh_at:
            return None
        _player_cache_hits += 1
        response = dict(_player_cache)
        age = max(0.0, now_mono - _player_cache_at)
        refresh_in = max(0.0, _player_next_refresh_at - now_mono)

    response["spotty_cache"] = {
        "cached": True,
        "age_seconds": int(age),
        "refresh_in_seconds": int(math.ceil(refresh_in)),
    }
    return response


def _store_player_cache(response, ttl, inactive_streak):
    global _player_cache
    global _player_cache_at
    global _player_next_refresh_at
    global _player_idle_streak

    now_mono = time.monotonic()
    with _policy_lock:
        _player_cache = dict(response)
        _player_cache_at = now_mono
        _player_next_refresh_at = now_mono + max(0.0, ttl)
        _player_idle_streak = inactive_streak


def _mark_player_cache_dirty(reset_idle=False):
    global _player_next_refresh_at
    global _player_idle_streak
    with _policy_lock:
        _player_next_refresh_at = 0.0
        if reset_idle:
            _player_idle_streak = 0


def _build_player_response(data):
    summary = core.player_summary(data)
    response = {"ok": True, "active": True}
    response.update(summary)
    response["player"] = data
    return response


def _fresh_player_response():
    """Fetch authoritative player state once and update the shared cache."""
    status, raw, _headers = core.spotify_request("/me/player")

    if status == 204:
        with _policy_lock:
            streak = _player_idle_streak + 1
        ttl = _idle_ttl(streak)
        response = {"ok": True, "active": False}
        _store_player_cache(response, ttl, streak)
        response = dict(response)
        response["spotty_cache"] = {
            "cached": False,
            "refresh_in_seconds": int(ttl),
            "idle_streak": streak,
        }
        return 200, response

    if status == 200:
        player = core.decode_json(raw) or {}
        response = _build_player_response(player)
        ttl = PLAYER_TTL_PLAYING if response.get("is_playing") else PLAYER_TTL_PAUSED
        _store_player_cache(response, ttl, 0)
        response = dict(response)
        response["spotty_cache"] = {
            "cached": False,
            "refresh_in_seconds": int(ttl),
            "idle_streak": 0,
        }
        return 200, response

    if status == 429:
        cached = _cached_player_response(allow_expired=True)
        if cached is not None:
            remaining, reason = _cooldown_snapshot()
            cached["spotty_cache"]["stale"] = True
            cached["spotty_cache"]["spotify_rate_limited"] = True
            cached["spotty_cache"]["cooldown_seconds"] = int(math.ceil(remaining))
            cached["spotty_cache"]["cooldown_reason"] = reason
            return 200, cached

    _status, data = core.api_result(status, raw)
    return 502, data


def metrics_snapshot():
    now_wall = time.time()
    now_mono = time.monotonic()
    with _metrics_lock:
        _trim_recent(now_wall)
        recent_by_endpoint = Counter(item[1] for item in _recent)
        recent_by_status = Counter(item[2] for item in _recent)
        totals = dict(sorted(_totals.items()))
        status_totals = dict(sorted(_status_totals.items()))
        recent_count = len(_recent)
        total_count = sum(_totals.values())

    with _policy_lock:
        cooldown_seconds = max(0, int(math.ceil(_spotify_cooldown_until - now_mono)))
        policy = {
            "spotify_cooldown_seconds": cooldown_seconds,
            "spotify_cooldown_reason": _spotify_cooldown_reason if cooldown_seconds else "",
            "spotify_requests_suppressed_since_start": _spotify_suppressed_requests,
            "first_429_at": int(_spotify_first_429_at) if _spotify_first_429_at else None,
            "last_429_at": int(_spotify_last_429_at) if _spotify_last_429_at else None,
            "last_429_reason": _spotify_last_429_reason,
            "last_retry_after_seconds": _spotify_last_retry_after,
        }

    return {
        "ok": True,
        "service": "spotty",
        "uptime_seconds": int(now_mono - _started_monotonic),
        "spotify_requests_last_60s": recent_count,
        "last_60s_by_endpoint": dict(sorted(recent_by_endpoint.items())),
        "last_60s_by_status": dict(sorted(recent_by_status.items())),
        "total_requests_since_start": total_count,
        "total_by_endpoint": totals,
        "total_by_status": status_totals,
        "player_cache": _cache_metadata(now_mono),
        "traffic_policy": policy,
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
        if parsed.path == "/api/player":
            query = urllib.parse.parse_qs(parsed.query)
            force = query.get("refresh", ["0"])[0].strip().lower() in ("1", "true", "yes")

            remaining, reason = _cooldown_snapshot()
            if remaining > 0:
                cached = _cached_player_response(allow_expired=True)
                if cached is not None:
                    cached["spotty_cache"]["stale"] = True
                    cached["spotty_cache"]["spotify_rate_limited"] = True
                    cached["spotty_cache"]["cooldown_seconds"] = int(math.ceil(remaining))
                    cached["spotty_cache"]["cooldown_reason"] = reason
                    self.send_json(200, cached)
                else:
                    self.send_json(503, {
                        "ok": False,
                        "error": "Spotify rate-limit cooldown is active",
                        "retry_after_seconds": int(math.ceil(remaining)),
                    })
                return

            if not force:
                cached = _cached_player_response()
                if cached is not None:
                    self.send_json(200, cached)
                    return

            # Only one thread is allowed to refresh Spotify player state at a
            # time. A second Spotty arriving simultaneously will reuse the
            # result instead of creating a duplicate upstream request.
            with _player_refresh_lock:
                if not force:
                    cached = _cached_player_response()
                    if cached is not None:
                        self.send_json(200, cached)
                        return
                try:
                    status, response = _fresh_player_response()
                    self.send_json(status, response)
                except Exception as exc:
                    self.send_json(503, {"ok": False, "error": str(exc)})
            return

        super().do_GET()

    def do_POST(self):
        parsed = self.parsed_url()
        playback_paths = {
            "/api/play",
            "/api/pause",
            "/api/playpause",
            "/api/next",
            "/api/previous",
            "/api/volume",
            "/api/transfer",
        }
        if parsed.path in playback_paths:
            # Keep the last value available as a stale fallback, but force the
            # next player read to re-check Spotify after an explicit command.
            _mark_player_cache_dirty(reset_idle=True)

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
