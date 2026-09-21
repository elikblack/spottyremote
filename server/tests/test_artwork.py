import json
import sys
import unittest
import urllib.parse
from pathlib import Path


SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import spotty_server as server


class ArtworkSelectionTests(unittest.TestCase):
    def setUp(self):
        self.images = [
            {"url": "small", "width": 64, "height": 64},
            {"url": "medium", "width": 300, "height": 300},
            {"url": "large", "width": 640, "height": 640},
        ]

    def test_explicit_size_64_selects_64(self):
        self.assertEqual(server._pick_artwork(self.images, 64)["url"], "small")

    def test_explicit_size_240_selects_300(self):
        self.assertEqual(server._pick_artwork(self.images, 240)["url"], "medium")

    def test_explicit_size_300_selects_300(self):
        self.assertEqual(server._pick_artwork(self.images, 300)["url"], "medium")

    def test_explicit_size_480_selects_640(self):
        self.assertEqual(server._pick_artwork(self.images, 480)["url"], "large")

    def test_explicit_size_larger_than_all_uses_largest(self):
        self.assertEqual(server._pick_artwork(self.images, 900)["url"], "large")

    def test_invalid_records_are_ignored(self):
        images = [
            None,
            {},
            {"width": 999},
            {"url": "medium", "width": 300, "height": 300},
        ]
        self.assertEqual(server._pick_artwork(images, 240)["url"], "medium")

    def test_missing_widths_fall_back_to_first_valid_image(self):
        images = [
            {"url": "first"},
            {"url": "second", "width": None},
        ]
        self.assertEqual(server._pick_artwork(images, 480)["url"], "first")


class ArtworkHandlerTests(unittest.TestCase):
    def setUp(self):
        self.original_fetch_artwork = server.fetch_artwork

    def tearDown(self):
        server.fetch_artwork = self.original_fetch_artwork

    def make_handler(self):
        handler = object.__new__(server.SpottyHandler)
        handler.sent_json = []
        handler.sent_bytes = []
        handler.send_json = lambda status, value: handler.sent_json.append((status, value))
        handler.send_bytes = (
            lambda status, payload, content_type, headers=None:
            handler.sent_bytes.append((status, payload, content_type, headers or {}))
        )
        return handler

    def test_omitted_size_returns_400(self):
        handler = self.make_handler()
        parsed = urllib.parse.urlparse("/api/artwork?item_type=track&track_id=abc123")
        handler.handle_artwork(parsed)

        self.assertEqual(handler.sent_json[0][0], 400)
        self.assertIn("size is required", handler.sent_json[0][1]["error"])

    def test_valid_size_is_passed_as_integer(self):
        handler = self.make_handler()
        calls = []

        def fake_fetch(item_type, item_id, target_size):
            calls.append((item_type, item_id, target_size))
            return {
                "payload": b"image",
                "content_type": "image/jpeg",
                "width": 640,
                "height": 640,
            }

        server.fetch_artwork = fake_fetch
        parsed = urllib.parse.urlparse(
            "/api/artwork?item_type=track&track_id=abc123&size=480"
        )
        handler.handle_artwork(parsed)

        self.assertEqual(calls, [("track", "abc123", 480)])
        self.assertEqual(handler.sent_bytes[0][3]["X-Artwork-Width"], 640)

    def test_non_integer_size_returns_400(self):
        handler = self.make_handler()
        parsed = urllib.parse.urlparse(
            "/api/artwork?item_type=track&track_id=abc123&size=large"
        )
        handler.handle_artwork(parsed)
        self.assertEqual(handler.sent_json[0][0], 400)

    def test_below_minimum_size_returns_400(self):
        handler = self.make_handler()
        parsed = urllib.parse.urlparse(
            "/api/artwork?item_type=track&track_id=abc123&size=63"
        )
        handler.handle_artwork(parsed)
        self.assertEqual(handler.sent_json[0][0], 400)

    def test_above_maximum_size_returns_400(self):
        handler = self.make_handler()
        parsed = urllib.parse.urlparse(
            "/api/artwork?item_type=track&track_id=abc123&size=2049"
        )
        handler.handle_artwork(parsed)
        self.assertEqual(handler.sent_json[0][0], 400)


class ArtworkCacheTests(unittest.TestCase):
    def setUp(self):
        self.original_spotify_request = server.spotify_request
        self.original_urlopen = server.urllib.request.urlopen
        with server._artwork_cache_lock:
            server._artwork_cache.clear()

    def tearDown(self):
        server.spotify_request = self.original_spotify_request
        server.urllib.request.urlopen = self.original_urlopen
        with server._artwork_cache_lock:
            server._artwork_cache.clear()

    def test_different_target_sizes_do_not_share_wrong_cached_result(self):
        metadata_calls = []

        def fake_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
            metadata_calls.append(path)
            payload = {
                "album": {
                    "images": [
                        {"url": "https://example/small", "width": 300, "height": 300},
                        {"url": "https://example/large", "width": 640, "height": 640},
                    ]
                }
            }
            return 200, json.dumps(payload).encode("utf-8"), {}

        class FakeHeaders:
            def get_content_type(self):
                return "image/jpeg"

        class FakeResponse:
            def __init__(self, url):
                self.url = url
                self.headers = FakeHeaders()

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, _limit):
                return b"large" if "large" in self.url else b"small"

        def fake_urlopen(request, timeout=15):
            return FakeResponse(request.full_url)

        server.spotify_request = fake_spotify_request
        server.urllib.request.urlopen = fake_urlopen

        medium = server.fetch_artwork("track", "abc123", 300)
        large = server.fetch_artwork("track", "abc123", 480)

        self.assertEqual(medium["width"], 300)
        self.assertEqual(medium["payload"], b"small")
        self.assertEqual(large["width"], 640)
        self.assertEqual(large["payload"], b"large")
        self.assertEqual(len(metadata_calls), 2)


if __name__ == "__main__":
    unittest.main()
