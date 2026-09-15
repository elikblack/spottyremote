#!/usr/bin/env python3
"""Run Spotty Server with lightweight Spotify Web API request accounting.

This keeps the production server implementation untouched while exposing
GET /api/metrics for diagnosing Spotify request volume and 429s, plus a small
LAN status dashboard at GET /.
"""

import threading
import time
from collections import Counter, deque
from http.server import ThreadingHTTPServer

import spotty_server as core


_metrics_lock = threading.Lock()
_recent = deque()
_totals = Counter()
_status_totals = Counter()
_started_at = time.time()
_original_spotify_request = core.spotify_request


STATUS_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spotty Server Status</title>
<style>
  :root { color-scheme: dark; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #101214; color: #e7ebef; }
  main { width: min(1050px, calc(100% - 32px)); margin: 36px auto 64px; }
  header { display: flex; gap: 18px; justify-content: space-between; align-items: end; margin-bottom: 22px; }
  h1 { margin: 0; font-size: 28px; }
  h2 { margin: 0 0 14px; font-size: 16px; font-weight: 650; color: #f5f7f8; }
  p { margin: 0; }
  .muted { color: #8e989f; font-size: 13px; }
  .grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
  .card { background: #181c1f; border: 1px solid #293036; border-radius: 12px; padding: 18px; min-width: 0; }
  .wide { grid-column: 1 / -1; }
  .statrow { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  .stat { background: #111416; border-radius: 8px; padding: 12px; }
  .stat strong { display: block; font-size: 24px; margin-top: 3px; }
  .label { color: #8e989f; font-size: 12px; text-transform: uppercase; letter-spacing: .06em; }
  .good { color: #67d391; }
  .warn { color: #e8bf68; }
  .bad { color: #ef7474; }
  .kv { display: grid; grid-template-columns: 150px 1fr; gap: 7px 14px; font-size: 14px; }
  .kv div:nth-child(odd) { color: #8e989f; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 8px 7px; border-bottom: 1px solid #293036; }
  th:last-child, td:last-child { text-align: right; }
  th { color: #8e989f; font-weight: 500; }
  tr:last-child td { border-bottom: 0; }
  button, a.button { appearance: none; border: 1px solid #3a444b; border-radius: 8px; background: #23292e; color: #e7ebef; padding: 8px 11px; font: inherit; text-decoration: none; cursor: pointer; }
  button:hover, a.button:hover { background: #2b3338; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .devices { display: grid; gap: 8px; }
  .device { display: flex; justify-content: space-between; gap: 12px; background: #111416; border-radius: 8px; padding: 10px 12px; }
  .pill { font-size: 11px; border-radius: 999px; padding: 3px 7px; background: #293036; color: #aeb7bd; white-space: nowrap; }
  .pill.active { background: #173c28; color: #67d391; }
  code { color: #bac5cc; }
  @media (max-width: 720px) {
    header { align-items: start; flex-direction: column; }
    .grid { grid-template-columns: 1fr; }
    .wide { grid-column: auto; }
    .statrow { grid-template-columns: 1fr; }
    .kv { grid-template-columns: 110px 1fr; }
  }
</style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Spotty Server</h1>
      <p class="muted">LAN diagnostics · metrics refresh automatically · Spotify state refreshes only on request</p>
    </div>
    <div id="updated" class="muted">Loading…</div>
  </header>

  <section class="grid">
    <div class="card">
      <h2>Server</h2>
      <div class="kv">
        <div>Status</div><div id="server-status">Loading…</div>
        <div>Spotify auth</div><div id="auth-status">Loading…</div>
        <div>Uptime</div><div id="uptime">—</div>
        <div>Granted scopes</div><div id="scopes">—</div>
      </div>
      <div class="actions">
        <a class="button" href="/auth/login">Reconnect Spotify</a>
        <a class="button" href="/api/health">Raw health</a>
        <a class="button" href="/api/metrics">Raw metrics</a>
      </div>
      <p class="muted" style="margin-top:10px">OAuth reconnect must be opened on the Mac running Spotty Server because the callback uses 127.0.0.1.</p>
    </div>

    <div class="card">
      <h2>Spotify API traffic</h2>
      <div class="statrow">
        <div class="stat"><span class="label">Last 60s</span><strong id="req60">—</strong></div>
        <div class="stat"><span class="label">Since start</span><strong id="reqtotal">—</strong></div>
        <div class="stat"><span class="label">429s</span><strong id="rate429">—</strong></div>
      </div>
    </div>

    <div class="card">
      <h2>Requests by endpoint</h2>
      <table><thead><tr><th>Endpoint</th><th>Last 60s</th><th>Total</th></tr></thead><tbody id="endpoint-body"></tbody></table>
    </div>

    <div class="card">
      <h2>Responses by status</h2>
      <table><thead><tr><th>Status</th><th>Last 60s</th><th>Total</th></tr></thead><tbody id="status-body"></tbody></table>
    </div>

    <div class="card">
      <h2>Player</h2>
      <div class="kv">
        <div>Playback</div><div id="playback">Not loaded</div>
        <div>Track</div><div id="track">—</div>
        <div>Artist</div><div id="artist">—</div>
        <div>Device</div><div id="player-device">—</div>
        <div>Volume</div><div id="volume">—</div>
      </div>
      <div class="actions"><button id="refresh-player">Refresh player</button></div>
    </div>

    <div class="card">
      <h2>Spotify Connect devices</h2>
      <div id="devices" class="devices"><span class="muted">Not loaded</span></div>
      <div class="actions"><button id="refresh-devices">Refresh devices</button></div>
    </div>

    <div class="card wide">
      <h2>Useful endpoints</h2>
      <div class="kv">
        <div>Health</div><div><code>/api/health</code></div>
        <div>Metrics</div><div><code>/api/metrics</code></div>
        <div>Player</div><div><code>/api/player</code></div>
        <div>Devices</div><div><code>/api/devices</code></div>
        <div>Artwork</div><div><code>/api/artwork?item_type=track&amp;track_id=…</code></div>
      </div>
    </div>
  </section>
</main>
<script>
const $ = id => document.getElementById(id);

function fmtUptime(seconds) {
  seconds = Math.max(0, Number(seconds) || 0);
  const d = Math.floor(seconds / 86400); seconds %= 86400;
  const h = Math.floor(seconds / 3600); seconds %= 3600;
  const m = Math.floor(seconds / 60); const s = Math.floor(seconds % 60);
  return [d ? `${d}d` : '', h || d ? `${h}h` : '', m || h || d ? `${m}m` : '', `${s}s`].filter(Boolean).join(' ');
}

async function getJson(path) {
  const r = await fetch(path, { cache: 'no-store' });
  const data = await r.json();
  if (!r.ok || data?.ok === false) throw new Error(data?.error || `HTTP ${r.status}`);
  return data;
}

function fillTable(body, recent = {}, total = {}) {
  const keys = [...new Set([...Object.keys(recent), ...Object.keys(total)])].sort();
  body.replaceChildren();
  if (!keys.length) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="3" class="muted">No requests yet</td>';
    body.append(tr);
    return;
  }
  for (const key of keys) {
    const tr = document.createElement('tr');
    for (const value of [key, recent[key] || 0, total[key] || 0]) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    body.append(tr);
  }
}

async function refreshCore() {
  try {
    const [health, metrics] = await Promise.all([getJson('/api/health'), getJson('/api/metrics')]);
    $('server-status').textContent = 'Running'; $('server-status').className = 'good';
    $('auth-status').textContent = health.authorized ? 'Authorized' : 'Not authorized';
    $('auth-status').className = health.authorized ? 'good' : 'warn';
    $('scopes').textContent = health.scope || 'none';
    $('uptime').textContent = fmtUptime(metrics.uptime_seconds);
    $('req60').textContent = metrics.spotify_requests_last_60s;
    $('reqtotal').textContent = metrics.total_requests_since_start;
    const rate429 = Number(metrics.total_by_status?.['429'] || 0);
    $('rate429').textContent = rate429;
    $('rate429').className = rate429 ? 'bad' : 'good';
    fillTable($('endpoint-body'), metrics.last_60s_by_endpoint, metrics.total_by_endpoint);
    fillTable($('status-body'), metrics.last_60s_by_status, metrics.total_by_status);
    $('updated').textContent = `Updated ${new Date().toLocaleTimeString()}`;
  } catch (e) {
    $('server-status').textContent = e.message; $('server-status').className = 'bad';
  }
}

async function refreshPlayer() {
  const button = $('refresh-player'); button.disabled = true;
  try {
    const p = await getJson('/api/player');
    if (!p.active) {
      $('playback').textContent = 'No active player'; $('playback').className = 'muted';
      $('track').textContent = $('artist').textContent = $('player-device').textContent = $('volume').textContent = '—';
      return;
    }
    $('playback').textContent = p.is_playing ? 'Playing' : 'Paused';
    $('playback').className = p.is_playing ? 'good' : '';
    $('track').textContent = p.track_name || '—';
    $('artist').textContent = p.artist_name || '—';
    $('player-device').textContent = p.device_name || '—';
    $('volume').textContent = Number.isInteger(p.volume_percent) ? `${p.volume_percent}%` : 'Unavailable';
  } catch (e) {
    $('playback').textContent = e.message; $('playback').className = 'bad';
  } finally { button.disabled = false; refreshCore(); }
}

async function refreshDevices() {
  const button = $('refresh-devices'); button.disabled = true;
  try {
    const result = await getJson('/api/devices');
    const list = result.data?.devices || [];
    $('devices').replaceChildren();
    if (!list.length) { $('devices').innerHTML = '<span class="muted">No devices reported</span>'; return; }
    for (const d of list) {
      const row = document.createElement('div'); row.className = 'device';
      const name = document.createElement('span'); name.textContent = `${d.name || 'Unnamed'}${d.type ? ` · ${d.type}` : ''}`;
      const pill = document.createElement('span'); pill.className = `pill${d.is_active ? ' active' : ''}`; pill.textContent = d.is_active ? 'ACTIVE' : 'available';
      row.append(name, pill); $('devices').append(row);
    }
  } catch (e) {
    $('devices').innerHTML = ''; const span = document.createElement('span'); span.className = 'bad'; span.textContent = e.message; $('devices').append(span);
  } finally { button.disabled = false; refreshCore(); }
}

$('refresh-player').addEventListener('click', refreshPlayer);
$('refresh-devices').addEventListener('click', refreshDevices);
refreshCore();
setInterval(refreshCore, 5000);
</script>
</body>
</html>"""


def _trim_recent(now):
    cutoff = now - 60.0
    while _recent and _recent[0][0] < cutoff:
        _recent.popleft()


def instrumented_spotify_request(path, method="GET", query=None, body=None, retry_auth=True):
    key = "{} {}".format(method, path)
    status = "exception"

    try:
        result = _original_spotify_request(path, method, query, body, retry_auth)
        status = str(result[0])
        return result
    finally:
        finished = time.time()
        with _metrics_lock:
            _totals[key] += 1
            _status_totals[status] += 1
            _recent.append((finished, key, status))
            _trim_recent(finished)


core.spotify_request = instrumented_spotify_request


def metrics_snapshot():
    now = time.time()
    with _metrics_lock:
        _trim_recent(now)
        recent_by_endpoint = Counter(item[1] for item in _recent)
        recent_by_status = Counter(item[2] for item in _recent)
        return {
            "ok": True,
            "service": "spotty",
            "uptime_seconds": int(now - _started_at),
            "spotify_requests_last_60s": len(_recent),
            "last_60s_by_endpoint": dict(sorted(recent_by_endpoint.items())),
            "last_60s_by_status": dict(sorted(recent_by_status.items())),
            "total_requests_since_start": sum(_totals.values()),
            "total_by_endpoint": dict(sorted(_totals.items())),
            "total_by_status": dict(sorted(_status_totals.items())),
        }


class InstrumentedSpottyHandler(core.SpottyHandler):
    def do_GET(self):
        parsed = self.parsed_url()
        if parsed.path == "/":
            self.send_html(200, STATUS_PAGE)
            return
        if parsed.path == "/api/metrics":
            self.send_json(200, metrics_snapshot())
            return
        super().do_GET()


if __name__ == "__main__":
    print("Spotty Server (instrumented)")
    print("  Listening: http://{}:{}".format(core.HOST, core.PORT))
    print("  Dashboard: http://127.0.0.1:{}/".format(core.PORT))
    print("  Metrics:   http://127.0.0.1:{}/api/metrics".format(core.PORT))
    print("  Token file: {}".format(core.TOKEN_FILE))
    print()
    print("LAN clients may use this server without authentication. Keep it on a trusted network.")

    server = ThreadingHTTPServer((core.HOST, core.PORT), InstrumentedSpottyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Spotty Server.")
    finally:
        server.server_close()
