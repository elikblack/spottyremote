const AUTH_URL = 'https://accounts.spotify.com/authorize';
const TOKEN_URL = 'https://accounts.spotify.com/api/token';
const STORAGE_PREFIX = 'spottyremote.spotify.';

export const DEFAULT_SCOPES = [
  'user-read-playback-state',
  'user-read-currently-playing',
  'user-modify-playback-state',
];

function randomBase64Url(byteCount = 48) {
  const bytes = new Uint8Array(byteCount);
  crypto.getRandomValues(bytes);
  const binary = String.fromCharCode(...bytes);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function sha256Base64Url(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  const bytes = new Uint8Array(digest);
  const binary = String.fromCharCode(...bytes);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function defaultRedirectUri() {
  return `${window.location.origin}${window.location.pathname}`;
}

export class SpotifyBrowserAuth {
  constructor({
    clientId = null,
    redirectUri = defaultRedirectUri(),
    scopes = DEFAULT_SCOPES,
    storage = window.localStorage,
    fetchImpl = window.fetch.bind(window),
  } = {}) {
    this.storage = storage;
    this.fetch = fetchImpl;
    this.redirectUri = redirectUri;
    this.scopes = scopes;
    if (clientId) this.clientId = clientId;
  }

  key(name) {
    return `${STORAGE_PREFIX}${name}`;
  }

  get clientId() {
    return this.storage.getItem(this.key('client_id')) || '';
  }

  set clientId(value) {
    const clean = String(value || '').trim();
    if (clean) this.storage.setItem(this.key('client_id'), clean);
    else this.storage.removeItem(this.key('client_id'));
  }

  get configured() {
    return Boolean(this.clientId);
  }

  get hasSession() {
    return Boolean(this.storage.getItem(this.key('refresh_token')) || this.storage.getItem(this.key('access_token')));
  }

  async beginLogin() {
    if (!this.clientId) throw new Error('Spotify Client ID is not configured.');

    const verifier = randomBase64Url(64);
    const challenge = await sha256Base64Url(verifier);
    const state = randomBase64Url(24);

    this.storage.setItem(this.key('pkce_verifier'), verifier);
    this.storage.setItem(this.key('oauth_state'), state);

    const params = new URLSearchParams({
      client_id: this.clientId,
      response_type: 'code',
      redirect_uri: this.redirectUri,
      scope: this.scopes.join(' '),
      code_challenge_method: 'S256',
      code_challenge: challenge,
      state,
    });

    window.location.assign(`${AUTH_URL}?${params.toString()}`);
  }

  async handleCallback() {
    const params = new URLSearchParams(window.location.search);
    const code = params.get('code');
    const error = params.get('error');
    const returnedState = params.get('state');

    if (!code && !error) return false;

    try {
      if (error) throw new Error(`Spotify authorization failed: ${error}`);

      const expectedState = this.storage.getItem(this.key('oauth_state'));
      if (!expectedState || returnedState !== expectedState) {
        throw new Error('Spotify authorization state did not match.');
      }

      const verifier = this.storage.getItem(this.key('pkce_verifier'));
      if (!verifier) throw new Error('PKCE verifier is missing. Start authorization again.');

      const token = await this.requestToken({
        grant_type: 'authorization_code',
        code,
        redirect_uri: this.redirectUri,
        client_id: this.clientId,
        code_verifier: verifier,
      });
      this.saveToken(token);
      return true;
    } finally {
      this.storage.removeItem(this.key('pkce_verifier'));
      this.storage.removeItem(this.key('oauth_state'));
      history.replaceState({}, '', this.redirectUri);
    }
  }

  async requestToken(fields) {
    const response = await this.fetch(TOKEN_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(fields),
    });

    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.error_description || body.error || `Spotify token request failed (${response.status}).`);
    }
    return body;
  }

  saveToken(token) {
    if (token.access_token) this.storage.setItem(this.key('access_token'), token.access_token);
    if (token.refresh_token) this.storage.setItem(this.key('refresh_token'), token.refresh_token);
    if (token.expires_in) {
      this.storage.setItem(this.key('expires_at'), String(Date.now() + token.expires_in * 1000));
    }
    if (token.scope) this.storage.setItem(this.key('scope'), token.scope);
  }

  async refresh() {
    const refreshToken = this.storage.getItem(this.key('refresh_token'));
    if (!refreshToken) throw new Error('No Spotify refresh token is available. Reconnect Spotify.');
    if (!this.clientId) throw new Error('Spotify Client ID is not configured.');

    const token = await this.requestToken({
      grant_type: 'refresh_token',
      refresh_token: refreshToken,
      client_id: this.clientId,
    });
    this.saveToken(token);
    return token.access_token;
  }

  async getToken(forceRefresh = false) {
    const accessToken = this.storage.getItem(this.key('access_token'));
    const expiresAt = Number(this.storage.getItem(this.key('expires_at')) || 0);
    const stillValid = accessToken && expiresAt > Date.now() + 30_000;

    if (!forceRefresh && stillValid) return accessToken;
    if (this.storage.getItem(this.key('refresh_token'))) return this.refresh();
    if (accessToken && !forceRefresh) return accessToken;

    throw new Error('Spotify is not connected.');
  }

  disconnect() {
    for (const name of ['access_token', 'refresh_token', 'expires_at', 'scope', 'pkce_verifier', 'oauth_state']) {
      this.storage.removeItem(this.key(name));
    }
  }
}
