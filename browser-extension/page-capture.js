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
