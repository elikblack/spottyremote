# Artwork size requests: design and implementation handoff

Last reviewed: September 20, 2026

## Purpose

This document is a self-contained handoff for adding client-requested artwork sizing to SpottyServer.

The immediate motivation is SpottySquare. SpottyDial has a 240 x 240 display and is well served by the server's current approximately-300-pixel artwork selection. SpottySquare has a 480 x 480 display and should be able to ask for a larger source image without forcing every hardware client to receive larger artwork.

The feature belongs in SpottyServer because artwork acquisition is already a generic server primitive. Hardware should state the image resolution it wants; the server should continue to own Spotify metadata lookup, CDN access, payload limits, and caching.

This should remain a generic capability. Do not add Square-specific or Dial-specific routes or policy.

## Current production architecture

Production is layered:

```text
spotty_supervisor.py
  -> spotty_history_server.py
      -> spotty_instrumented.py
          -> spotty_server.py
```

The wrapper layers import the core implementation, so the artwork feature should be implemented in:

```text
server/spotty_server.py
```

No special changes should be required in the history, instrumentation, or supervisor layers unless testing uncovers an unexpected interception of the artwork route.

After deployment, the normal production restart remains:

```bash
cd ~/Projects/spottyremote
git pull --ff-only
python3 server/install_launch_agent.py
```

Do not start a second standalone server next to the launchd/supervisor-managed service.

## Current artwork behavior

Current route:

```text
GET /api/artwork?item_type=track|episode&track_id=<spotify-id>
```

The core server currently:

1. fetches Spotify metadata for the track or episode;
2. obtains Spotify's available image variants;
3. chooses the image whose width is numerically closest to 300 pixels;
4. downloads that image from Spotify's CDN;
5. returns the original compressed bytes without resizing or recompression;
6. stores the result in a small in-memory LRU cache.

Relevant current constants:

```text
ARTWORK_CACHE_ITEMS = 12
ARTWORK_MAX_BYTES   = 2 MiB
```

Binary responses already contain:

```text
Content-Type
Content-Length
X-Artwork-Width
X-Artwork-Height
Cache-Control: public, max-age=86400
```

The current in-memory cache key is effectively only:

```text
item_type:item_id
```

That cache key becomes incorrect as soon as more than one artwork size can be requested.

SpottyDial currently requests the route without a size parameter. Its firmware accepts compressed artwork up to 768 KiB and expects the existing roughly-300-pixel behavior.

## Desired API

Extend the existing route rather than creating a new one:

```text
GET /api/artwork?item_type=track&track_id=<spotify-id>&size=480
```

### Meaning of `size`

`size` is a **requested source-image width in pixels**, not a promise that the returned bitmap will have exactly that width.

SpottyServer should continue to return one of Spotify's original image variants unchanged. It should not resize or recompress the image in this feature.

The actual returned dimensions remain authoritative through:

```text
X-Artwork-Width
X-Artwork-Height
```

This distinction matters. Spotify commonly offers a few discrete artwork variants rather than arbitrary dimensions. A client asking for 480 pixels may therefore receive a 640 x 640 source image and scale it locally to 480 x 480.

### Backward compatibility

If `size` is omitted, preserve today's behavior exactly:

```text
no size parameter -> choose image nearest 300 px
```

This means existing SpottyDial firmware does not need to change and should continue receiving the same class of artwork it receives today.

A request with an explicit size should opt into the new selection policy described below.

## Recommended source-selection policy

For an explicit `size=N`, favor image quality while avoiding unnecessarily huge sources:

1. collect image entries with a URL and a usable positive integer width;
2. if one or more images are at least `N` pixels wide, choose the **smallest width that is >= N**;
3. otherwise choose the **largest available width**;
4. if width metadata is unavailable for every otherwise-valid image, fall back deterministically to the first valid image.

This produces useful behavior for the common Spotify 64 / 300 / 640 image set:

```text
size=64   -> 64
size=100  -> 300
size=240  -> 300
size=300  -> 300
size=480  -> 640
size=640  -> 640
size=900  -> 640
```

The reason to prefer "smallest source at least as large as requested" over pure nearest-distance selection is to avoid preventable upscaling. A 480-pixel display should not receive a 300-pixel image merely because 300 happens to be numerically closer than a larger source in an unusual image set.

The no-parameter path should retain the old "nearest 300" behavior separately for compatibility.

## Parameter validation

Parse `size` in `handle_artwork()`.

Recommended accepted range:

```text
64 <= size <= 2048
```

Rationale:

- 64 covers Spotify's common small thumbnail class.
- 2048 is comfortably above the hardware use cases while preventing nonsense values from becoming unbounded cache-key cardinality.
- The value is only a selection hint. A request for 2048 does not create or synthesize a 2048-pixel image.

Reject malformed or out-of-range values with HTTP 400 and a simple JSON error, for example:

```json
{"ok":false,"error":"size must be an integer from 64 to 2048"}
```

Do not silently coerce invalid values. A hardware bug should be visible rather than producing surprising cache behavior.

If the parameter is absent, pass a sentinel such as `None` so the legacy selection path is unambiguous.

## Suggested internal API changes

A clean shape is:

```python
def _pick_artwork(images, target_size=None):
    ...

def fetch_artwork(item_type, item_id, target_size=None):
    ...

def handle_artwork(self, parsed):
    ...
```

Suggested semantics:

- `target_size is None`: current nearest-300 selection.
- integer `target_size`: new quality-first explicit-size selection.

Keep the selection rule in `_pick_artwork()`, not in the HTTP handler, so it is directly unit-testable.

Keep HTTP parsing/validation in `handle_artwork()`.

## Cache design

### Required change

The artwork cache must distinguish different requested sizes.

The simplest safe key is:

```text
(item_type, item_id, target_size)
```

or an equivalent string representation.

Use a stable sentinel for the legacy/no-size request, for example:

```text
("track", "<id>", None)
```

This avoids the critical failure mode where a Dial requests an item first, caches the 300-pixel image, and a later Square request for 480 receives that cached 300-pixel image.

### Why not key only by actual selected width?

Keying by the selected Spotify source width sounds attractive because several requested sizes may map to the same source. However, the server cannot know which source width would be selected until it has the Spotify image metadata. The current cache exists partly to avoid repeating that metadata/CDN work.

A second metadata cache could normalize requests to source-width keys, but that is unnecessary complexity for the first version. The expected clients will use a tiny number of stable target sizes.

Start with the requested target in the cache key.

### Cache capacity

Keep `ARTWORK_CACHE_ITEMS = 12` initially.

Multiple target sizes mean one album can occupy more than one cache entry. That is acceptable for the expected Dial + Square use case and should be measured before increasing the cache.

If cache churn becomes visible later, possible improvements are:

- raise the item count;
- cache Spotify image metadata separately;
- deduplicate payloads by source URL/width;
- track memory bytes rather than only item count.

None of those are necessary for the first implementation.

## No server-side resizing in this feature

Do not add Pillow, ImageMagick, ffmpeg, CoreGraphics bindings, or subprocess-based image conversion as part of this change.

SpottyServer currently has a valuable property:

```text
Python 3.8+ standard library only
```

Exact resizing would introduce a dependency and a new class of CPU, memory, image-codec, and deployment concerns.

The useful first feature is source selection, not image transformation.

If exact server-side resizing becomes desirable later, treat it as a separate design decision with explicit format/quality/cache semantics. The current API can remain compatible because `size` is already documented as a requested target rather than an exact-output guarantee.

## Client expectations

### SpottyDial

No firmware change is required.

It should continue requesting:

```text
/api/artwork?item_type=...&track_id=...
```

with no `size` parameter.

That preserves its current roughly-300-pixel source selection and avoids increasing its network/memory cost.

### SpottySquare

The initial Square client should request:

```text
/api/artwork?item_type=...&track_id=...&size=480
```

With Spotify's common image set this will usually return a 640 x 640 source.

The Square firmware should always trust `X-Artwork-Width` and `X-Artwork-Height` rather than assuming 480 x 480. It should scale/crop during decode/render as appropriate for the final UI.

The server-wide payload ceiling remains 2 MiB. The Square client needs its own defensible compressed-artwork ceiling and allocation strategy. Do not blindly inherit SpottyDial's 768 KiB firmware cap if real 640-pixel sources demonstrate that a larger ceiling is useful. Measure actual payloads first.

### Future clients

The same mechanism naturally supports small thumbnails, secondary displays, or larger screens without changing the server route.

Examples:

```text
size=64
size=240
size=480
size=800
```

The server remains ignorant of which hardware model is asking.

## HTTP and cache behavior

Because `size` is part of the query string, distinct requested sizes already have distinct URLs for ordinary HTTP caches.

Continue returning:

```text
Cache-Control: public, max-age=86400
```

Continue returning actual dimensions in the existing custom headers.

Do not add a new MIME type or wrap artwork bytes in JSON.

A successful response remains raw image bytes.

## Error behavior

Keep existing artwork error behavior:

- invalid Spotify item ID: HTTP 400
- invalid item type: HTTP 400
- invalid size: HTTP 400
- metadata/CDN failure: HTTP 502 through the existing artwork exception path
- source payload larger than `ARTWORK_MAX_BYTES`: HTTP 502 through the existing exception path

Do not fall back to an arbitrary cached image from another size request when the requested variant fails.

A client may decide to retry without `size` or show a placeholder, but that is client policy.

## Tests to add

Create:

```text
server/tests/test_artwork.py
```

Use the existing unittest style in `server/tests`: import `spotty_server`, monkeypatch module-level functions, instantiate handlers with `object.__new__`, and capture `send_json` / `send_bytes`.

At minimum cover the following.

### Selection unit tests

Test `_pick_artwork()` directly with a representative list:

```text
64, 300, 640
```

Required cases:

- no target keeps legacy nearest-300 behavior;
- `size=64` selects 64;
- `size=240` selects 300;
- `size=300` selects 300;
- `size=480` selects 640;
- `size=900` selects 640;
- invalid/missing image records are ignored;
- all-missing-width candidates still produce a deterministic fallback.

### Handler tests

Monkeypatch `fetch_artwork` and verify:

- omitted `size` calls it with legacy/no-size semantics;
- valid `size=480` passes integer 480;
- non-integer size returns 400;
- below-minimum size returns 400;
- above-maximum size returns 400;
- successful binary response retains `X-Artwork-Width` and `X-Artwork-Height`.

### Cache-separation test

Verify that the same Spotify item requested with different explicit target sizes can resolve to different cached results.

The test should fail if the cache key accidentally ignores target size.

Clear/restore the module's artwork cache around this test so test ordering cannot contaminate it.

### Regression test

Verify that a request without `size` still follows the pre-feature 300-pixel selection behavior.

This is the important SpottyDial compatibility test.

## Documentation changes required with implementation

Update both:

```text
README.md
server/README.md
```

Change the route documentation to show the optional parameter:

```text
GET /api/artwork?item_type=track|episode&track_id=<spotify-id>&size=<optional-target-width>
```

Document clearly:

- omitted size preserves current near-300 behavior;
- explicit size chooses an available Spotify source suitable for that target;
- the server does not resize;
- returned dimensions are in the existing headers.

Do not describe `size=480` as guaranteeing 480 x 480 output.

## Suggested implementation sequence

1. Add tests for the desired selector behavior.
2. Change `_pick_artwork()` to accept the optional target.
3. Change `fetch_artwork()` to accept the target and include it in its cache key.
4. Parse and validate `size` in `handle_artwork()`.
5. Pass the target into `fetch_artwork()`.
6. Update root and server documentation.
7. Run the full server unit-test suite.
8. Deploy/restart the production server through `install_launch_agent.py`.
9. Verify the old no-size URL still returns the expected 300-class artwork.
10. Verify a `size=480` request returns the larger source when available.
11. Only after the server behavior is confirmed, point SpottySquare at `size=480`.

## Manual verification

For one known track ID, compare headers for the two routes:

```text
/api/artwork?item_type=track&track_id=<id>
/api/artwork?item_type=track&track_id=<id>&size=480
```

Expected with a typical Spotify image set:

```text
legacy request:
X-Artwork-Width: 300
X-Artwork-Height: 300

size=480 request:
X-Artwork-Width: 640
X-Artwork-Height: 640
```

Also request them in the reverse order. The result must be identical regardless of which size populated the in-memory cache first. That is the practical test for the cache-key fix.

## Things not to change while implementing this

Keep this feature narrow.

Do not combine it with:

- SpottySquare firmware work;
- player-cache behavior;
- artwork prefetching;
- disk caching;
- history changes;
- server-side image resizing;
- a dependency-management system;
- SpottyDial firmware changes;
- changes to Spotify authentication.

The server is already functioning in production. A small generic extension with tests is preferable to turning artwork sizing into a server rewrite.

## Future possibilities, explicitly out of scope

These may become useful later but are not requirements for this feature:

- exact server-side resize/crop;
- JPEG quality negotiation;
- WebP conversion;
- disk-persistent artwork cache;
- metadata cache for image variant lists;
- ETag / conditional requests;
- byte-budgeted LRU caching;
- per-client prefetch policy;
- returning dominant colors or palettes with artwork metadata.

If any of those are pursued, preserve the core principle established here: the hardware asks for a generic artwork capability; SpottyServer handles Spotify/CDN mechanics; device-specific UI policy stays on the device.

## Definition of done

The feature is complete when all of the following are true:

- old artwork URLs behave as before;
- `size` is optional and validated;
- explicit size requests select an appropriate Spotify source variant;
- different size requests cannot poison each other's cache results;
- actual response dimensions remain exposed in headers;
- no new runtime dependency is introduced;
- server tests cover selection, validation, cache separation, and legacy behavior;
- root and server API documentation are updated;
- production server is restarted through the managed launchd/supervisor path and manually verified;
- SpottyDial requires no firmware change.

At that point SpottySquare can safely request `size=480` as part of its artwork implementation without coupling the server to SpottySquare itself.
