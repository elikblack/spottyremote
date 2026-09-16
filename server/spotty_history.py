#!/usr/bin/env python3
"""Persistent playback history for Spotty-managed playback sessions.

The history is intentionally based on state observed by Spotty Server, not on
Spotify's account listening history. A session is armed by successful playback
commands that came through Spotty, then tracks are recorded as the server sees
them play on that managed device.
"""

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


HISTORY_FILE = Path(__file__).resolve().with_name(".spotty_play_history.jsonl")
MEMORY_ENTRIES = 5000

_lock = threading.Lock()
_entries = deque(maxlen=MEMORY_ENTRIES)
_total_entries = 0

_session_active = False
_session_pending = False
_session_device_id = ""
_last_track_id = ""


def _load_history():
    global _total_entries
    if not HISTORY_FILE.exists():
        return
    try:
        with HISTORY_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    _entries.append(entry)
                    _total_entries += 1
    except OSError:
        pass


def _utc_iso(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _append_entry(entry):
    global _total_entries
    payload = json.dumps(entry, separators=(",", ":"), ensure_ascii=False)
    with _lock:
        try:
            with HISTORY_FILE.open("a", encoding="utf-8") as handle:
                handle.write(payload + "\n")
        except OSError:
            # History must never be allowed to break playback control. Keep the
            # event in memory even if the disk write failed.
            pass
        _entries.append(entry)
        _total_entries += 1


def _arm(device_id=""):
    global _session_pending
    global _session_device_id
    _session_pending = True
    if device_id:
        _session_device_id = device_id


def note_command_success(path, response=None, cached_device_id=""):
    """Update history-session ownership after a successful Spotty command."""
    global _session_device_id

    response = response if isinstance(response, dict) else {}
    with _lock:
        if path == "/api/play":
            _arm(cached_device_id)
        elif path == "/api/playpause" and response.get("action") == "play":
            _arm(cached_device_id)
        elif path in ("/api/next", "/api/previous"):
            _arm(cached_device_id)
        elif path == "/api/transfer":
            target = response.get("device_id") or ""
            if target and _session_active:
                _session_device_id = target
            if response.get("play") is True:
                _arm(target or cached_device_id)


def observe_inactive():
    """End a Spotty-owned playback session when Spotify has no active player."""
    global _session_active
    global _session_pending
    global _session_device_id
    global _last_track_id
    with _lock:
        _session_active = False
        _session_pending = False
        _session_device_id = ""
        _last_track_id = ""


def observe_player(response):
    """Record a newly observed playing item when Spotty owns the session."""
    global _session_active
    global _session_pending
    global _session_device_id
    global _last_track_id

    if not isinstance(response, dict) or not response.get("active"):
        observe_inactive()
        return

    if not response.get("is_playing"):
        return

    device_id = response.get("device_id") or ""
    track_id = response.get("track_id") or ""
    if not track_id:
        return

    with _lock:
        if not _session_active and not _session_pending:
            return

        if _session_device_id and device_id and device_id != _session_device_id:
            # During a just-requested transfer Spotify may briefly still report
            # the old device. Keep the pending lease alive until it settles.
            if _session_pending:
                return
            _session_active = False
            _session_pending = False
            _session_device_id = ""
            _last_track_id = ""
            return

        if _session_pending:
            _session_active = True
            _session_pending = False
            if not _session_device_id:
                _session_device_id = device_id

        if not _session_active or track_id == _last_track_id:
            return
        _last_track_id = track_id

    player = response.get("player") if isinstance(response.get("player"), dict) else {}
    item = player.get("item") if isinstance(player.get("item"), dict) else {}
    album = item.get("album") if isinstance(item.get("album"), dict) else {}
    context = player.get("context") if isinstance(player.get("context"), dict) else {}
    now = time.time()

    entry = {
        "played_at": _utc_iso(now),
        "played_at_unix": int(now),
        "item_type": response.get("item_type") or item.get("type") or "track",
        "track_id": track_id,
        "track_name": response.get("track_name") or item.get("name") or "",
        "artist_name": response.get("artist_name") or "",
        "album_name": album.get("name") or "",
        "device_id": device_id,
        "device_name": response.get("device_name") or "",
        "context_uri": context.get("uri") or "",
    }
    _append_entry(entry)


def snapshot(limit=500):
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 500
    limit = max(1, min(MEMORY_ENTRIES, limit))
    with _lock:
        selected = list(_entries)[-limit:]
        total = _total_entries
    selected.reverse()
    return {
        "ok": True,
        "total": total,
        "returned": len(selected),
        "entries": selected,
    }


def session_snapshot():
    with _lock:
        return {
            "active": _session_active,
            "pending": _session_pending,
            "device_id": _session_device_id,
        }


_load_history()
