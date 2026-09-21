# Artwork size requests: design and implementation handoff

Last reviewed: September 20, 2026

## Purpose

SpottyServer owns Spotify artwork lookup and CDN access. Hardware clients should state the source-image width they want, and the server should choose an appropriate Spotify-provided image without resizing or recompressing it.

The immediate clients are:

```text
SpottyDial    240 x 240 display -> requests size=300
SpottySquare  480 x 480 display -> requests size=480
```

The route remains generic. There are no Dial-specific or Square-specific server endpoints or policies.

## Production architecture

```text
spotty_supervisor.py
  -> spotty_history_server.py
      -> spotty_instrumented.py
          -> spotty_server.py
```

Artwork behavior belongs in:

```text
server/spotty_server.py
```

The wrapper layers import the core implementation, so no special history/instrumentation/supervisor artwork code is required.

## API

```text
GET /api/artwork?item_type=track|episode&track_id=<spotify-id>&size=<target-width>
```

`size` is required and means **requested source-image width in pixels**.

Accepted range:

```text
64 <= size <= 2048
```

Malformed, missing, or out-of-range values return HTTP 400.

The value is a source-selection hint, not an exact-output promise. SpottyServer continues to return one of Spotify's original compressed image variants unchanged.

Actual returned dimensions remain authoritative through:

```text
X-Artwork-Width
X-Artwork-Height
```

For example, a client asking for `size=480` will commonly receive a 640 x 640 Spotify source and scale it locally.

## Source-selection policy

For `size=N`:

1. collect image entries with a URL and a usable positive integer width;
2. if one or more images are at least `N` pixels wide, choose the **smallest width >= N**;
3. otherwise choose the **largest available width**;
4. if all otherwise-valid images lack width metadata, use the first valid image deterministically.

For a common Spotify 64 / 300 / 640 image set:

```text
size=64   -> 64
size=100  -> 300
size=240  -> 300
size=300  -> 300
size=480  -> 640
size=640  -> 640
size=900  -> 640
```

The quality-first rule deliberately avoids preventable upscaling. A 480 px display should not receive a 300 px source merely because 300 happens to be numerically closer than 640.

## Why size is explicit

Earlier design notes considered preserving a legacy no-size path for SpottyDial. That is no longer the preferred architecture.

Each hardware client now states its artwork requirement explicitly:

```text
SpottyDial:
  /api/artwork?...&size=300

SpottySquare:
  /api/artwork?...&size=480
```

This keeps the server contract uniform and avoids a compatibility branch whose only purpose would be remembering what one older client happened to need.

Device policy stays on the device; Spotify image-selection mechanics stay on the server.

## Cache behavior

Artwork cache entries distinguish requested target sizes.

Conceptually:

```text
(item_type, item_id, target_size)
```

This prevents one client from poisoning another client's result. A cached 300-class Dial image cannot satisfy a later 480 request from Square.

Current cache limits remain:

```text
ARTWORK_CACHE_ITEMS = 12
ARTWORK_MAX_BYTES   = 2 MiB
```

The first implementation intentionally does not add a metadata cache or payload deduplication. Those are possible later optimizations if measured cache churn warrants them.

## No server-side resizing

This feature does not add Pillow, ImageMagick, ffmpeg, CoreGraphics bindings, or any other image-processing dependency.

SpottyServer retains its useful property:

```text
Python 3.8+ standard library only
```

The server chooses a Spotify source and returns its original bytes.

Exact resize/crop, quality negotiation, WebP conversion, or other transformation would be a separate feature with explicit format and cache semantics.

## Client expectations

### SpottyDial

The Dial requests:

```text
/api/artwork?item_type=...&track_id=...&size=300
```

Its current compressed-artwork ceiling remains 768 KiB.

### SpottySquare

Square should request:

```text
/api/artwork?item_type=...&track_id=...&size=480
```

With Spotify's common image set this will often return a 640 x 640 source.

Square should trust `X-Artwork-Width` and `X-Artwork-Height` and scale/crop locally during decode/render.

Do not blindly inherit Dial's 768 KiB compressed payload ceiling. Measure real 640 px artwork payloads on the Square hardware before choosing its client-side ceiling.

### Future clients

The same route naturally supports other display classes:

```text
size=64
size=240
size=480
size=800
```

The server does not need to know which model is asking.

## HTTP behavior

Successful artwork responses remain raw image bytes and continue to include:

```text
Content-Type
Content-Length
X-Artwork-Width
X-Artwork-Height
Cache-Control: public, max-age=86400
```

Because size is part of the query string, distinct target sizes already have distinct URLs for ordinary HTTP caches.

## Error behavior

- invalid Spotify item ID: HTTP 400
- invalid item type: HTTP 400
- missing/invalid/out-of-range size: HTTP 400
- metadata/CDN failure: HTTP 502 through the existing artwork exception path
- source payload larger than `ARTWORK_MAX_BYTES`: HTTP 502

Do not silently fall back to artwork cached for another requested size.

## Tests

`server/tests/test_artwork.py` should cover:

- `size=64` selects 64;
- `size=240` selects 300;
- `size=300` selects 300;
- `size=480` selects 640;
- `size=900` selects 640;
- invalid/missing image records are ignored;
- all-missing-width candidates use deterministic fallback;
- missing size returns 400;
- non-integer size returns 400;
- below-minimum and above-maximum sizes return 400;
- valid size is passed to `fetch_artwork()`;
- binary responses retain actual dimension headers;
- different requested target sizes do not share the wrong cached result.

The existing server test style uses `unittest`, monkeypatches module-level functions, and constructs handlers with `object.__new__`.

## Deployment

Normal production deployment remains:

```bash
cd ~/Projects/spottyremote
git pull --ff-only
python3 -m unittest discover -s server/tests -p 'test_*.py'
python3 server/install_launch_agent.py
```

Do not start a second standalone server beside the launchd/supervisor-managed service.

## Manual verification

Using one known track ID:

```text
/api/artwork?item_type=track&track_id=<id>&size=300
/api/artwork?item_type=track&track_id=<id>&size=480
```

Expected with a typical Spotify image set:

```text
size=300:
X-Artwork-Width: 300
X-Artwork-Height: 300

size=480:
X-Artwork-Width: 640
X-Artwork-Height: 640
```

Request the URLs in both orders. Results must not depend on which target size populated the in-memory cache first.

Also verify that omitting `size` returns HTTP 400. That confirms clients are using the explicit contract rather than relying on a server-side default.

## Scope boundaries

Keep this feature narrow. Do not combine it with:

- server-side image resizing;
- artwork prefetching;
- disk caching;
- player-cache behavior;
- history changes;
- Spotify authentication changes;
- a dependency-management system.

SpottyDial's only required firmware change is to request `size=300`. SpottySquare should request `size=480` when its artwork implementation lands.

## Definition of done

The feature is complete when:

- artwork clients provide an explicit validated target size;
- explicit size requests select the appropriate Spotify source variant;
- different requested sizes cannot poison each other's cache results;
- actual response dimensions remain exposed in headers;
- no new runtime dependency is introduced;
- server tests cover selection, validation, cache separation, and missing-size behavior;
- root/server/client documentation reflects the explicit-size contract;
- SpottyDial requests `size=300`;
- SpottySquare is ready to request `size=480`;
- production is restarted through the managed launchd/supervisor path and manually verified.
