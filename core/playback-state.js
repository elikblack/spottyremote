function bestImage(images = []) {
  if (!Array.isArray(images) || images.length === 0) return null;
  return [...images].sort((a, b) => (b.width || 0) - (a.width || 0))[0]?.url || null;
}

export function normalizePlayback(payload) {
  if (!payload) {
    return {
      available: false,
      isPlaying: false,
      progressMs: 0,
      durationMs: 0,
      shuffle: false,
      repeat: 'off',
      item: null,
      device: null,
      context: null,
      fetchedAt: Date.now(),
    };
  }

  const rawItem = payload.item || null;
  const isEpisode = rawItem?.type === 'episode';
  const artists = isEpisode
    ? [rawItem?.show?.name].filter(Boolean)
    : (rawItem?.artists || []).map((artist) => artist.name).filter(Boolean);

  const images = isEpisode ? rawItem?.images : rawItem?.album?.images;

  return {
    available: Boolean(payload.device || rawItem),
    isPlaying: Boolean(payload.is_playing),
    progressMs: payload.progress_ms || 0,
    durationMs: rawItem?.duration_ms || 0,
    shuffle: Boolean(payload.shuffle_state),
    repeat: payload.repeat_state || 'off',
    item: rawItem
      ? {
          id: rawItem.id || null,
          uri: rawItem.uri || null,
          type: rawItem.type || null,
          name: rawItem.name || 'Unknown',
          artists,
          album: isEpisode ? rawItem?.show?.name || null : rawItem?.album?.name || null,
          artworkUrl: bestImage(images),
          explicit: Boolean(rawItem.explicit),
        }
      : null,
    device: payload.device
      ? {
          id: payload.device.id || null,
          name: payload.device.name || 'Unknown device',
          type: payload.device.type || null,
          volume: payload.device.volume_percent ?? null,
          supportsVolume: Boolean(payload.device.supports_volume),
          restricted: Boolean(payload.device.is_restricted),
        }
      : null,
    context: payload.context
      ? {
          type: payload.context.type || null,
          uri: payload.context.uri || null,
          href: payload.context.href || null,
        }
      : null,
    fetchedAt: Date.now(),
  };
}

export function estimatedProgress(state, now = Date.now()) {
  if (!state?.isPlaying || !state?.durationMs) return state?.progressMs || 0;
  const elapsed = Math.max(0, now - (state.fetchedAt || now));
  return Math.min(state.durationMs, (state.progressMs || 0) + elapsed);
}
