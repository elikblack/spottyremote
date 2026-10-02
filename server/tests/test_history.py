import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import spotty_history as history


class PlaybackHistoryTests(unittest.TestCase):
    def setUp(self):
        self.original_history_file = history.HISTORY_FILE
        self.original_entries = list(history._entries)
        self.original_total_entries = history._total_entries
        self.original_last_track_id = history._last_track_id
        self.temp_dir = tempfile.TemporaryDirectory()

        history.HISTORY_FILE = Path(self.temp_dir.name) / "history.jsonl"
        history._entries = deque(maxlen=history.MEMORY_ENTRIES)
        history._total_entries = 0
        history._last_track_id = ""

    def tearDown(self):
        history.HISTORY_FILE = self.original_history_file
        history._entries = deque(self.original_entries, maxlen=history.MEMORY_ENTRIES)
        history._total_entries = self.original_total_entries
        history._last_track_id = self.original_last_track_id
        self.temp_dir.cleanup()

    def player(self, track_id, *, playing=True, active=True):
        return {
            "ok": True,
            "active": active,
            "is_playing": playing,
            "track_id": track_id,
            "item_type": "track",
            "track_name": "Track " + track_id,
            "artist_name": "Artist",
            "device_id": "echo-network",
            "device_name": "Everywhere",
            "player": {
                "item": {
                    "id": track_id,
                    "type": "track",
                    "name": "Track " + track_id,
                    "album": {"name": "Album"},
                },
                "context": {"uri": "spotify:playlist:test"},
            },
        }

    def test_first_observed_playing_item_is_logged_without_spotty_command(self):
        history.observe_player(self.player("track-1"))

        snapshot = history.snapshot(10)
        self.assertEqual(snapshot["total"], 1)
        self.assertEqual(snapshot["entries"][0]["track_id"], "track-1")
        self.assertTrue(history.HISTORY_FILE.exists())

    def test_repeated_refresh_of_same_playing_item_is_deduplicated(self):
        history.observe_player(self.player("track-1"))
        history.observe_player(self.player("track-1"))
        history.observe_player(self.player("track-1"))

        self.assertEqual(history.snapshot(10)["total"], 1)

    def test_new_observed_playing_item_is_appended(self):
        history.observe_player(self.player("track-1"))
        history.observe_player(self.player("track-2"))

        snapshot = history.snapshot(10)
        self.assertEqual(snapshot["total"], 2)
        self.assertEqual(
            [entry["track_id"] for entry in snapshot["entries"]],
            ["track-2", "track-1"],
        )

    def test_pause_resume_same_active_item_does_not_duplicate(self):
        history.observe_player(self.player("track-1"))
        history.observe_player(self.player("track-1", playing=False))
        history.observe_player(self.player("track-1"))

        self.assertEqual(history.snapshot(10)["total"], 1)


if __name__ == "__main__":
    unittest.main()
