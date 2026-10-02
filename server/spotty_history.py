#!/usr/bin/env python3
"""Persistent playback history for Spotify items observed by Spotty Server.

History is based on fresh player state that Spotty Server actually observes. It
is intentionally not a claim to be complete Spotify account history: items can
be missed while no client is causing player state to refresh.
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


def observe_inactive():
    """Reset same-item dedupe after Spotify has no active player."""
    global _last_track_id
    with _lock:
        _last_track_id = ""


def observe_player(response):
    """Record each newly observed playing item, regardless of how it was started."""
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

    # Fresh player reads can happen every few seconds while playing. Record a
    # track once as it becomes current, then wait for the observed item ID to
    # change. Pausing/resuming the same active item does not create a duplicate.
    with _lock:
        if track_id == _last_track_id:
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


_load_history()
