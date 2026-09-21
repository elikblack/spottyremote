#!/usr/bin/env python3
"""Small LAN-only Spotty server using only the Python standard library.

Responsibilities:
- own the Spotify OAuth/refresh-token session
- expose a tiny HTTP API to Spotty hardware on the local network
- avoid requiring Spotify credentials on the ESP32
- proxy/cache album artwork so hardware never needs Spotify/CDN credentials
- expose generic device enumeration and playback transfer primitives
- expose a generic playlist-item append primitive

Designed for Python 3.8+.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HOST = os.environ.get("SPOTTY_HOST", "0.0.0.0")
PORT = int(os.environ.get("SPOTTY_PORT", "8787"))
CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "927a9b735c9a4078be889423cbd2836f")
REDIRECT_URI = "http://127.0.0.1:{}/auth/callback".format(PORT)
TOKEN_FILE = Path(__file__).resolve().parent / ".spotty_tokens.json"

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = [
    "user-read-playback-state",
    "user-read-currently-playing",
    "user-modify-playback-state",
    "playlist-modify-private",
    "playlist-modify-public",
]

ARTWORK_CACHE_ITEMS = 12
ARTWORK_MIN_SIZE = 64
ARTWORK_MAX_SIZE = 2048
ARTWORK_MAX_BYTES = 2 * 1024 * 1024

_oauth_state = None
_pkce_verifier = None
_token_lock = threading.Lock()
_artwork_cache = OrderedDict()
_artwork_cache_lock = threading.Lock()


def _b64url(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _json_bytes(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _valid_spotify_id(value):
    return bool(value) and value.isalnum()


def load_tokens():
    if not TOKEN_FILE.exists():
        return {}
    try:
        with TOKEN_FILE.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def save_tokens(tokens):
    temp = TOKEN_FILE.with_suffix(TOKEN_FILE.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(tokens, handle, indent=2)
    try:
        os.chmod(str(temp), 0o600)
    except OSError:
        pass
    os.replace(str(temp), str(TOKEN_FILE))


def token_request(fields):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        TOKEN_URL,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "SpottyServer/1.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError("Spotify token request failed ({}): {}".format(error.code, body))


def store_token_response(token, old_tokens=None):
    old_tokens = old_tokens or {}
    merged = dict(old_tokens)
    if token.get("access_token"):
        merged["access_token"] = token["access_token"]
    if token.get("refresh_token"):
        merged["refresh_token"] = token["refresh_token"]
    if token.get("scope"):
        merged["scope"] = token["scope"]
    if token.get("expires_in"):
        merged["expires_at"] = int(time.time()) + int(token["expires_in"])
    save_tokens(merged)
    return merged


def refresh_access_token(force=False):
    with _token_lock:
        tokens = load_tokens()
        access_token = tokens.get("access_token")
        expires_at = int(tokens.get("expires_at", 0) or 0)

        if not force and access_token and expires_at > int(time.time()) + 30:
            return access_token

        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            raise RuntimeError("Spotify is not authorized yet.")

        token = token_request({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        })
        tokens = store_token_response(token, tokens)
        return tokens.get("access_token")


def spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
    token = refresh_access_token(False)
    url = API_BASE + path
    if query:
        url += "?" + urllib.parse.urlencode(query)

    payload = None
    headers = {
        "Authorization": "Bearer " + token,
        "User-Agent": "SpottyServer/1.1",
    }
    if body is not None:
        payload = _json_bytes(body)
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, raw, dict(response.headers)
    except urllib.error.HTTPError as error:
        raw = error.read()
        if error.code == 401 and retry_auth:
            refresh_access_token(True)
            return spotify_request(path, method, query, body, retry_auth=False)
        return error.code, raw, dict(error.headers)


def decode_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def api_result(status, raw):
    parsed = decode_json(raw)
    if 200 <= status < 300:
        return status, parsed
    message = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = error.get("message")
        elif isinstance(error, str):
            message = error
    return status, {
        "ok": False,
        "spotify_status": status,
        "error": message or "Spotify request failed",
    }


def player_summary(data):
    data = data if isinstance(data, dict) else {}
    item = data.get("item") if isinstance(data.get("item"), dict) else {}
    device = data.get("device") if isinstance(data.get("device"), dict) else {}
    item_type = item.get("type") or "track"

    artist_name = ""
    if item_type == "track":
        artists = item.get("artists") if isinstance(item.get("artists"), list) else []
        names = [
            artist.get("name")
            for artist in artists
            if isinstance(artist, dict) and artist.get("name")
        ]
        artist_name = ", ".join(names)
    elif item_type == "episode":
        show = item.get("show") if isinstance(item.get("show"), dict) else {}
        artist_name = show.get("name") or item.get("publisher") or ""

    volume = device.get("volume_percent")
    if not isinstance(volume, int):
        volume = None

    return {
        "track_id": item.get("id") or "",
        "item_type": item_type,
        "track_name": item.get("name") or "",
        "artist_name": artist_name,
        "is_playing": bool(data.get("is_playing")),
        "volume_percent": volume,
        "supports_volume": bool(device.get("supports_volume", True)),
        "device_id": device.get("id") or "",
        "device_name": device.get("name") or "",
    }


def _artwork_cache_get(key):
    with _artwork_cache_lock:
        value = _artwork_cache.get(key)
        if value is not None:
            _artwork_cache.move_to_end(key)
        return value


def _artwork_cache_put(key, value):
    with _artwork_cache_lock:
        _artwork_cache[key] = value
        _artwork_cache.move_to_end(key)
        while len(_artwork_cache) > ARTWORK_CACHE_ITEMS:
            _artwork_cache.popitem(last=False)


def _pick_artwork(images, target_size):
    candidates = [
        image for image in images
        if isinstance(image, dict) and image.get("url")
    ]
    if not candidates:
        return None

    sized = [
        image for image in candidates
        if isinstance(image.get("width"), int) and image.get("width") > 0
    ]
    if not sized:
        # Spotify image lists are ordered, so retain deterministic behavior if
        # width metadata is absent rather than inventing a size.
        return candidates[0]

    large_enough = [image for image in sized if image["width"] >= target_size]
    if large_enough:
        return min(large_enough, key=lambda image: image["width"])

    return max(sized, key=lambda image: image["width"])


def fetch_artwork(item_type, item_id, target_size):
    item_type = item_type if item_type in ("track", "episode") else "track"
    key = "{}:{}:{}".format(item_type, item_id, target_size)
    cached = _artwork_cache_get(key)
    if cached is not None:
        return cached

    endpoint = "/tracks/{}".format(urllib.parse.quote(item_id, safe=""))
    if item_type == "episode":
        endpoint = "/episodes/{}".format(urllib.parse.quote(item_id, safe=""))

    status, raw, _headers = spotify_request(endpoint)
    if status != 200:
        _status, data = api_result(status, raw)
        raise RuntimeError(
            data.get("error") if isinstance(data, dict) else "Artwork metadata request failed"
        )

    item = decode_json(raw) or {}
    if item_type == "track":
        album = item.get("album") if isinstance(item.get("album"), dict) else {}
        images = album.get("images") if isinstance(album.get("images"), list) else []
    else:
        images = item.get("images") if isinstance(item.get("images"), list) else []

    image = _pick_artwork(images, target_size)
    if not image:
        raise RuntimeError("No artwork available for this item")

    request = urllib.request.Request(
        image["url"],
        headers={"User-Agent": "SpottyServer/1.1", "Accept": "image/*"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        content_type = response.headers.get_content_type() or "image/jpeg"
        payload = response.read(ARTWORK_MAX_BYTES + 1)

    if len(payload) > ARTWORK_MAX_BYTES:
        raise RuntimeError("Artwork exceeded {} bytes".format(ARTWORK_MAX_BYTES))

    result = {
        "payload": payload,
        "content_type": content_type,
        "width": int(image.get("width") or 0),
        "height": int(image.get("height") or 0),
    }
    _artwork_cache_put(key, result)
    return result


class SpottyHandler(BaseHTTPRequestHandler):
    server_version = "SpottyServer/1.1"

    def log_message(self, fmt, *args):
        print("{} - {}".format(self.client_address[0], fmt % args))

    def send_json(self, status, value):
        payload = _json_bytes(value)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def send_bytes(self, status, payload, content_type, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "public, max-age=86400")
        for key, value in (headers or {}).items():
            self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(payload)

    def send_html(self, status, html):
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def parsed_url(self):
        return urllib.parse.urlparse(self.path)

    def do_GET(self):
        parsed = self.parsed_url()
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/":
            tokens = load_tokens()
            authorized = bool(tokens.get("refresh_token"))
            state = "AUTHORIZED" if authorized else "NOT AUTHORIZED"
            color = "#33cc66" if authorized else "#cc9933"
            scopes = tokens.get("scope") or "none"
            self.send_html(200, """<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Spotty Server</title>
<style>body{{font:16px system-ui;max-width:700px;margin:60px auto;padding:0 20px;background:#111;color:#eee}}a{{color:#6cf}}code{{background:#222;padding:2px 5px}}.state{{color:{color};font-weight:700}}</style></head>
<body><h1>Spotty Server</h1><p class=\"state\">{state}</p>
<p><a href=\"/auth/login\">Connect / reconnect Spotify</a></p>
<p>Granted scopes: <code>{scopes}</code></p>
<p>Health: <code>/api/health</code><br>
Player: <code>/api/player</code><br>
Devices: <code>/api/devices</code><br>
Artwork: <code>/api/artwork?item_type=track&amp;track_id=...&amp;size=480</code> (size optional)<br>
Play: <code>POST /api/play?device_id=...</code> (device_id optional)<br>
Volume: <code>POST /api/volume?value=...&amp;device_id=...</code> (device_id optional)<br>
Transfer: <code>POST /api/transfer?device_id=...&amp;play=true</code><br>
Playlist append: <code>POST /api/playlist/add?playlist_id=...&amp;item_type=track&amp;item_id=...</code></p>
</body></html>""".format(state=state, color=color, scopes=scopes))
            return

        if parsed.path == "/api/health":
            tokens = load_tokens()
            self.send_json(200, {
                "ok": True,
                "service": "spotty",
                "authorized": bool(tokens.get("refresh_token")),
                "scope": tokens.get("scope") or "",
            })
            return

        if parsed.path == "/auth/login":
            self.begin_oauth()
            return

        if parsed.path == "/auth/callback":
            self.finish_oauth(parsed)
            return

        if parsed.path == "/api/player":
            self.handle_player()
            return

        if parsed.path == "/api/artwork":
            self.handle_artwork(parsed)
            return

        if parsed.path == "/api/devices":
            self.proxy_json("/me/player/devices", "GET")
            return
        if parsed.path == "/api/queue":
            self.handle_queue(query)
            return

        self.send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        parsed = self.parsed_url()
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/play":
            self.handle_play(query)
            return
        if parsed.path == "/api/pause":
            self.proxy_empty("/me/player/pause", "PUT")
            return
        if parsed.path == "/api/queue":
            self.handle_queue_add(query)
            return
        if parsed.path == "/api/next":
            self.handle_next(query)
            return
        if parsed.path == "/api/previous":
            self.proxy_empty("/me/player/previous", "POST")
            return
        if parsed.path == "/api/playpause":
            self.handle_playpause()
            return
        if parsed.path == "/api/volume":
            self.handle_volume(query)
            return
        if parsed.path == "/api/transfer":
            self.handle_transfer(query)
            return
        if parsed.path == "/api/repeat":
            self.handle_repeat(query)
            return
        if parsed.path == "/api/playlist/add":
            self.handle_playlist_add(query)
            return

        self.send_json(404, {"ok": False, "error": "Not found"})

    def begin_oauth(self):
        global _oauth_state, _pkce_verifier
        _pkce_verifier = secrets.token_urlsafe(64)
        _oauth_state = secrets.token_urlsafe(24)
        challenge = _b64url(hashlib.sha256(_pkce_verifier.encode("ascii")).digest())

        params = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join(SCOPES),
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": _oauth_state,
        })
        self.redirect(AUTH_URL + "?" + params)

    def finish_oauth(self, parsed):
        global _oauth_state, _pkce_verifier
        query = urllib.parse.parse_qs(parsed.query)
        error = query.get("error", [None])[0]
        code = query.get("code", [None])[0]
        returned_state = query.get("state", [None])[0]

        if error:
            self.send_html(400, "<h1>Spotify authorization failed</h1><p>{}</p>".format(error))
            return
        if not code or not _pkce_verifier or returned_state != _oauth_state:
            self.send_html(400, "<h1>OAuth state mismatch</h1><p>Start authorization again.</p>")
            return

        try:
            token = token_request({
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": _pkce_verifier,
            })
            store_token_response(token)
            self.send_html(200, "<h1>Spotty is connected.</h1><p>You can close this tab.</p>")
        except Exception as exc:
            self.send_html(500, "<h1>Token exchange failed</h1><pre>{}</pre>".format(str(exc)))
        finally:
            _oauth_state = None
            _pkce_verifier = None

    def handle_player(self):
        try:
            status, raw, _headers = spotify_request("/me/player")
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})
            return

        if status == 204:
            self.send_json(200, {"ok": True, "active": False})
            return

        out_status, data = api_result(status, raw)
        if 200 <= out_status < 300:
            summary = player_summary(data)
            response = {"ok": True, "active": True}
            response.update(summary)
            response["player"] = data
            self.send_json(200, response)
        else:
            self.send_json(502, data)

    def handle_artwork(self, parsed):
        query = urllib.parse.parse_qs(parsed.query)
        item_id = query.get("track_id", [None])[0]
        item_type = query.get("item_type", ["track"])[0]
        raw_size = query.get("size", [None])[0]

        if not _valid_spotify_id(item_id):
            self.send_json(400, {"ok": False, "error": "track_id must be a Spotify item id"})
            return
        if item_type not in ("track", "episode"):
            self.send_json(400, {"ok": False, "error": "item_type must be track or episode"})
            return

        if raw_size is None:
            self.send_json(400, {
                "ok": False,
                "error": "size is required and must be an integer from {} to {}".format(
                    ARTWORK_MIN_SIZE, ARTWORK_MAX_SIZE
                ),
            })
            return

        try:
            target_size = int(raw_size)
        except (TypeError, ValueError):
            self.send_json(400, {
                "ok": False,
                "error": "size must be an integer from {} to {}".format(
                    ARTWORK_MIN_SIZE, ARTWORK_MAX_SIZE
                ),
            })
            return
        if target_size < ARTWORK_MIN_SIZE or target_size > ARTWORK_MAX_SIZE:
            self.send_json(400, {
                "ok": False,
                "error": "size must be an integer from {} to {}".format(
                    ARTWORK_MIN_SIZE, ARTWORK_MAX_SIZE
                ),
            })
            return

        try:
            artwork = fetch_artwork(item_type, item_id, target_size)
            self.send_bytes(
                200,
                artwork["payload"],
                artwork["content_type"],
                {
                    "X-Artwork-Width": artwork["width"],
                    "X-Artwork-Height": artwork["height"],
                },
            )
        except Exception as exc:
            self.send_json(502, {"ok": False, "error": str(exc)})

    def handle_queue(self, query=None):
        query = query or {}
        find_item_id = query.get("item_id", [None])[0]
        if find_item_id is not None and not _valid_spotify_id(find_item_id):
            self.send_json(400, {
                "ok": False,
                "error": "item_id must be a Spotify item id",
            })
            return

        try:
            status, raw, _headers = spotify_request("/me/player/queue")
            out_status, data = api_result(status, raw)
            if not (200 <= out_status < 300) or not isinstance(data, dict):
                self.send_json(502, data)
                return

            current = data.get("currently_playing")
            if not isinstance(current, dict):
                current = {}
            queue = data.get("queue")
            if not isinstance(queue, list):
                queue = []
            next_item = queue[0] if queue and isinstance(queue[0], dict) else {}

            response = {
                "ok": True,
                "current_item_id": current.get("id") or "",
                "current_item_type": current.get("type") or "",
                "next_item_id": next_item.get("id") or "",
                "next_item_type": next_item.get("type") or "",
                "queue_count": len(queue),
            }

            if find_item_id is not None:
                item_position = -1
                for index, item in enumerate(queue):
                    if isinstance(item, dict) and item.get("id") == find_item_id:
                        item_position = index
                        break
                response["item_position"] = item_position

            self.send_json(200, response)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def handle_queue_add(self, query):
        device_id = query.get("device_id", [None])[0]
        item_id = query.get("item_id", [None])[0]
        item_type = query.get("item_type", ["track"])[0]

        if not _valid_spotify_id(item_id):
            self.send_json(400, {"ok": False, "error": "item_id must be a Spotify item id"})
            return
        if item_type not in ("track", "episode"):
            self.send_json(400, {"ok": False, "error": "item_type must be track or episode"})
            return

        spotify_uri = "spotify:{}:{}".format(item_type, item_id)
        spotify_query = {"uri": spotify_uri}
        if device_id:
            spotify_query["device_id"] = device_id

        try:
            status, raw, _headers = spotify_request(
                "/me/player/queue",
                "POST",
                spotify_query,
            )
            if 200 <= status < 300:
                self.send_json(200, {
                    "ok": True,
                    "device_id": device_id or "",
                    "item_type": item_type,
                    "item_id": item_id,
                })
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def handle_next(self, query):
        device_id = query.get("device_id", [None])[0]
        spotify_query = {"device_id": device_id} if device_id else None
        self.proxy_empty("/me/player/next", "POST", spotify_query)

    def handle_play(self, query):
        device_id = query.get("device_id", [None])[0]
        item_id = query.get("item_id", [None])[0]
        item_type = query.get("item_type", ["track"])[0]

        spotify_query = {"device_id": device_id} if device_id else None
        if item_id is None:
            self.proxy_empty("/me/player/play", "PUT", spotify_query)
            return

        if not _valid_spotify_id(item_id):
            self.send_json(400, {"ok": False, "error": "item_id must be a Spotify item id"})
            return
        if item_type not in ("track", "episode"):
            self.send_json(400, {"ok": False, "error": "item_type must be track or episode"})
            return

        spotify_uri = "spotify:{}:{}".format(item_type, item_id)
        try:
            status, raw, _headers = spotify_request(
                "/me/player/play",
                "PUT",
                spotify_query,
                {"uris": [spotify_uri]},
            )
            if 200 <= status < 300:
                self.send_json(200, {
                    "ok": True,
                    "device_id": device_id or "",
                    "item_type": item_type,
                    "item_id": item_id,
                })
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def handle_playpause(self):
        try:
            status, raw, _headers = spotify_request("/me/player")
            if status == 204:
                target_path = "/me/player/play"
            elif status == 200:
                player = decode_json(raw) or {}
                target_path = "/me/player/pause" if player.get("is_playing") else "/me/player/play"
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
                return

            action_status, action_raw, _headers = spotify_request(target_path, "PUT")
            if 200 <= action_status < 300:
                self.send_json(200, {
                    "ok": True,
                    "action": "pause" if target_path.endswith("pause") else "play",
                })
            else:
                _status, data = api_result(action_status, action_raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def handle_volume(self, query):
        raw_value = query.get("value", [None])[0]
        device_id = query.get("device_id", [None])[0]
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            self.send_json(400, {"ok": False, "error": "value must be an integer from 0 to 100"})
            return

        value = max(0, min(100, value))
        spotify_query = {"volume_percent": value}
        if device_id:
            spotify_query["device_id"] = device_id

        try:
            status, raw, _headers = spotify_request(
                "/me/player/volume", "PUT", spotify_query
            )
            if 200 <= status < 300:
                self.send_json(200, {
                    "ok": True,
                    "volume": value,
                    "device_id": device_id or "",
                })
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def handle_transfer(self, query):
        device_id = query.get("device_id", [None])[0]
        raw_play = query.get("play", [None])[0]

        if not device_id:
            self.send_json(400, {"ok": False, "error": "device_id is required"})
            return

        body = {"device_ids": [device_id]}
        if raw_play is not None:
            normalized = raw_play.strip().lower()
            if normalized not in ("true", "false", "1", "0"):
                self.send_json(400, {"ok": False, "error": "play must be true or false"})
                return
            body["play"] = normalized in ("true", "1")

        try:
            status, raw, _headers = spotify_request("/me/player", "PUT", body=body)
            if 200 <= status < 300:
                self.send_json(200, {
                    "ok": True,
                    "device_id": device_id,
                    "play": body.get("play"),
                })
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def handle_repeat(self, query):
        state = query.get("state", [None])[0]
        device_id = query.get("device_id", [None])[0]

        if state not in ("track", "context", "off"):
            self.send_json(400, {
                "ok": False,
                "error": "state must be track, context, or off",
            })
            return

        spotify_query = {"state": state}
        if device_id:
            spotify_query["device_id"] = device_id

        try:
            status, raw, _headers = spotify_request(
                "/me/player/repeat",
                "PUT",
                spotify_query,
            )
            if 200 <= status < 300:
                self.send_json(200, {
                    "ok": True,
                    "state": state,
                    "device_id": device_id or "",
                })
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def handle_playlist_add(self, query):
        playlist_id = query.get("playlist_id", [None])[0]
        item_id = query.get("item_id", [None])[0]
        item_type = query.get("item_type", ["track"])[0]

        if not _valid_spotify_id(playlist_id):
            self.send_json(400, {"ok": False, "error": "playlist_id must be a Spotify playlist id"})
            return
        if not _valid_spotify_id(item_id):
            self.send_json(400, {"ok": False, "error": "item_id must be a Spotify item id"})
            return
        if item_type not in ("track", "episode"):
            self.send_json(400, {"ok": False, "error": "item_type must be track or episode"})
            return

        spotify_uri = "spotify:{}:{}".format(item_type, item_id)
        path = "/playlists/{}/items".format(urllib.parse.quote(playlist_id, safe=""))
        try:
            status, raw, _headers = spotify_request(
                path,
                "POST",
                body={"uris": [spotify_uri]},
            )
            if 200 <= status < 300:
                data = decode_json(raw) or {}
                self.send_json(200, {
                    "ok": True,
                    "playlist_id": playlist_id,
                    "item_type": item_type,
                    "item_id": item_id,
                    "snapshot_id": data.get("snapshot_id") or "",
                })
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def proxy_empty(self, path, method, query=None):
        try:
            status, raw, _headers = spotify_request(path, method, query=query)
            if 200 <= status < 300:
                self.send_json(200, {"ok": True})
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def proxy_json(self, path, method):
        try:
            status, raw, _headers = spotify_request(path, method)
            out_status, data = api_result(status, raw)
            if 200 <= out_status < 300:
                self.send_json(200, {"ok": True, "data": data})
            else:
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})


if __name__ == "__main__":
    print("Spotty Server")
    print("  Listening: http://{}:{}".format(HOST, PORT))
    print("  Authorize on this Mac: http://127.0.0.1:{}/".format(PORT))
    print("  Spotify redirect URI: {}".format(REDIRECT_URI))
    print("  Token file: {}".format(TOKEN_FILE))
    print()
    print("LAN clients may use this server without authentication. Keep it on a trusted network.")

    server = ThreadingHTTPServer((HOST, PORT), SpottyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Spotty Server.")
    finally:
        server.server_close()
