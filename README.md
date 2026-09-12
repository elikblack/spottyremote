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
  app.js              reference web remote
  styles.css          reference UI
index.html             GitHub Pages entry point
```

`SpotifyClient` only expects a token provider exposing `getToken(forceRefresh)`. That boundary is deliberate: a future ESP32 implementation can provide tokens from NVS and use the same conceptual API without inheriting browser storage or UI code.

## Web remote setup

1. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Enable Web API access.
3. Add the exact URL shown by SpottyRemote under **Redirect URI** to the app's redirect URI allowlist.
4. Open SpottyRemote, paste the app's **Client ID**, and choose **Connect Spotify**.
5. Authorize the requested playback scopes.

The browser version uses Authorization Code with PKCE, so it does not use or store a Spotify Client Secret. The Client ID and OAuth tokens are stored in local browser storage.

For GitHub Pages the redirect URI will normally be:

```text
https://elikblack.github.io/spottyremote/
```

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

Playback state is polled while progress is interpolated locally between API responses, so the UI does not need to hammer Spotify just to move a progress bar.

## Direction

Likely next targets:

- CrowPanel 1.28-inch ESP32-S3 rotary display
- alternate physical remotes
- a small shared state/command vocabulary for integration with PorchPilot

The hardware-facing layers should remain separate from the Spotify core: knobs, touchscreens, LEDs and display libraries belong to targets, not to the API client.
