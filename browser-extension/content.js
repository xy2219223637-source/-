(() => {
  const CHANNEL = 'enrui-video-capture-v1';
  const { looksLikeVideo, normalizedMediaUrl, platformNameForUrl, trustedAppOrigin } = globalThis.EnruiCaptureUtils;

  function postBridge(type, requestId, payload) {
    window.postMessage({ channel: CHANNEL, type, requestId, payload }, window.location.origin);
  }

  window.addEventListener('message', async (event) => {
    if (event.source !== window || event.origin !== window.location.origin) return;
    const message = event.data;
    if (!message || message.channel !== CHANNEL || !['PING', 'START_CAPTURE'].includes(message.type)) return;
    const appOrigin = trustedAppOrigin(event.origin);
    if (!appOrigin) return;
    void chrome.storage.sync.set({ appBase: appOrigin }).catch(() => undefined);

    if (message.type === 'PING') {
      postBridge('PING_RESULT', message.requestId, { ok: true, version: chrome.runtime.getManifest().version });
      return;
    }

    const payload = message.payload || {};
    try {
      if (new URL(payload.apiBase).origin !== event.origin) throw new Error('采集服务地址与当前页面不一致');
      const response = await chrome.runtime.sendMessage({ type: 'START_CAPTURE', payload });
      postBridge('START_CAPTURE_RESULT', message.requestId, response || { ok: false, error: '插件后台无响应' });
    } catch (error) {
      postBridge('START_CAPTURE_RESULT', message.requestId, { ok: false, error: error?.message || '插件启动失败' });
    }
  });

  function collectVideoCandidates() {
    const candidates = new Map();
    const add = (rawUrl, score, source) => {
      const url = normalizedMediaUrl(rawUrl, window.location.href);
      if (!url || !looksLikeVideo(url)) return;
      const existing = candidates.get(url);
      if (!existing || score > existing.score) candidates.set(url, { url, score, source });
    };

    for (const video of document.querySelectorAll('video')) {
      const rect = video.getBoundingClientRect();
      const visible = rect.width > 120 && rect.height > 120 && rect.bottom > 0 && rect.top < window.innerHeight;
      const playing = !video.paused && !video.ended;
      const bonus = (visible ? 35 : 0) + (playing ? 40 : 0) + (video.readyState >= 2 ? 15 : 0);
      add(video.currentSrc, 180 + bonus, 'video.currentSrc');
      add(video.src, 165 + bonus, 'video.src');
      for (const source of video.querySelectorAll('source')) add(source.src, 150 + bonus, 'video.source');
    }

    for (const selector of [
      'meta[property="og:video"]',
      'meta[property="og:video:url"]',
      'meta[property="og:video:secure_url"]',
      'meta[name="twitter:player:stream"]',
    ]) {
      add(document.querySelector(selector)?.content, 150, selector);
    }

    for (const entry of performance.getEntriesByType('resource')) {
      if (looksLikeVideo(entry.name)) add(entry.name, entry.initiatorType === 'video' ? 145 : 105, `performance.${entry.initiatorType}`);
    }

    const urlPattern = /https?:\\?\/\\?\/[^"'<>\s]+/g;
    for (const script of document.scripts) {
      const text = script.textContent || '';
      if (!text || text.length > 5_000_000) continue;
      const matches = text.match(urlPattern) || [];
      for (const match of matches.slice(0, 500)) add(match, 80, 'page-state');
    }

    const sorted = [...candidates.values()].sort((left, right) => right.score - left.score);
    return {
      platform: platformNameForUrl(window.location.href),
      title: document.title,
      pageUrl: window.location.href,
      canonicalUrl: document.querySelector('link[rel="canonical"]')?.href || window.location.href,
      candidates: sorted.slice(0, 20),
      best: sorted[0] || null,
    };
  }

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== 'EXTRACT_VIDEO') return undefined;
    sendResponse({ ok: true, result: collectVideoCandidates() });
    return false;
  });
})();
