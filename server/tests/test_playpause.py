import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spotty_server as server


def player(playing, device_id="speaker-1", restricted=False):
    return 200, json.dumps({"is_playing": playing, "device": {
        "id": device_id, "is_restricted": restricted}}).encode(), {}


def error(status):
    return status, json.dumps({"error": {"status": status, "message": "Not found"}}).encode(), {}


class PlayPauseTests(unittest.TestCase):
    def run_toggle(self, responses, require_device=True):
        handler = object.__new__(server.SpottyHandler)
        sent, calls = [], []
        handler.send_json = lambda status, value: sent.append((status, value))
        remaining = list(responses)

        def request(path, method="GET", query=None, body=None, retry_auth=True):
            calls.append((path, method, query, body))
            self.assertTrue(remaining, "unexpected extra Spotify request")
            response = remaining.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with patch.object(server, "spotify_request", side_effect=request), patch.object(server.time, "sleep"):
            handler.handle_playpause({"require_device": ["true"]} if require_device else None)
        self.assertFalse(remaining)
        self.assertEqual(len(sent), 1)
        return sent[0], calls

    def test_pause_and_resume_explicitly_target_current_device_without_uri_body(self):
        for playing, action in ((True, "pause"), (False, "play")):
            with self.subTest(action=action):
                sent, calls = self.run_toggle([player(playing), (204, b"", {})])
                self.assertEqual(sent[0], 200)
                self.assertEqual(sent[1]["action"], action)
                self.assertEqual(calls[-1], ("/me/player/" + action, "PUT", {"device_id": "speaker-1"}, None))

    def test_404_retries_same_action_on_same_device_once(self):
        for playing in (True, False):
            with self.subTest(playing=playing):
                sent, calls = self.run_toggle([player(playing), error(404), player(playing), (204, b"", {})])
                self.assertEqual(sent[0], 200)
                self.assertEqual(calls[1], calls[3])
                self.assertEqual(calls[2], ("/me/player", "GET", None, None))

    def test_404_already_achieved_does_not_toggle_again(self):
        for playing in (True, False):
            with self.subTest(playing=playing):
                sent, calls = self.run_toggle([player(playing), error(404), player(not playing)])
                self.assertTrue(sent[1]["verified"])
                self.assertEqual(len(calls), 3)

    def test_404_device_change_does_not_control_new_output(self):
        sent, calls = self.run_toggle([player(True), error(404), player(True, "speaker-2")])
        self.assertEqual(sent[0], 502)
        self.assertEqual(sent[1]["spotify_status"], 404)
        self.assertEqual(len(calls), 3)

    def test_404_inactive_or_unreadable_or_restricted_state_does_not_retry(self):
        for state in ((204, b"", {}), error(429), player(True, restricted=True), player(None)):
            with self.subTest(state=state):
                sent, calls = self.run_toggle([player(True), error(404), state])
                self.assertEqual(sent[0], 502)
                self.assertEqual(len(calls), 3)

    def test_persistent_404_stops_after_second_put(self):
        sent, calls = self.run_toggle([player(True), error(404), player(True), error(404)])
        self.assertEqual(sent[0], 502)
        self.assertEqual(sent[1]["error"], "pause: Not found")
        self.assertEqual(len(calls), 4)

    def test_other_action_errors_are_not_retried(self):
        for status in (403, 429, 500):
            with self.subTest(status=status):
                sent, calls = self.run_toggle([player(False), error(status)])
                self.assertEqual(sent[0], 502)
                self.assertEqual(sent[1]["spotify_status"], status)
                self.assertEqual(len(calls), 2)

    def test_player_read_error_is_distinguished_from_action_error(self):
        sent, calls = self.run_toggle([error(404)])
        self.assertEqual(sent[1]["error"], "player read: Not found")
        self.assertEqual(len(calls), 1)

    def test_idle_incomplete_or_restricted_player_never_blindly_resumes(self):
        for response in ((204, b"", {}), (200, b"{}", {}), player(True, ""),
                         player(None), player(True, restricted=True), (200, b"[]", {})):
            with self.subTest(response=response):
                sent, calls = self.run_toggle([response])
                self.assertEqual(sent[0], 409)
                self.assertEqual(len(calls), 1)

    def test_legacy_idle_resume_is_preserved_without_strict_flag(self):
        sent, calls = self.run_toggle([(204, b"", {}), (204, b"", {})], require_device=False)
        self.assertEqual(sent[0], 200)
        self.assertEqual(sent[1]["action"], "play")
        self.assertEqual(calls[-1], ("/me/player/play", "PUT", None, None))

    def test_ambiguous_transport_failure_never_repeats_toggle(self):
        sent, calls = self.run_toggle([player(True), OSError("connection lost")])
        self.assertEqual(sent[0], 503)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
