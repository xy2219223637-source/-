(() => {
  const CHANNEL = 'enrui-video-capture-v1';
  const PAGE_CHANNEL = 'enrui-page-media-v2';
  const { normalizedMediaUrl, looksLikeVideo, platformNameForUrl, trustedAppOrigin, douyinVideoId } = globalThis.EnruiCaptureUtils;
  const { readVideoResponse, MAX_VIDEO_BYTES } = globalThis.EnruiCaptureDownload;
  const transfers = new Map();
  const controllers = new Map();
  let lastCandidates = new Map();

  window.addEventListener('message', async (event) => {
    if (event.source !== window || event.origin !== location.origin) return;
    const message = event.data;
    if (!message || message.channel !== CHANNEL || !['PING', 'START_CAPTURE', 'OPEN_SOURCE'].includes(message.type)) return;
    if (!trustedAppOrigin(event.origin)) return;
    void chrome.storage.sync.set({ appBase: event.origin }).catch(() => undefined);
    let response;
    try {
      if (message.type === 'PING') {
        response = { ok: true, version: chrome.runtime.getManifest().version, build: globalThis.EnruiCaptureBuild.build };
      } else {
        const payload = message.payload || {};
        if (message.type === 'START_CAPTURE' && new URL(payload.apiBase).origin !== event.origin) throw new Error('采集服务地址与当前页面不一致');
        response = await chrome.runtime.sendMessage({ type: message.type, payload });
      }
    } catch (error) { response = { ok: false, error: error?.message || '插件启动失败' }; }
    window.postMessage({ channel: CHANNEL, type: `${message.type}_RESULT`, requestId: message.requestId, payload: response }, location.origin);
  });

  function pageCandidates(videoId) {
    return new Promise((resolve) => {
      const requestId = crypto.randomUUID();
      const finish = (items) => { clearTimeout(timer); window.removeEventListener('message', receive); resolve(items); };
      const timer = setTimeout(() => finish([]), 500);
      function receive(event) {
        const message = event.data;
        if (event.source !== window || event.origin !== location.origin || message?.channel !== PAGE_CHANNEL
          || message.type !== 'RESULT' || message.requestId !== requestId) return;
        finish(Array.isArray(message.candidates) ? message.candidates.slice(0, 24) : []);
      }
      window.addEventListener('message', receive);
      window.postMessage({ channel: PAGE_CHANNEL, type: 'COLLECT', requestId, videoId }, location.origin);
    });
  }

  function visible(element) {
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight;
  }

  function interactionRequired() {
    const dialogs = document.querySelectorAll('[role="dialog"], [id*="captcha"], [class*="captcha_verify"], [class*="verify-wrap"]');
    for (const element of dialogs) {
      if (!visible(element)) continue;
      const text = element.textContent || '';
      if (/安全验证|拖动滑块|完成验证|验证码|验证身份/.test(text)) return 'verification_required';
      if (/扫码登录|手机号登录|登录后继续|请先登录/.test(text)) return 'login_required';
    }
    return '';
  }

  function playerItemId(video) {
    let element = video;
    for (let depth = 0; element && depth < 10; depth++, element = element.parentElement) {
      for (const name of ['data-aweme-id', 'data-e2e-vid', 'data-item-id']) {
        if (element.getAttribute(name)) return element.getAttribute(name);
      }
      const match = String(element.className || '').match(/(?:^|\s)video_(\d{15,24})(?:\s|$)/);
      if (match) return match[1];
    }
    return '';
  }

  async function collectVideoCandidates(requestedId) {
    const platform = platformNameForUrl(location.href);
    const videoId = requestedId || douyinVideoId(location.href);
    const candidates = new Map();
    const add = (rawUrl, score, source, itemId = '') => {
      const url = normalizedMediaUrl(rawUrl, location.href);
      if (!url || /\.(m3u8|mpd|m4s|ts)(?:$|[?#])/i.test(url)) return;
      if (platform === 'douyin' && (!videoId || itemId !== videoId)) return;
      const old = candidates.get(url);
      if (!old || score > old.score) candidates.set(url, { url, score, source, videoId: itemId });
    };
    let title = document.title;
    if (platform === 'douyin' && videoId) {
      for (const item of await pageCandidates(videoId)) {
        if (item.videoId !== videoId) continue;
        add(item.url, 1000, 'douyin.detail', videoId);
        if (item.title) title = String(item.title).slice(0, 200);
      }
    }

    const videos = [...document.querySelectorAll('video')];
    for (const video of videos) {
      const explicitId = playerItemId(video);
      const itemId = explicitId;
      if (platform === 'douyin' && (!videoId || itemId !== videoId)) continue;
      const score = 400 + (visible(video) ? 35 : 0) + (!video.paused ? 40 : 0);
      add(video.currentSrc, score, 'video.currentSrc', itemId);
      add(video.src, score - 10, 'video.src', itemId);
      for (const source of video.querySelectorAll('source')) add(source.src, score - 20, 'video.source', itemId);
      // Only a task-selected player is started, always muted in its background tab.
      if (video.paused && (visible(video) || videos.length === 1)) {
        video.muted = true;
        void video.play().catch(() => undefined);
      }
    }
    if (platform !== 'douyin') {
      for (const selector of ['meta[property="og:video"]', 'meta[property="og:video:url"]', 'meta[property="og:video:secure_url"]', 'meta[name="twitter:player:stream"]']) {
        add(document.querySelector(selector)?.content, 150, 'page.meta');
      }
      for (const entry of performance.getEntriesByType('resource')) {
        if (looksLikeVideo(entry.name)) add(entry.name, 100, 'performance.media');
      }
    }
    const sorted = [...candidates.values()].sort((a, b) => b.score - a.score).slice(0, 24);
    lastCandidates = new Map(sorted.map((item) => [item.url, item]));
    return { platform, title, pageUrl: location.href, videoId, interaction: interactionRequired(), candidates: sorted };
  }

  function dispose(id) {
    const transfer = transfers.get(id);
    if (transfer) clearTimeout(transfer.timer);
    transfers.delete(id);
    controllers.get(id)?.abort();
    controllers.delete(id);
  }

  async function download(message) {
    if (!lastCandidates.has(message.url)) throw new Error('unknown_candidate');
    dispose(message.id);
    const controller = new AbortController();
    controllers.set(message.id, controller);
    const timeout = setTimeout(() => controller.abort(), 60_000);
    try {
      const response = await fetch(message.url, { credentials: 'include', cache: 'no-store', signal: controller.signal });
      const video = await readVideoResponse(response, Math.min(message.maxBytes || MAX_VIDEO_BYTES, MAX_VIDEO_BYTES));
      transfers.set(message.id, { ...video, timer: setTimeout(() => dispose(message.id), 120_000) });
      return { size: video.blob.size, contentType: video.contentType };
    } finally { clearTimeout(timeout); controllers.delete(message.id); }
  }

  async function handle(message) {
    if (message.type === 'EXTRACT_VIDEO') return collectVideoCandidates(message.videoId);
    if (message.type === 'DOWNLOAD_VIDEO') return download(message);
    if (message.type === 'VIDEO_CHUNK') {
      const transfer = transfers.get(message.id);
      if (!transfer || !Number.isInteger(message.offset) || message.offset < 0) throw new Error('transfer_expired');
      clearTimeout(transfer.timer);
      transfer.timer = setTimeout(() => dispose(message.id), 120_000);
      const bytes = new Uint8Array(await transfer.blob.slice(message.offset, message.offset + 512 * 1024).arrayBuffer());
      let binary = '';
      for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
      return { data: btoa(binary) };
    }
    if (message.type === 'RELEASE_VIDEO') { dispose(message.id); return {}; }
    if (message.type === 'STOP_CAPTURE') {
      for (const id of new Set([...transfers.keys(), ...controllers.keys()])) dispose(id);
      window.postMessage({ channel: PAGE_CHANNEL, type: 'STOP' }, location.origin);
      lastCandidates.clear();
      return {};
    }
  }

  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (sender.id !== chrome.runtime.id || !['EXTRACT_VIDEO', 'DOWNLOAD_VIDEO', 'VIDEO_CHUNK', 'RELEASE_VIDEO', 'STOP_CAPTURE'].includes(message?.type)) return;
    void handle(message).then((result) => sendResponse({ ok: true, result })).catch((error) => sendResponse({
      ok: false, code: error.code || (error.name === 'AbortError' ? 'download_timeout' : 'network_error'),
      status: error.status || 0, mime: error.mime || '', size: error.size || 0, kind: error.kind || '',
    }));
    return true;
  });
})();
