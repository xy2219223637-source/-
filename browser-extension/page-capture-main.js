(() => {
  const DEFAULT_APP_BASE = 'https://waynepartners.xyz';
  const TRUSTED_APP_ORIGINS = new Set([
    DEFAULT_APP_BASE,
    'https://www.waynepartners.xyz',
    'http://127.0.0.1:8080',
    'http://localhost:8080',
    'http://127.0.0.1:5173',
    'http://localhost:5173',
  ]);

  function trustedAppOrigin(value) {
    try {
      const origin = new URL(value).origin;
      return TRUSTED_APP_ORIGINS.has(origin) ? origin : '';
    } catch {
      return '';
    }
  }

  function platformNameForUrl(value) {
    try {
      const host = new URL(value).hostname.toLowerCase();
      if (host === 'douyin.com' || host.endsWith('.douyin.com')) return 'douyin';
      if (host === 'xiaohongshu.com' || host.endsWith('.xiaohongshu.com') || host === 'xhslink.com' || host.endsWith('.xhslink.com')) return 'xiaohongshu';
      if (host === 'kuaishou.com' || host.endsWith('.kuaishou.com')) return 'kuaishou';
      if (host === 'tiktok.com' || host.endsWith('.tiktok.com')) return 'tiktok';
    } catch {
      return 'generic';
    }
    return 'generic';
  }

  function capturePageUrl(value) {
    try {
      const url = new URL(value);
      if (platformNameForUrl(url.href) !== 'douyin') return url.href;
      const modalId = url.searchParams.get('modal_id') || '';
      if (/^\d{15,24}$/.test(modalId) && !/^\/video\/\d+/.test(url.pathname)) {
        return `https://www.douyin.com/video/${modalId}`;
      }
      return url.href;
    } catch {
      return value;
    }
  }

  function decodeEscapedUrl(value) {
    let decoded = value.trim()
      .replaceAll('\\u002F', '/')
      .replaceAll('\\u002f', '/')
      .replaceAll('\\u0026', '&')
      .replaceAll('\\u003D', '=')
      .replaceAll('\\u003d', '=')
      .replaceAll('\\u003F', '?')
      .replaceAll('\\u003f', '?')
      .replaceAll('\\/', '/')
      .replaceAll('&amp;', '&');
    if (/^https?%3a%2f%2f/i.test(decoded)) {
      try {
        decoded = decodeURIComponent(decoded);
      } catch {
        // Keep the original value when it contains malformed percent escapes.
      }
    }
    return decoded;
  }

  function normalizedMediaUrl(value, baseUrl) {
    if (typeof value !== 'string' || !value.trim()) return '';
    let url = decodeEscapedUrl(value);
    if (url.startsWith('//')) {
      try {
        url = `${new URL(baseUrl).protocol}${url}`;
      } catch {
        url = `https:${url}`;
      }
    }
    try {
      url = new URL(url, baseUrl).href;
    } catch {
      return '';
    }
    return url.startsWith('http://') || url.startsWith('https://') ? url : '';
  }

  function looksLikeVideo(value) {
    if (typeof value !== 'string') return false;
    const lower = value.toLowerCase();
    if (/\.(m3u8|mpd|m4s|ts)(?:$|[?#])/.test(lower)) return false;
    return /\.(mp4|mov|m4v|webm)(?:$|[?#])/.test(lower)
      || lower.includes('/video/tos')
      || lower.includes('/aweme/v1/play')
      || lower.includes('video_id=')
      || lower.includes('videoplayback')
      || lower.includes('play_addr')
      || lower.includes('/playwm/')
      || lower.includes('mime_type=video')
      || lower.includes('douyinvod.com')
      || lower.includes('bytevcloudvod.com')
      || /sns-video-[^.]+\.xhscdn\.com/.test(lower)
      || (lower.includes('kwaicdn.com') && lower.includes('video'));
  }

  function douyinVideoId(value) {
    try {
      const url = new URL(value);
      if (platformNameForUrl(url.href) !== 'douyin') return '';
      const id = url.searchParams.get('modal_id') || url.pathname.match(/\/(?:video|note)\/(\d+)/)?.[1] || '';
      return /^\d{15,24}$/.test(id) ? id : '';
    } catch {
      return '';
    }
  }

  // Only accept addresses attached to the requested item, never a recommended item.
  function douyinCandidates(data, targetId, baseUrl) {
    if (!targetId) return [];
    const found = new Map();
    const visited = new WeakSet();
    let remaining = 6000;
    function visit(node, depth) {
      if (!node || typeof node !== 'object' || depth > 18 || remaining-- <= 0 || visited.has(node)) return;
      visited.add(node);
      const id = node.aweme_id ?? node.awemeId ?? node.id;
      const exactId = typeof id === 'string' && id === targetId;
      const video = node.video;
      if (exactId && video && !node.is_ads && !node.isAd) {
        const addresses = [video.play_addr, video.playAddr];
        const rates = video.bit_rate || video.bitRateList || [];
        for (const rate of Array.isArray(rates) ? rates : []) {
          if (!rate || typeof rate !== 'object') continue;
          addresses.push(rate.play_addr || rate.playAddr);
        }
        for (const address of addresses.filter(Boolean)) {
          const values = typeof address === 'string' ? [address] : Array.isArray(address) ? address : address.url_list || address.urlList || [];
          const urls = Array.isArray(values) ? values : [];
          for (const raw of urls) {
            const url = normalizedMediaUrl(raw, baseUrl);
            if (!url || /\.(m3u8|mpd|m4s|ts)(?:$|[?#])/i.test(url)) continue;
            found.set(url, {
              url, score: 1000, source: 'douyin.detail', videoId: targetId,
              title: typeof node.desc === 'string' ? node.desc.slice(0, 200) : '',
            });
          }
        }
      }
      for (const child of Object.values(node)) visit(child, depth + 1);
    }
    visit(data, 0);
    return [...found.values()].slice(0, 24);
  }

  function videoMimeFromBytes(value) {
    const bytes = value instanceof Uint8Array ? value : new Uint8Array(value || []);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let offset = 0; offset + 12 <= bytes.length;) {
      const box = String.fromCharCode(...bytes.slice(offset + 4, offset + 8));
      if (box === 'ftyp') return 'video/mp4';
      if (box !== 'free' && box !== 'wide' && box !== 'skip') break;
      const length = view.getUint32(offset);
      if (length < 8) break;
      offset += length;
    }
    if (bytes.length >= 4
      && bytes[0] === 0x1a
      && bytes[1] === 0x45
      && bytes[2] === 0xdf
      && bytes[3] === 0xa3) return 'video/webm';
    return '';
  }

  const captureUtils = Object.freeze({
    DEFAULT_APP_BASE,
    capturePageUrl,
    douyinVideoId,
    douyinCandidates,
    looksLikeVideo,
    normalizedMediaUrl,
    platformNameForUrl,
    trustedAppOrigin,
    videoMimeFromBytes,
  });

  globalThis.EnruiCaptureUtils = captureUtils;
  if (typeof module === 'object' && module.exports) module.exports = captureUtils;
})();

(() => {
  const CHANNEL = 'enrui-page-media-v2';
  const { douyinCandidates } = globalThis.EnruiCaptureUtils;
  const candidates = new Map();
  let targetId = '';
  let enabledUntil = 0;

  function remember(data) {
    if (Date.now() > enabledUntil) return;
    for (const item of douyinCandidates(data, targetId, location.href)) candidates.set(item.url, item);
    while (candidates.size > 24) candidates.delete(candidates.keys().next().value);
  }

  const isDetailApi = (url) => {
    try {
      const parsed = new URL(url, location.href);
      return parsed.origin === location.origin && /\/aweme\/v1\/web\/(?:aweme\/detail|aweme\/post|tab\/feed|aweme\/related|general\/search)/.test(parsed.pathname);
    } catch { return false; }
  };

  async function inspectResponse(response) {
    if (!response.ok || !response.body) return;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let text = '';
    let size = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > 4_000_000) return;
        text += decoder.decode(value, { stream: true });
      }
      remember(JSON.parse(text + decoder.decode()));
    } catch {
      // A non-JSON platform response must not affect the original page request.
    } finally { void reader.cancel().catch(() => undefined); }
  }

  const originalFetch = window.fetch;
  window.fetch = function (...args) {
    const pending = originalFetch.apply(this, args);
    const url = typeof args[0] === 'string' || args[0] instanceof URL ? String(args[0]) : args[0]?.url;
    if (isDetailApi(url)) {
      void pending.then((response) => {
        if (Date.now() <= enabledUntil) return inspectResponse(response.clone());
      }).catch(() => undefined);
    }
    return pending;
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    if (isDetailApi(url)) {
      this.addEventListener('load', () => {
        if (Date.now() > enabledUntil) return;
        try {
          if (this.responseType === 'json') remember(this.response);
          else if ((!this.responseType || this.responseType === 'text') && this.responseText.length < 4_000_000) remember(JSON.parse(this.responseText));
        } catch { /* The page retains its original response. */ }
      }, { once: true });
    }
    return originalOpen.call(this, method, url, ...rest);
  };

  window.addEventListener('message', (event) => {
    if (event.source !== window || event.origin !== location.origin || event.data?.channel !== CHANNEL) return;
    const message = event.data;
    if (message.type === 'STOP') { enabledUntil = 0; candidates.clear(); return; }
    if (message.type !== 'COLLECT' || !/^\d{15,24}$/.test(message.videoId || '')) return;
    if (message.videoId !== targetId) candidates.clear();
    targetId = message.videoId;
    enabledUntil = Date.now() + 15_000;
    for (const name of ['_ROUTER_DATA', '__INITIAL_STATE__', '__NEXT_DATA__']) {
      try { remember(window[name]); } catch { /* Some page globals are accessors. */ }
    }
    for (const script of document.querySelectorAll('script[type="application/json"], script#RENDER_DATA')) {
      const raw = script.textContent || '';
      if (raw.length > 4_000_000) continue;
      try { remember(JSON.parse(script.id === 'RENDER_DATA' ? decodeURIComponent(raw) : raw)); } catch { /* Ignore unrelated state. */ }
    }
    window.postMessage({ channel: CHANNEL, type: 'RESULT', requestId: message.requestId, candidates: [...candidates.values()] }, location.origin);
  });
})();
