"""Small, nonblocking Open-Meteo cache shared by all Spotty clients."""
import json
import math
import threading
import time
import urllib.parse
import urllib.request

# Ballard / Seattle 98107. Keep configuration here until location settings exist.
LATITUDE = 47.668
LONGITUDE = -122.377
TEMPERATURE_UNIT = "fahrenheit"
REFRESH_SECONDS = 15 * 60
RETRY_SECONDS = 60
MAX_AGE_SECONDS = 2 * 60 * 60


class WeatherCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = None
        self.fetched_at = 0
        self.next_attempt = 0
        self.refreshing = False

    def snapshot(self):
        now = time.time()
        with self.lock:
            if not self.refreshing and time.monotonic() >= self.next_attempt:
                self.refreshing = True
                threading.Thread(target=self.refresh, name="spotty-weather", daemon=True).start()
            age = max(0, int(now - self.fetched_at)) if self.data else None
            result = {
                "ok": True, "unix_time": int(now), "location": "98107",
                "unit": "F" if TEMPERATURE_UNIT == "fahrenheit" else "C",
                "available": self.data is not None and age < MAX_AGE_SECONDS,
                "age_seconds": age, "stale": age is not None and age >= REFRESH_SECONDS,
                "source": "Open-Meteo", "source_url": "https://open-meteo.com/",
            }
            if result["available"]:
                result.update(self.data)
            return result

    def refresh(self):
        delay = RETRY_SECONDS
        try:
            query = urllib.parse.urlencode({
                "latitude": LATITUDE, "longitude": LONGITUDE,
                "current": "temperature_2m,weather_code,is_day",
                "temperature_unit": TEMPERATURE_UNIT,
            })
            request = urllib.request.Request(
                "https://api.open-meteo.com/v1/forecast?" + query,
                headers={"User-Agent": "SpottyServer/1.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read(65536))
            current = payload["current"]
            temperature = current["temperature_2m"]
            code = current["weather_code"]
            day = current["is_day"]
            if (type(temperature) not in (int, float) or not math.isfinite(temperature)
                    or not -150 <= temperature <= 160 or type(code) is not int
                    or not 0 <= code <= 99 or type(day) is not int or day not in (0, 1)):
                raise ValueError("invalid weather fields")
            with self.lock:
                self.data = {"temperature": round(temperature, 1), "weather_code": code,
                             "is_day": bool(day)}
                self.fetched_at = time.time()
            delay = REFRESH_SECONDS
        except (OSError, ValueError, KeyError, TypeError):
            # Keep the last good reading; callers get its age and stale flag.
            pass
        finally:
            with self.lock:
                self.next_attempt = time.monotonic() + delay
                self.refreshing = False


weather = WeatherCache()
