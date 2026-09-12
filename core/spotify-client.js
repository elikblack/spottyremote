export class SpotifyApiError extends Error {
  constructor(message, { status = 0, body = null } = {}) {
    super(message);
    this.name = 'SpotifyApiError';
    this.status = status;
    this.body = body;
  }
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function queryString(params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value));
    }
  }
  const text = query.toString();
  return text ? `?${text}` : '';
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function retryDelayMs(response, attempt) {
  const retryAfter = Number(response.headers.get('retry-after'));
  if (Number.isFinite(retryAfter) && retryAfter >= 0) {
    return retryAfter * 1000;
  }

  const exponentialSeconds = Math.min(2 ** attempt, 30);
  return exponentialSeconds * 1000 + Math.floor(Math.random() * 250);
}

export class SpotifyClient {
  constructor({
    tokenProvider,
    fetchImpl = globalThis.fetch?.bind(globalThis),
    baseUrl = 'https://api.spotify.com/v1',
    maxRateRetries = 3,
  }) {
    if (!tokenProvider?.getToken) {
      throw new TypeError('SpotifyClient requires a tokenProvider with getToken(forceRefresh).');
    }
    if (!fetchImpl) {
      throw new TypeError('SpotifyClient requires fetch.');
    }

    this.tokenProvider = tokenProvider;
    this.fetch = fetchImpl;
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.maxRateRetries = Math.max(0, Math.floor(maxRateRetries));
  }

  async request(path, {
    method = 'GET',
    query,
    body,
    retryAuth = true,
    rateRetry = 0,
  } = {}) {
    const token = await this.tokenProvider.getToken(false);
    const response = await this.fetch(`${this.baseUrl}${path}${queryString(query)}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });

    if (response.status === 401 && retryAuth) {
      await this.tokenProvider.getToken(true);
      return this.request(path, {
        method,
        query,
        body,
        retryAuth: false,
        rateRetry,
      });
    }

    if (response.status === 429 && rateRetry < this.maxRateRetries) {
      await sleep(retryDelayMs(response, rateRetry));
      return this.request(path, {
        method,
        query,
        body,
        retryAuth,
        rateRetry: rateRetry + 1,
      });
    }

    if (!response.ok) {
      let responseBody = null;
      try {
        responseBody = await response.json();
      } catch {
        responseBody = await response.text().catch(() => null);
      }
      const message = responseBody?.error?.message || `Spotify API request failed (${response.status}).`;
      throw new SpotifyApiError(message, { status: response.status, body: responseBody });
    }

    if (response.status === 204) return null;
    const contentType = response.headers.get('content-type') || '';
    return contentType.includes('application/json') ? response.json() : response.text();
  }

  getPlaybackState() {
    return this.request('/me/player');
  }

  getDevices() {
    return this.request('/me/player/devices');
  }

  play(deviceId = null) {
    return this.request('/me/player/play', {
      method: 'PUT',
      query: { device_id: deviceId },
    });
  }

  pause(deviceId = null) {
    return this.request('/me/player/pause', {
      method: 'PUT',
      query: { device_id: deviceId },
    });
  }

  next(deviceId = null) {
    return this.request('/me/player/next', {
      method: 'POST',
      query: { device_id: deviceId },
    });
  }

  previous(deviceId = null) {
    return this.request('/me/player/previous', {
      method: 'POST',
      query: { device_id: deviceId },
    });
  }

  seek(positionMs, deviceId = null) {
    return this.request('/me/player/seek', {
      method: 'PUT',
      query: {
        position_ms: Math.max(0, Math.round(positionMs)),
        device_id: deviceId,
      },
    });
  }

  setVolume(percent, deviceId = null) {
    return this.request('/me/player/volume', {
      method: 'PUT',
      query: {
        volume_percent: Math.round(clamp(percent, 0, 100)),
        device_id: deviceId,
      },
    });
  }

  setShuffle(enabled, deviceId = null) {
    return this.request('/me/player/shuffle', {
      method: 'PUT',
      query: { state: Boolean(enabled), device_id: deviceId },
    });
  }

  setRepeat(mode, deviceId = null) {
    if (!['off', 'track', 'context'].includes(mode)) {
      throw new RangeError('Repeat mode must be off, track, or context.');
    }
    return this.request('/me/player/repeat', {
      method: 'PUT',
      query: { state: mode, device_id: deviceId },
    });
  }

  transferPlayback(deviceId, { play = false } = {}) {
    if (!deviceId) throw new TypeError('transferPlayback requires a deviceId.');
    return this.request('/me/player', {
      method: 'PUT',
      body: { device_ids: [deviceId], play: Boolean(play) },
    });
  }
}
