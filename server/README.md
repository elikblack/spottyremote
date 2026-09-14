# Spotty local server

A small LAN-only Spotify control server for Spotty hardware. It uses only the Python standard library and is intended to run on an always-on Mac or other local computer.

## Requirements

- Python 3.8 or newer
- outbound HTTPS access to Spotify
- the server and Spotty hardware on the same trusted LAN

No pip packages are required.

## Spotify setup

Add this redirect URI to the existing Spotify developer app and save the app settings:

```text
http://127.0.0.1:8787/auth/callback
```

Spotify permits HTTP OAuth redirects for explicit loopback addresses such as `127.0.0.1`. Authorization should therefore be performed in a browser on the Mac that is running Spotty Server.

Spotty requests playback-control plus public/private playlist-modification scopes. If a server installation was authorized before playlist support was added, reconnect Spotify once so the stored refresh token receives the new playlist scopes.

## Run

From the repository root:

```bash
python3 server/spotty_server.py
```

Then, on that same Mac, open:

```text
http://127.0.0.1:8787/
```

Choose **Connect / reconnect Spotify** and complete Spotify authorization. OAuth tokens are stored locally in:

```text
server/.spotty_tokens.json
```

That file is ignored by Git and should not be copied into the repository.

## LAN access

The server listens on all network interfaces by default. Other devices on the LAN can use the Mac's local IP address or a working mDNS hostname, for example:

```text
http://192.168.1.50:8787/api/health
http://your-mac.local:8787/api/health
```

The first hardware test should use the numeric LAN IP address so hostname resolution is not another variable.

## API

```text
GET  /api/health
GET  /api/player
GET  /api/devices
GET  /api/artwork?item_type=track&track_id=...
POST /api/play
POST /api/pause
POST /api/playpause
POST /api/next
POST /api/previous
POST /api/volume?value=50
POST /api/transfer?device_id=...&play=true
POST /api/playlist/add?playlist_id=...&item_type=track&item_id=...
```

`/api/playlist/add` is deliberately generic. Hardware chooses the destination playlist and supplies the current Spotify item ID; the server owns authentication and translates that into Spotify's playlist API.

The API is deliberately small and returns JSON intended for simple hardware clients.

## Security model

This first version is LAN-only and does not authenticate clients. Anyone who can reach the server on port 8787 can issue playback or playlist-modification commands. Run it only on a trusted local network and do not expose port 8787 to the public internet.

If Spotty later needs remote access or untrusted-network use, add device authentication and HTTPS before exposing it.

## Configuration

Optional environment variables:

```text
SPOTTY_HOST          default: 0.0.0.0
SPOTTY_PORT          default: 8787
SPOTIFY_CLIENT_ID    defaults to the public Spotty Spotify Client ID
```

Changing the port also changes the OAuth callback URI, so the matching loopback redirect must be added to the Spotify developer app.
