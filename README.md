# SpottyRemote

Reusable Spotify remote-control core plus browser and hardware-facing front ends.

The project is intentionally split so Spotify behavior is not tied to a particular display, knob, browser, or microcontroller.

## Current structure

```text
core/
  spotify-client.js   Spotify Web API commands; UI/platform agnostic
  playback-state.js   normalized playback state + progress interpolation
web/
  browser-auth.js     browser PKCE authorization + token refresh
  config.js           public browser configuration
  app.js              reference web remote
  styles.css          reference UI
server/
  spotty_server.py    LAN API, Spotify OAuth/token owner, artwork proxy
index.html             GitHub Pages entry point
```

## Web remote

Canonical deployment:

```text
https://spotty.elistuff.com/
```

The browser version uses Authorization Code with PKCE. No Spotify Client Secret is used or required. The configured Client ID is public by design and lives in `web/config.js`.

The browser front end predates the LAN server and still owns its own browser-side Spotify session. It is useful as an independent remote and for selecting Spotify Connect devices.

## LAN server

`server/spotty_server.py` is the hardware-facing Spotty service. It uses only the Python standard library and is designed for Python 3.8+.

The server:

- owns the Spotify OAuth/refresh-token session used by hardware
- refreshes Spotify access tokens centrally
- exposes a small HTTP API to LAN clients
- proxies/caches album artwork so an ESP32 never needs Spotify/CDN credentials or TLS handling for artwork
- binds to `0.0.0.0:8787` by default

Start it with:

```bash
python3 server/spotty_server.py
```

Authorize Spotify from the server Mac at:

```text
http://127.0.0.1:8787/
```

The Spotify developer dashboard redirect URI for this server is exactly:

```text
http://127.0.0.1:8787/auth/callback
```

Tokens are stored beside the server in `.spotty_tokens.json`, which is intentionally ignored by Git.

### Hardware API

Current routes include:

```text
GET  /api/health
GET  /api/player
GET  /api/devices
GET  /api/artwork?item_type=track&track_id=<spotify-id>
POST /api/play
POST /api/pause
POST /api/playpause
POST /api/next
POST /api/previous
POST /api/volume?value=0..100
```

`/api/player` preserves the raw Spotify player object for compatibility but also exposes dial-friendly top-level fields:

```text
track_id
item_type
track_name
artist_name
is_playing
volume_percent
supports_volume
```

JSON is serialized as UTF-8 rather than escaping ordinary non-ASCII names as `\uXXXX`.

### Artwork proxy

`/api/artwork` accepts a Spotify track or episode ID. The server fetches the item's metadata, chooses the available image closest to 300 px, downloads the image, and keeps a small in-memory cache of recent covers. This keeps artwork bandwidth modest for a 240×240 hardware display and lets the ESP32 stay entirely on plain LAN HTTP.

The binary response includes `Content-Type`, `Content-Length`, and source-size headers:

```text
X-Artwork-Width
X-Artwork-Height
```

The hardware uses those dimensions to center-crop the cover to its round display.

## Current controls

The shared Spotify behavior supports:

- read current track and active device
- play / pause
- previous / next
- seek in the browser client
- volume
- shuffle and repeat in the browser client
- enumerate Spotify Connect devices
- transfer playback between devices in the browser client

## Security rules

- Never commit a Spotify Client Secret.
- Never commit access tokens or refresh tokens.
- The Client ID is not a secret and may be public.
- Browser production OAuth uses PKCE over HTTPS.
- The LAN server uses loopback PKCE and stores its refresh token locally.
- The current LAN API has **no client authentication**. Keep it on a trusted network until device authentication is added.
- If Spotify invalidates a refresh token, authorize that client again.

## Direction

The Elecrow CrowPanel SpottyDial now uses the LAN server as its Spotify boundary. Future hardware controls should continue using the same pattern: hardware expresses intents and consumes compact state, while OAuth, Spotify API details, retries, and artwork fetching stay in the service layer.
