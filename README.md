# SpottyRemote

Reusable Spotify remote-control core plus reference front ends.

The project is intentionally split so Spotify behavior is not tied to a particular display, knob, or microcontroller. The first front end is a browser remote; ESP32/CrowPanel targets can reuse the same state and command model later.

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
index.html             GitHub Pages entry point
```

`SpotifyClient` only expects a token provider exposing `getToken(forceRefresh)`. That boundary is deliberate: a future ESP32 implementation can provide tokens from NVS and use the same conceptual API without inheriting browser storage or UI code.

## Web remote

Canonical deployment:

```text
https://spotty.elistuff.com/
```

The Spotify developer app is configured for that exact redirect URI. The browser front end refuses production authorization from other origins, while still allowing explicit `127.0.0.1` loopback development.

The browser version uses Authorization Code with PKCE. No Spotify Client Secret is used or required. The configured Client ID is public by design and lives in `web/config.js`.

OAuth access and refresh tokens are stored in browser `localStorage` on the dedicated `spotty.elistuff.com` origin. Do not add third-party scripts to the production page without considering that they would execute with access to that origin's storage.

For local development, serve the repository over HTTP rather than opening `index.html` as a `file://` URL. Spotify permits HTTP redirect URIs only for explicit loopback addresses such as `127.0.0.1`; `localhost` is not accepted.

## Current controls

- read current track, progress and active device
- play / pause
- previous / next
- seek
- volume
- shuffle
- repeat mode
- enumerate Spotify Connect devices
- transfer playback between devices

Playback state is polled every 10 seconds while progress is interpolated locally between API responses. Device discovery is refreshed every 60 seconds. The API client respects Spotify `429` responses and waits for `Retry-After`, with bounded exponential backoff when that header is unavailable.

## Security rules

- Never commit a Spotify Client Secret.
- Never commit access tokens or refresh tokens.
- The Client ID is not a secret and may be public.
- Production OAuth uses PKCE over HTTPS.
- Keep production dependencies minimal; a script running on the SpottyRemote origin can access the browser's stored tokens.
- If Spotify invalidates or expires a refresh token, the browser session is cleared and the user is asked to authorize again.

## Direction

Likely next targets:

- CrowPanel 1.28-inch ESP32-S3 rotary display
- alternate physical remotes
- a small shared state/command vocabulary for integration with PorchPilot

The hardware-facing layers should remain separate from the Spotify core: knobs, touchscreens, LEDs and display libraries belong to targets, not to the API client.
