import io
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import spotty_weather as weather


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.cache = weather.WeatherCache()

    def response(self, **overrides):
        current = dict(temperature_2m=54.3, weather_code=61, is_day=1)
        current.update(overrides)
        return io.BytesIO(json.dumps(dict(current=current)).encode())

    def test_cold_cache_returns_clock_without_waiting(self):
        with patch.object(weather.threading, 'Thread') as thread:
            first = self.cache.snapshot()
            second = self.cache.snapshot()
        thread.return_value.start.assert_called_once()
        self.assertFalse(first['available'])
        self.assertGreater(first['unix_time'], 1700000000)
        self.assertEqual(first['location'], '98107')
        self.assertFalse(second['available'])

    def test_refresh_and_cached_requests(self):
        with patch.object(weather.urllib.request, 'urlopen', return_value=self.response()) as fetch:
            self.cache.refresh()
        with patch.object(weather.threading, 'Thread') as thread:
            result = self.cache.snapshot()
        thread.assert_not_called()
        self.assertTrue(result['available'])
        self.assertEqual(result['temperature'], 54.3)
        self.assertTrue(result['is_day'])
        self.assertFalse(result['stale'])
        self.assertIn('temperature_unit=fahrenheit', fetch.call_args[0][0].full_url)
        self.assertEqual(fetch.call_args[1]['timeout'], 8)

    def test_failure_keeps_old_reading_but_expires_it(self):
        with patch.object(weather.urllib.request, 'urlopen', return_value=self.response()):
            self.cache.refresh()
        self.cache.fetched_at = time.time() - 1000
        with patch.object(weather.urllib.request, 'urlopen', side_effect=OSError('offline')):
            self.cache.refresh()
        result = self.cache.snapshot()
        self.assertTrue(result['available'])
        self.assertTrue(result['stale'])
        self.assertFalse(self.cache.refreshing)
        self.assertLessEqual(self.cache.next_attempt - time.monotonic(), weather.RETRY_SECONDS)
        self.cache.fetched_at = time.time() - 7201
        result = self.cache.snapshot()
        self.assertFalse(result['available'])
        self.assertNotIn('temperature', result)
        self.assertGreater(result['unix_time'], 1700000000)

    def test_malformed_readings_never_replace_good_data(self):
        with patch.object(weather.urllib.request, 'urlopen', return_value=self.response()):
            self.cache.refresh()
        for bad in ({'temperature_2m': None}, {'temperature_2m': float('nan')},
                    {'temperature_2m': True}, {'weather_code': None}, {'is_day': 3}):
            with patch.object(weather.urllib.request, 'urlopen', return_value=self.response(**bad)):
                self.cache.refresh()
            self.assertEqual(self.cache.snapshot()['temperature'], 54.3)


if __name__ == '__main__':
    unittest.main()
