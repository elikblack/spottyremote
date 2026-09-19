import sys
import unittest
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import spotty_server as server


class RepeatModeTests(unittest.TestCase):
    def setUp(self):
        self.original_spotify_request = server.spotify_request

    def tearDown(self):
        server.spotify_request = self.original_spotify_request

    def make_handler(self):
        handler = object.__new__(server.SpottyHandler)
        handler.sent = []
        handler.send_json = lambda status, value: handler.sent.append((status, value))
        return handler

    def test_repeat_off_targets_device(self):
        handler = self.make_handler()
        calls = []

        def fake_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
            calls.append((path, method, query, body))
            return 204, b"", {}

        server.spotify_request = fake_spotify_request

        handler.handle_repeat({
            "state": ["off"],
            "device_id": ["speaker-1"],
        })

        self.assertEqual(
            calls,
            [(
                "/me/player/repeat",
                "PUT",
                {"state": "off", "device_id": "speaker-1"},
                None,
            )],
        )
        self.assertEqual(handler.sent[0][0], 200)
        self.assertEqual(handler.sent[0][1]["state"], "off")

    def test_repeat_rejects_unknown_state(self):
        handler = self.make_handler()

        handler.handle_repeat({"state": ["forever"]})

        self.assertEqual(handler.sent[0][0], 400)
        self.assertFalse(handler.sent[0][1]["ok"])


if __name__ == "__main__":
    unittest.main()
