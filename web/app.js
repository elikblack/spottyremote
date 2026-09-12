import { SpotifyClient } from '../core/spotify-client.js';
import { normalizePlayback, estimatedProgress } from '../core/playback-state.js';
import { SpotifyBrowserAuth } from './browser-auth.js';
import { CANONICAL_ORIGIN, SPOTIFY_CLIENT_ID } from './config.js';

const $ = (id) => document.getElementById(id);

const els = {
  connectionStatus: $('connection-status'),
  setup: $('setup'),
  clientId: $('client-id'),
  redirectUri: $('redirect-uri'),
  connect: $('connect'),
  setupMessage: $('setup-message'),
  player: $('player'),
  artwork: $('artwork'),
  artworkEmpty: $('artwork-empty'),
  playbackLabel: $('playback-label'),
  trackName: $('track-name'),
  artistName: $('artist-name'),
  albumName: $('album-name'),
  progress: $('progress'),
  elapsed: $('elapsed'),
  duration: $('duration'),
  shuffle: $('shuffle'),
  previous: $('previous'),
  playPause: $('play-pause'),
  next: $('next'),
  repeat: $('repeat'),
  device: $('device'),
  refreshDevices: $('refresh-devices'),
  volume: $('volume'),
  volumeValue: $('volume-value'),
  deviceDetail: $('device-detail'),
  disconnect: $('disconnect'),
  playerMessage: $('player-message'),
};

const auth = new SpotifyBrowserAuth();
if (!auth.clientId) auth.clientId = SPOTIFY_CLIENT_ID;
const api = new SpotifyClient({ tokenProvider: auth });

let playback = normalizePlayback(null);
let devices = [];
let pollTimer = null;
let progressTimer = null;
let deviceTimer = null;
let seeking = false;
let changingVolume = false;

function formatTime(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '0:00';
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = String(total % 60).padStart(2, '0');
  return `${minutes}:${seconds}`;
}

function message(target, text = '') {
  target.textContent = text;
}

function setConnectedUi(connected) {
  els.setup.hidden = connected;
  els.player.hidden = !connected;
  els.connectionStatus.textContent = connected ? 'Connected' : 'Not connected';
  els.connectionStatus.classList.toggle('ok', connected);
}

function isLocalDevelopment() {
  return window.location.protocol === 'http:' && ['127.0.0.1', '[::1]', '::1'].includes(window.location.hostname);
}

function selectedDeviceId() {
  return els.device.value || playback.device?.id || null;
}

function currentDeviceRecord() {
  const id = selectedDeviceId();
  return devices.find((device) => device.id === id) || null;
}

function renderProgress() {
  if (seeking) return;
  const progressMs = estimatedProgress(playback);
  const durationMs = Math.max(0, playback.durationMs || 0);
  els.progress.max = String(Math.max(1, durationMs));
  els.progress.value = String(Math.min(progressMs, durationMs || 0));
  els.elapsed.textContent = formatTime(progressMs);
  els.duration.textContent = formatTime(durationMs);
}

function renderPlayback() {
  const item = playback.item;

  if (item?.artworkUrl) {
    els.artwork.src = item.artworkUrl;
    els.artwork.alt = item.album ? `${item.album} artwork` : `${item.name} artwork`;
    els.artwork.hidden = false;
    els.artworkEmpty.hidden = true;
  } else {
    els.artwork.removeAttribute('src');
    els.artwork.alt = '';
    els.artwork.hidden = true;
    els.artworkEmpty.hidden = false;
  }

  els.playbackLabel.textContent = playback.isPlaying ? 'NOW PLAYING' : 'PLAYBACK PAUSED';
  els.trackName.textContent = item?.name || 'Nothing playing';
  els.artistName.textContent = item?.artists?.join(', ') || 'Start playback on a Spotify Connect device.';
  els.albumName.textContent = item?.album || '';
  els.playPause.textContent = playback.isPlaying ? '❚❚' : '▶';
  els.playPause.setAttribute('aria-label', playback.isPlaying ? 'Pause' : 'Play');

  els.shuffle.classList.toggle('active', playback.shuffle);
  els.repeat.classList.toggle('active', playback.repeat !== 'off');
  els.repeat.textContent = playback.repeat === 'track' ? 'RPT 1' : playback.repeat === 'context' ? 'RPT ALL' : 'RPT';

  if (!changingVolume) {
    const volume = playback.device?.volume;
    if (volume == null) {
      els.volumeValue.textContent = '--';
    } else {
      els.volume.value = String(volume);
      els.volumeValue.textContent = `${volume}%`;
    }
  }

  const activeDevice = playback.device;
  els.deviceDetail.textContent = activeDevice
    ? `${activeDevice.name}${activeDevice.type ? ` · ${activeDevice.type}` : ''}`
    : 'No active playback device';

  const current = currentDeviceRecord();
  const supportsVolume = current ? current.supports_volume !== false : playback.device?.supportsVolume !== false;
  els.volume.disabled = !supportsVolume;

  renderProgress();
}

function renderDevices() {
  const currentValue = els.device.value;
  const preferred = playback.device?.id || currentValue;

  els.device.replaceChildren();

  if (!devices.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No Spotify Connect devices found';
    els.device.append(option);
    els.device.disabled = true;
    return;
  }

  els.device.disabled = false;
  for (const device of devices) {
    const option = document.createElement('option');
    option.value = device.id || '';
    option.textContent = `${device.is_active ? '● ' : ''}${device.name}${device.type ? ` · ${device.type}` : ''}`;
    if (device.id && device.id === preferred) option.selected = true;
    els.device.append(option);
  }
}

function handleLostSession(error) {
  if (auth.hasSession) return false;
  stopPolling();
  els.clientId.value = auth.clientId;
  setConnectedUi(false);
  message(els.setupMessage, error?.message || 'Spotify authorization ended. Reconnect Spotify.');
  return true;
}

async function refreshPlayback({ quiet = false } = {}) {
  try {
    const raw = await api.getPlaybackState();
    playback = normalizePlayback(raw);
    renderPlayback();
    if (!quiet) message(els.playerMessage, '');
  } catch (error) {
    if (handleLostSession(error)) return;
    if (!quiet) message(els.playerMessage, error.message);
  }
}

async function refreshDevices({ quiet = false } = {}) {
  try {
    const result = await api.getDevices();
    devices = result?.devices || [];
    renderDevices();
    renderPlayback();
    if (!quiet) message(els.playerMessage, '');
  } catch (error) {
    if (handleLostSession(error)) return;
    if (!quiet) message(els.playerMessage, error.message);
  }
}

function stopPolling() {
  clearInterval(pollTimer);
  clearInterval(progressTimer);
  clearInterval(deviceTimer);
  pollTimer = null;
  progressTimer = null;
  deviceTimer = null;
}

function startPolling() {
  stopPolling();
  refreshPlayback();
  refreshDevices({ quiet: true });
  pollTimer = setInterval(() => refreshPlayback({ quiet: true }), 10_000);
  progressTimer = setInterval(renderProgress, 500);
  deviceTimer = setInterval(() => refreshDevices({ quiet: true }), 60_000);
}

async function command(action, { refreshDelay = 350 } = {}) {
  message(els.playerMessage, '');
  try {
    await action();
    setTimeout(() => refreshPlayback({ quiet: true }), refreshDelay);
  } catch (error) {
    if (handleLostSession(error)) return;
    message(els.playerMessage, error.message);
  }
}

els.connect.addEventListener('click', async () => {
  message(els.setupMessage, '');
  const clientId = els.clientId.value.trim();
  if (!clientId) {
    message(els.setupMessage, 'Enter a Spotify Client ID first.');
    return;
  }
  auth.clientId = clientId;
  try {
    await auth.beginLogin();
  } catch (error) {
    message(els.setupMessage, error.message);
  }
});

els.playPause.addEventListener('click', () => {
  const deviceId = selectedDeviceId();
  command(() => playback.isPlaying ? api.pause(deviceId) : api.play(deviceId));
});

els.previous.addEventListener('click', () => command(() => api.previous(selectedDeviceId())));
els.next.addEventListener('click', () => command(() => api.next(selectedDeviceId())));

els.shuffle.addEventListener('click', () => {
  command(() => api.setShuffle(!playback.shuffle, selectedDeviceId()));
});

els.repeat.addEventListener('click', () => {
  const nextMode = playback.repeat === 'off' ? 'context' : playback.repeat === 'context' ? 'track' : 'off';
  command(() => api.setRepeat(nextMode, selectedDeviceId()));
});

els.progress.addEventListener('input', () => {
  seeking = true;
  els.elapsed.textContent = formatTime(Number(els.progress.value));
});

els.progress.addEventListener('change', () => {
  const position = Number(els.progress.value);
  seeking = false;
  command(() => api.seek(position, selectedDeviceId()), { refreshDelay: 500 });
});

els.volume.addEventListener('input', () => {
  changingVolume = true;
  els.volumeValue.textContent = `${els.volume.value}%`;
});

els.volume.addEventListener('change', () => {
  const volume = Number(els.volume.value);
  command(() => api.setVolume(volume, selectedDeviceId()), { refreshDelay: 500 });
  changingVolume = false;
});

els.device.addEventListener('change', () => {
  const id = els.device.value;
  if (!id) return;
  command(() => api.transferPlayback(id, { play: playback.isPlaying }), { refreshDelay: 800 });
});

els.refreshDevices.addEventListener('click', () => refreshDevices());

els.disconnect.addEventListener('click', () => {
  stopPolling();
  auth.disconnect();
  playback = normalizePlayback(null);
  devices = [];
  els.clientId.value = auth.clientId;
  setConnectedUi(false);
  message(els.setupMessage, 'Spotify session cleared.');
});

async function init() {
  els.redirectUri.textContent = auth.redirectUri;
  els.clientId.value = auth.clientId;

  if (window.location.protocol === 'file:') {
    setConnectedUi(false);
    message(els.setupMessage, 'Serve SpottyRemote over HTTP(S); Spotify OAuth cannot use a file:// URL.');
    els.connect.disabled = true;
    return;
  }

  if (!isLocalDevelopment() && window.location.origin !== CANONICAL_ORIGIN) {
    setConnectedUi(false);
    message(els.setupMessage, `Spotify authorization is enabled only at ${CANONICAL_ORIGIN}/`);
    els.connect.disabled = true;
    return;
  }

  try {
    await auth.handleCallback();
  } catch (error) {
    setConnectedUi(false);
    message(els.setupMessage, error.message);
    return;
  }

  if (!auth.configured || !auth.hasSession) {
    setConnectedUi(false);
    return;
  }

  setConnectedUi(true);
  startPolling();
}

init();
