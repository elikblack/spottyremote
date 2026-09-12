#!/usr/bin/env python3
"""Small LAN-only Spotty server using only the Python standard library.

Responsibilities:
- own the Spotify OAuth/refresh-token session
- expose a tiny HTTP API to Spotty hardware on the local network
- avoid requiring Spotify credentials on the ESP32

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
]

_oauth_state = None
_pkce_verifier = None
_token_lock = threading.Lock()


def _b64url(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _json_bytes(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


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
            "User-Agent": "SpottyServer/1.0",
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
        "User-Agent": "SpottyServer/1.0",
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
    return status, {"ok": False, "spotify_status": status, "error": message or "Spotify request failed"}


class SpottyHandler(BaseHTTPRequestHandler):
    server_version = "SpottyServer/1.0"

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

        if parsed.path == "/":
            authorized = bool(load_tokens().get("refresh_token"))
            state = "AUTHORIZED" if authorized else "NOT AUTHORIZED"
            color = "#33cc66" if authorized else "#cc9933"
            self.send_html(200, """<!doctype html>
<html><head><meta charset=\"utf-8\"><title>Spotty Server</title>
<style>body{{font:16px system-ui;max-width:700px;margin:60px auto;padding:0 20px;background:#111;color:#eee}}a{{color:#6cf}}code{{background:#222;padding:2px 5px}}.state{{color:{color};font-weight:700}}</style></head>
<body><h1>Spotty Server</h1><p class=\"state\">{state}</p>
<p><a href=\"/auth/login\">Connect / reconnect Spotify</a></p>
<p>Health: <code>/api/health</code><br>Player: <code>/api/player</code></p>
</body></html>""".format(state=state, color=color))
            return

        if parsed.path == "/api/health":
            tokens = load_tokens()
            self.send_json(200, {
                "ok": True,
                "service": "spotty",
                "authorized": bool(tokens.get("refresh_token")),
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

        if parsed.path == "/api/devices":
            self.proxy_json("/me/player/devices", "GET")
            return

        self.send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        parsed = self.parsed_url()
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/play":
            self.proxy_empty("/me/player/play", "PUT")
            return
        if parsed.path == "/api/pause":
            self.proxy_empty("/me/player/pause", "PUT")
            return
        if parsed.path == "/api/next":
            self.proxy_empty("/me/player/next", "POST")
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
            self.send_json(200, {"ok": True, "active": True, "player": data})
        else:
            self.send_json(502, data)

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
                self.send_json(200, {"ok": True, "action": "pause" if target_path.endswith("pause") else "play"})
            else:
                _status, data = api_result(action_status, action_raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def handle_volume(self, query):
        raw_value = query.get("value", [None])[0]
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            self.send_json(400, {"ok": False, "error": "value must be an integer from 0 to 100"})
            return

        value = max(0, min(100, value))
        try:
            status, raw, _headers = spotify_request("/me/player/volume", "PUT", {"volume_percent": value})
            if 200 <= status < 300:
                self.send_json(200, {"ok": True, "volume": value})
            else:
                _status, data = api_result(status, raw)
                self.send_json(502, data)
        except Exception as exc:
            self.send_json(503, {"ok": False, "error": str(exc)})

    def proxy_empty(self, path, method):
        try:
            status, raw, _headers = spotify_request(path, method)
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
