import sys
import unittest
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import spotty_history_server as server


class ForcedPlayerResponseTests(unittest.TestCase):
    def setUp(self):
        self.original_cooldown_snapshot = server.base._cooldown_snapshot
        self.original_fresh_player_response = server.base._fresh_player_response

    def tearDown(self):
        server.base._cooldown_snapshot = self.original_cooldown_snapshot
        server.base._fresh_player_response = self.original_fresh_player_response

    def test_active_cooldown_never_calls_fresh_player(self):
        called = []
        server.base._cooldown_snapshot = lambda: (12.2, "QUOTA_EXCEEDED")
        server.base._fresh_player_response = lambda: called.append(True)

        status, response = server._forced_player_response()

        self.assertEqual(status, 503)
        self.assertFalse(response["ok"])
        self.assertEqual(response["spotify_status"], 429)
        self.assertEqual(response["retry_after_seconds"], 13)
        self.assertEqual(response["cooldown_reason"], "QUOTA_EXCEEDED")
        self.assertEqual(called, [])

    def test_fresh_success_is_returned_unchanged(self):
        expected = {
            "ok": True,
            "active": True,
            "device_name": "Kitchen",
            "spotty_cache": {"cached": False},
        }
        server.base._cooldown_snapshot = lambda: (0.0, "")
        server.base._fresh_player_response = lambda: (200, expected)

        status, response = server._forced_player_response()

        self.assertEqual(status, 200)
        self.assertIs(response, expected)

    def test_new_rate_limit_stale_fallback_becomes_failure(self):
        server.base._cooldown_snapshot = lambda: (0.0, "")
        server.base._fresh_player_response = lambda: (
            200,
            {
                "ok": True,
                "active": True,
                "spotty_cache": {
                    "cached": True,
                    "stale": True,
                    "spotify_rate_limited": True,
                    "cooldown_seconds": 44,
                    "cooldown_reason": "rate_limited",
                },
            },
        )

        status, response = server._forced_player_response()

        self.assertEqual(status, 503)
        self.assertFalse(response["ok"])
        self.assertEqual(response["spotify_status"], 429)
        self.assertEqual(response["retry_after_seconds"], 44)

    def test_non_stale_upstream_failure_remains_failure(self):
        expected = {"ok": False, "error": "upstream failed", "spotify_status": 500}
        server.base._cooldown_snapshot = lambda: (0.0, "")
        server.base._fresh_player_response = lambda: (502, expected)

        status, response = server._forced_player_response()

        self.assertEqual(status, 502)
        self.assertIs(response, expected)


if __name__ == "__main__":
    unittest.main()
