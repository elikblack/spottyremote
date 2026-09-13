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

The browser front end predates the LAN server and still owns its own browser-side Spotify session. It remains useful as an independent remote and for richer controls.

## LAN server

`server/spotty_server.py` is the hardware-facing Spotty service. It uses only the Python standard library and is designed for Python 3.8+.

The server:

- owns the Spotify OAuth/refresh-token session used by hardware
- refreshes Spotify access tokens centrally
- exposes a small generic HTTP API to LAN clients
- enumerates Spotify Connect devices and transfers playback between them
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
POST /api/transfer?device_id=<spotify-device-id>&play=true|false
```

`/api/transfer` is intentionally generic. Hardware decides which endpoint it prefers and when a transfer should happen; the server only performs the Spotify operation. The `play` argument is optional at the Spotify layer but the current hardware sends it explicitly.

`/api/player` preserves the raw Spotify player object for compatibility but also exposes dial-friendly top-level fields:

```text
track_id
item_type
track_name
artist_name
is_playing
volume_percent
supports_volume
device_id
device_name
```

Those active-device fields let a hardware client make endpoint decisions without parsing the nested raw player object.

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
- volume
- enumerate Spotify Connect devices
- transfer playback between devices through both the browser and LAN server
- seek, shuffle, and repeat in the browser client

The Elecrow SpottyDial keeps its preferred-device policy on the device. Its current v1.1 development firmware resolves the friendly name `Everywhere` through `/api/devices`, then uses `/api/transfer` as needed. This keeps future hardware remotes free to implement different endpoint rules without adding remote-specific policy to the server.

## Security rules

- Never commit a Spotify Client Secret.
- Never commit access tokens or refresh tokens.
- The Client ID is not a secret and may be public.
- Browser production OAuth uses PKCE over HTTPS.
- The LAN server uses loopback PKCE and stores its refresh token locally.
- The current LAN API has **no client authentication**. Keep it on a trusted network until device authentication is added.
- If Spotify invalidates a refresh token, authorize that client again.

## Direction

The LAN server is the Spotify boundary for hardware, but not the owner of each remote's behavior. Hardware expresses intents and device-specific policy; the service layer owns OAuth, Spotify API details, token refresh, generic playback/device primitives, retries, and artwork fetching.
