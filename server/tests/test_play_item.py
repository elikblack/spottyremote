import sys
import unittest
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import spotty_server as server


class PlayItemTests(unittest.TestCase):
    def setUp(self):
        self.original_spotify_request = server.spotify_request

    def tearDown(self):
        server.spotify_request = self.original_spotify_request

    def make_handler(self):
        handler = object.__new__(server.SpottyHandler)
        handler.sent = []
        handler.send_json = lambda status, value: handler.sent.append((status, value))
        return handler

    def test_plain_play_keeps_existing_resume_behavior(self):
        handler = self.make_handler()
        calls = []
        handler.proxy_empty = lambda path, method, query=None: calls.append(
            (path, method, query)
        )

        handler.handle_play({"device_id": ["speaker-1"]})

        self.assertEqual(
            calls,
            [("/me/player/play", "PUT", {"device_id": "speaker-1"})],
        )
        self.assertEqual(handler.sent, [])

    def test_item_play_targets_device_with_uri_body(self):
        handler = self.make_handler()
        calls = []

        def fake_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
            calls.append((path, method, query, body))
            return 204, b"", {}

        server.spotify_request = fake_spotify_request

        handler.handle_play({
            "device_id": ["speaker-1"],
            "item_type": ["track"],
            "item_id": ["abc123"],
        })

        self.assertEqual(
            calls,
            [(
                "/me/player/play",
                "PUT",
                {"device_id": "speaker-1"},
                {"uris": ["spotify:track:abc123"]},
            )],
        )
        self.assertEqual(handler.sent[0][0], 200)
        self.assertTrue(handler.sent[0][1]["ok"])
        self.assertEqual(handler.sent[0][1]["item_id"], "abc123")

    def test_item_play_rejects_unknown_item_type(self):
        handler = self.make_handler()

        handler.handle_play({
            "item_type": ["album"],
            "item_id": ["abc123"],
        })

        self.assertEqual(handler.sent[0][0], 400)
        self.assertFalse(handler.sent[0][1]["ok"])


if __name__ == "__main__":
    unittest.main()
