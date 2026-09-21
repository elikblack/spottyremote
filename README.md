# SpottyRemote

Reusable Spotify control core plus browser and hardware-facing front ends.

The project is intentionally split so Spotify mechanics are not tied to a particular display, knob, browser, or microcontroller. Hardware owns device-specific interaction policy; SpottyServer owns OAuth, Spotify API mechanics, shared player state, retries, history, and generic playback primitives.

## Current structure

```text
core/
  spotify-client.js        browser/shared Spotify API commands
  playback-state.js        normalized playback state + interpolation

web/
  browser-auth.js          browser PKCE authorization + token refresh
  config.js                public browser configuration
  app.js                   reference web remote
  styles.css               reference UI

server/
  spotty_server.py         generic LAN API + Spotify OAuth/token owner
  spotty_instrumented.py   metrics, dashboard, shared player cache, cooldowns
  spotty_history_server.py production history wrapper
  spotty_supervisor.py     health-check/restart supervisor
  install_launch_agent.py  macOS launchd installer
  README.md                detailed server operations

index.html                  GitHub Pages entry point
```

## Web remote

Canonical deployment:

```text
https://spotty.elistuff.com/
```

The browser version uses Authorization Code with PKCE. No Spotify Client Secret is used or required. The configured Client ID is public by design and lives in `web/config.js`.

The browser front end predates the LAN server and still owns its own browser-side Spotify session. It remains useful as an independent remote and for richer controls.

## SpottyServer

SpottyServer is the LAN Spotify boundary used by hardware such as SpottyDial.

The production runtime is layered:

```text
spotty_supervisor.py
  -> spotty_history_server.py
      -> spotty_instrumented.py
          -> spotty_server.py
```

The layers provide:

- Spotify OAuth and refresh-token ownership
- generic playback/device/queue/playlist primitives
- Spotify Connect device discovery and transfer
- album-art proxy/cache
- shared player-state caching for multiple hardware clients
- adaptive idle polling
- Spotify 429 cooldown handling
- metrics and LAN status dashboard
- persistent Spotty-managed playback history
- health supervision and automatic restart

The service binds to `0.0.0.0:8787` by default.

See [server/README.md](server/README.md) for setup, launchd installation, logs, configuration, and detailed route behavior.

## Recommended macOS runtime

For an always-on Mac, use the included LaunchAgent rather than running the core server manually:

```bash
python3 server/install_launch_agent.py
```

The LaunchAgent runs the supervisor, uses `RunAtLoad` and `KeepAlive`, and writes logs to:

```text
~/Library/Logs/SpottyServer.log
~/Library/Logs/SpottyServer.err.log
```

Re-running the installer replaces and restarts the existing service. This is also the normal restart mechanism after pulling server changes.

Service label:

```text
com.elistuff.spottyserver
```

## Spotify authorization

Authorize Spotify from a browser on the server Mac:

```text
http://127.0.0.1:8787/
```

Spotify redirect URI:

```text
http://127.0.0.1:8787/auth/callback
```

Tokens are stored locally in:

```text
server/.spotty_tokens.json
```

That file is intentionally ignored by Git.

## Hardware API

Important current routes include:

```text
GET  /api/health
GET  /api/metrics
GET  /api/player
GET  /api/player?refresh=1
GET  /api/devices
GET  /api/queue
GET  /api/queue?item_id=<spotify-item-id>
GET  /api/artwork?item_type=track|episode&track_id=<spotify-id>&size=<target-width>
GET  /api/history?limit=<n>
GET  /history

POST /api/play
POST /api/pause
POST /api/playpause
POST /api/next
POST /api/previous
POST /api/volume?value=0..100&device_id=<optional>
POST /api/transfer?device_id=<id>&play=true|false
POST /api/queue?item_type=track|episode&item_id=<id>&device_id=<optional>
POST /api/repeat?state=track|context|off&device_id=<optional>
POST /api/playlist/add?playlist_id=<id>&item_type=track|episode&item_id=<id>
```

`/api/queue` returns a compact current/next snapshot and queue count. If `item_id` is supplied, it also returns the zero-based position of the first matching item in the returned Spotify queue, or `-1` when absent. SpottyDial uses this to implement queue-first Recently Played playback.

`/api/player` preserves the raw Spotify player object for compatibility and also exposes hardware-friendly top-level fields including:

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
progress_ms
duration_ms
```

The production history wrapper treats `/api/player?refresh=1` as an authoritative read for safety-sensitive startup verification.

## Spotty-managed history

Production SpottyServer keeps a local history of tracks observed during Spotty-managed playback sessions.

This is deliberately not a general Spotify account listening-history service.

Backing file:

```text
server/.spotty_play_history.jsonl
```

Readable page:

```text
http://127.0.0.1:8787/history
```

API:

```text
GET /api/history?limit=500
```

History logging piggybacks on authoritative player refreshes and does not require a separate upstream polling stream.

## Artwork proxy

`/api/artwork` accepts a Spotify track or episode ID plus a required requested source width. The server chooses the smallest Spotify-provided source at least that wide, or the largest available source when none is large enough.

For example, SpottySquare can request:

```text
/api/artwork?item_type=track&track_id=<spotify-id>&size=480
```

The server returns Spotify's original compressed image bytes unchanged. `size=480` is therefore a source-selection hint, not a guarantee of 480 x 480 output; a typical Spotify image set may return a 640 x 640 source. Clients must supply `size` explicitly, so each hardware model states its own artwork requirement. Actual dimensions remain authoritative in the response headers.

The binary response includes:

```text
Content-Type
Content-Length
X-Artwork-Width
X-Artwork-Height
```

This keeps ESP32 clients on simple LAN HTTP rather than making them own Spotify/CDN authentication and artwork TLS behavior.

## SpottyDial relationship

SpottyDial's preferred-output, touch UI, Recently Played action semantics, launch volume, and menu behavior remain firmware policy.

The server stays generic. It exposes queue inspection/add, Next, device lookup/transfer, repeat control, playlist append, history, and authoritative player reads; SpottyDial combines those primitives into its physical-control model.

In particular, SpottyDial's current Recent Play behavior is:

```text
queue selected item
-> locate its position
-> Next until current
-> verify
-> Play
```

Existing queued songs ahead of the selected item may therefore be intentionally consumed. That policy belongs to the dial, not SpottyServer.

## Security rules

- Never commit a Spotify Client Secret.
- Never commit access tokens or refresh tokens.
- The Client ID is not a secret and may be public.
- Browser production OAuth uses PKCE over HTTPS.
- The LAN server uses loopback PKCE and stores its refresh token locally.
- The LAN API has **no client authentication**.
- Keep port 8787 on a trusted network and do not expose it publicly.
- If Spotify invalidates a refresh token, authorize that client again.

## Direction

The LAN server should remain the generic Spotify boundary for hardware rather than becoming the owner of each remote's interaction design.

New server features should generally be reusable primitives. Device-specific policy should stay with the hardware unless multiple clients genuinely need the same behavior.
