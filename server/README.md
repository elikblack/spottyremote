# Spotty local server

A LAN-only Spotify control service for Spotty hardware. It uses only the Python standard library and is intended to run on an always-on Mac or other local computer.

## Requirements

- Python 3.8 or newer
- outbound HTTPS access to Spotify
- server and hardware clients on the same trusted LAN

No pip packages are required.

## Runtime layers

The production runtime is not just `spotty_server.py`.

```text
spotty_supervisor.py
  -> spotty_history_server.py
      -> spotty_instrumented.py
          -> spotty_server.py
```

Responsibilities:

- `spotty_server.py`: generic Spotify OAuth/token owner and LAN API
- `spotty_instrumented.py`: metrics, status dashboard, shared player-state cache, adaptive polling, Spotify rate-limit cooldowns
- `spotty_history_server.py`: persistent Spotty-managed history and authoritative forced player reads
- `spotty_supervisor.py`: process supervision and health checks
- `install_launch_agent.py`: macOS launchd installation/restart helper

A core-server change is still used by production because the wrapper layers import the core implementation.

## Spotify setup

Add this redirect URI to the Spotify developer app:

```text
http://127.0.0.1:8787/auth/callback
```

Authorize in a browser on the Mac running SpottyServer:

```text
http://127.0.0.1:8787/
```

Spotty requests playback-control plus public/private playlist-modification scopes. If an installation was authorized before playlist support was added, reconnect Spotify once so the stored refresh token receives the newer scopes.

OAuth tokens are stored locally in:

```text
server/.spotty_tokens.json
```

That file is ignored by Git and must not be committed or copied into the repository.

## Running the server

For focused development, the core server can be run directly:

```bash
python3 server/spotty_server.py
```

That does **not** include the normal history/instrumentation production behavior.

For a manual production-equivalent run:

```bash
python3 server/spotty_supervisor.py
```

The supervisor starts `spotty_history_server.py`, checks `/api/health`, and restarts the child if it exits or repeatedly stops responding.

For the normal always-on macOS installation, use launchd as described below.

## Recommended macOS service setup

Install or refresh the LaunchAgent from the repository root:

```bash
python3 server/install_launch_agent.py
```

The installer resolves the current repository path and Python interpreter automatically, then creates:

```text
~/Library/LaunchAgents/com.elistuff.spottyserver.plist
```

The generated agent uses:

```text
RunAtLoad = true
KeepAlive = true
service label = com.elistuff.spottyserver
```

It runs `spotty_supervisor.py`, which in turn runs the history/instrumented/core stack.

Logs:

```text
~/Library/Logs/SpottyServer.log
~/Library/Logs/SpottyServer.err.log
```

Useful commands:

```bash
python3 server/install_launch_agent.py --status
python3 server/install_launch_agent.py --uninstall
```

Re-running the installer is the normal restart procedure after pulling server changes. It boots out the old agent, rewrites the plist, bootstraps the service, enables it, and kickstarts it.

If port 8787 immediately becomes occupied again after killing a server child, that is normally launchd/supervisor doing their job. Do not start a second `nohup spotty_server.py` alongside the managed service.

## LAN access

The server listens on all network interfaces by default.

Examples:

```text
http://192.168.1.50:8787/api/health
http://your-mac.local:8787/api/health
```

For first hardware setup, a numeric LAN IP is useful because it removes mDNS from the debugging chain.

## Spotify traffic policy

Hardware clients may poll the LAN server frequently. The instrumented layer decides when another upstream Spotify refresh is actually needed, so adding more hardware clients does not multiply Spotify player polling.

Current player-state refresh policy:

```text
playing        5 seconds
paused         15 seconds
inactive       15s, 30s, 60s, 120s, then 300s
```

Explicit playback commands mark the player cache dirty so the next player read can refresh upstream state.

When Spotify returns HTTP 429, Spotty records the event, honors `Retry-After` when available, and suppresses further upstream requests until the cooldown expires. The dashboard and `/api/metrics` expose cache/cooldown/request information.

## Authoritative player reads

```text
GET /api/player?refresh=1
```

is used by hardware when stale cached success is not safe enough, especially during launch-volume verification and ambiguous playback-command handling.

The production history wrapper serializes these forced refreshes and does not allow a stale cached player value during a Spotify cooldown to masquerade as an authoritative fresh success.

## Spotty playback history

The production runtime keeps a local history of tracks observed during Spotty-managed playback sessions.

This is deliberately not Spotify account history. A managed session is armed by successful Spotty playback actions such as play, play/pause-to-play, Next, Previous, or transfer-and-play. While the managed session remains on the tracked device, newly observed items are appended.

Backing file:

```text
server/.spotty_play_history.jsonl
```

Readable page:

```text
http://127.0.0.1:8787/history
```

JSON API:

```text
GET /api/history?limit=500
```

History records include observation time, Spotify item ID/type, title, artist, album when available, output device, and playback context URI.

Logging is passive and piggybacks on authoritative player refreshes already being performed.

## API

Production wrappers add metrics/history behavior, while the generic hardware API comes from the core server.

```text
GET  /api/health
GET  /api/metrics
GET  /history
GET  /api/history?limit=500

GET  /api/player
GET  /api/player?refresh=1
GET  /api/devices

GET  /api/queue
GET  /api/queue?item_id=<spotify-item-id>

GET  /api/artwork?item_type=track|episode&track_id=<spotify-id>&size=<optional-target-width>

POST /api/play
POST /api/pause
POST /api/playpause
POST /api/next
POST /api/previous
POST /api/volume?value=0..100&device_id=<optional>
POST /api/transfer?device_id=<spotify-device-id>&play=true|false

POST /api/queue?item_type=track|episode&item_id=<spotify-id>&device_id=<optional>
POST /api/repeat?state=track|context|off&device_id=<optional>
POST /api/playlist/add?playlist_id=<spotify-playlist-id>&item_type=track|episode&item_id=<spotify-id>
```

The API is deliberately small and JSON-oriented so simple hardware clients do not need to reproduce Spotify OAuth or API details.

## Player response

`/api/player` preserves the raw Spotify player object for compatibility and also exposes hardware-friendly top-level fields such as:

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

JSON is serialized as UTF-8 rather than escaping ordinary non-ASCII names as `\uXXXX`.

## Queue API

### Read

```text
GET /api/queue
```

returns a compact projection of Spotify's playback queue:

```text
ok
current_item_id
current_item_type
next_item_id
next_item_type
queue_count
```

Supplying an item ID:

```text
GET /api/queue?item_id=<spotify-item-id>
```

adds:

```text
item_position
```

`item_position` is the zero-based index of the first returned queue item with that Spotify ID, or `-1` when not present.

This is used by SpottyDial's Recently Played implementation after it appends a selected item. The dial can then determine how many Next operations are needed before that item becomes current.

Caveat: if the same Spotify item ID already appears earlier in the queue, the current lookup returns that first existing match. If this becomes a practical problem, the client/server contract should compare before/after queue snapshots rather than identifying an insertion only by item ID.

### Add

```text
POST /api/queue?item_type=track|episode&item_id=<id>&device_id=<optional>
```

adds the item to Spotify's playback queue.

The server does not impose SpottyDial-specific policy about whether to skip existing queued items. That policy remains in the firmware.

## Repeat

Generic repeat control:

```text
POST /api/repeat?state=track|context|off&device_id=<optional>
```

The endpoint remains available as a primitive. Current SpottyDial Recently Played playback deliberately preserves the user's existing repeat mode and does not call this endpoint.

## Playlist append

```text
POST /api/playlist/add?playlist_id=...&item_type=track|episode&item_id=...
```

Hardware chooses the destination playlist and supplies the current Spotify item identity. The server owns authentication and translates that into Spotify's playlist API.

## Artwork proxy

```text
GET /api/artwork?item_type=track|episode&track_id=<spotify-id>&size=<optional-target-width>
```

If `size` is omitted, the server preserves the original SpottyDial behavior and chooses the Spotify source whose width is nearest 300 px.

An explicit `size` from 64 through 2048 pixels opts into quality-first source selection: the server chooses the smallest available Spotify image at least as wide as requested, or the largest available image when no source reaches the requested width. The value is a selection hint only. SpottyServer does not resize or recompress artwork.

For a common Spotify 64 / 300 / 640 image set:

```text
size=64   -> 64
size=240  -> 300
size=300  -> 300
size=480  -> 640
size=900  -> 640
```

Different requested sizes use distinct in-memory cache entries so a legacy Dial request cannot poison a larger Square request, or vice versa.

Binary responses include:

```text
Content-Type
Content-Length
X-Artwork-Width
X-Artwork-Height
```

This lets ESP32 clients remain on simple LAN HTTP.

## Supervisor configuration

Defaults:

```text
SPOTTY_HEALTH_INTERVAL     10 seconds
SPOTTY_HEALTH_TIMEOUT       2 seconds
SPOTTY_STARTUP_GRACE        5 seconds
SPOTTY_HEALTH_FAILURES      3
SPOTTY_MAX_RESTART_DELAY   30 seconds
SPOTTY_STABLE_UPTIME       60 seconds
```

Optional environment variables:

```text
SPOTTY_HOST
SPOTTY_PORT
SPOTIFY_CLIENT_ID
SPOTTY_HEALTH_INTERVAL
SPOTTY_HEALTH_TIMEOUT
SPOTTY_STARTUP_GRACE
SPOTTY_HEALTH_FAILURES
SPOTTY_MAX_RESTART_DELAY
SPOTTY_STABLE_UPTIME
```

The macOS installer copies supported values from its environment into the generated LaunchAgent.

Changing the port also changes the OAuth callback URI, so the matching loopback redirect must be configured in the Spotify developer app.

## Security model

The current LAN API has no client authentication.

Anyone who can reach port 8787 can issue playback/playlist commands and read Spotty-managed history. Run the service only on a trusted LAN and do not expose it directly to the public internet.

Never commit refresh tokens, access tokens, or a Spotify Client Secret.
