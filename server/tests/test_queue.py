import json
import sys
import unittest
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import spotty_server as server


class QueueEndpointTests(unittest.TestCase):
    def setUp(self):
        self.original_spotify_request = server.spotify_request

    def tearDown(self):
        server.spotify_request = self.original_spotify_request

    def make_handler(self):
        handler = object.__new__(server.SpottyHandler)
        handler.sent = []
        handler.send_json = lambda status, value: handler.sent.append((status, value))
        return handler

    def test_add_queue_item_targets_device(self):
        handler = self.make_handler()
        calls = []

        def fake_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
            calls.append((path, method, query, body))
            return 204, b"", {}

        server.spotify_request = fake_spotify_request

        handler.handle_queue_add({
            "device_id": ["speaker-1"],
            "item_type": ["track"],
            "item_id": ["4iV5W9uYEdYUVa79Axb7Rh"],
        })

        self.assertEqual(
            calls,
            [(
                "/me/player/queue",
                "POST",
                {
                    "uri": "spotify:track:4iV5W9uYEdYUVa79Axb7Rh",
                    "device_id": "speaker-1",
                },
                None,
            )],
        )
        self.assertEqual(handler.sent[0][0], 200)
        self.assertTrue(handler.sent[0][1]["ok"])

    def test_queue_snapshot_returns_compact_current_and_next_ids(self):
        handler = self.make_handler()

        def fake_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
            self.assertEqual(path, "/me/player/queue")
            payload = {
                "currently_playing": {"id": "current-id", "type": "track"},
                "queue": [
                    {"id": "next-id", "type": "track"},
                    {"id": "later-id", "type": "track"},
                ],
            }
            return 200, json.dumps(payload).encode("utf-8"), {}

        server.spotify_request = fake_spotify_request

        handler.handle_queue()

        self.assertEqual(handler.sent[0][0], 200)
        self.assertEqual(
            handler.sent[0][1],
            {
                "ok": True,
                "current_item_id": "current-id",
                "current_item_type": "track",
                "next_item_id": "next-id",
                "next_item_type": "track",
                "queue_count": 2,
            },
        )

    def test_next_targets_device_when_requested(self):
        handler = self.make_handler()
        calls = []

        def fake_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
            calls.append((path, method, query, body))
            return 204, b"", {}

        server.spotify_request = fake_spotify_request

        handler.handle_next({"device_id": ["speaker-1"]})

        self.assertEqual(
            calls,
            [("/me/player/next", "POST", {"device_id": "speaker-1"}, None)],
        )
        self.assertEqual(handler.sent[0][0], 200)


if __name__ == "__main__":
    unittest.main()
