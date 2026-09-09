importScripts('build-info.js', 'capture-utils.js', 'capture-download.js');

const { DEFAULT_APP_BASE, capturePageUrl, douyinVideoId, looksLikeVideo, platformNameForUrl, trustedAppOrigin } = globalThis.EnruiCaptureUtils;
const { MAX_VIDEO_BYTES, readVideoResponse } = globalThis.EnruiCaptureDownload;
const activeCaptures = new Set();
const mediaByTab = new Map();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const MESSAGES = {
  login_required: '平台需要登录, 请点击原视频完成登录后重新采集',
  verification_required: '平台需要验证, 请点击原视频完成验证后重新采集',
  not_video: '平台返回了非视频内容, 请检查原视频页面后重新采集',
  network_error: '视频下载请求失败, 请检查浏览器站点权限和网络后重新采集',
  download_timeout: '视频下载超时, 请稍后重新采集',
  http_error: '平台拒绝了视频下载请求, 请检查原视频页面后重新采集',
  partial_video: '平台返回的视频文件不完整, 请重新采集',
  too_large: '视频超过当前上传大小限制',
  empty_video: '平台返回了空视频文件, 请重新采集',
  no_target_video: '未取得指定视频的有效资源, 请检查原视频是否可播放后重新采集',
  interrupted: '浏览器后台任务已中断, 请重新采集',
};

async function withTimeout(pending, ms) {
  let timer;
  try {
    return await Promise.race([pending, new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error('download_timeout'), { code: 'download_timeout' })), ms);
    })]);
  } finally { clearTimeout(timer); }
}

async function tabRequest(tabId, message, timeout = 5000) {
  const response = await withTimeout(chrome.tabs.sendMessage(tabId, message), timeout);
  if (!response?.ok) throw Object.assign(new Error(response?.code || 'network_error'), response, { code: response?.code || 'network_error' });
  return response.result;
}

async function apiRequest(task, path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), path === '/upload' ? 180_000 : 15_000);
  try {
    const response = await fetch(`${task.apiBase}/api/director-projects/capture-sessions/${task.captureId}${path}`, {
      ...options, signal: controller.signal,
      headers: { 'X-Capture-Token': task.captureToken, ...(options.headers || {}) },
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw Object.assign(new Error(typeof payload.detail === 'string' ? payload.detail : `采集服务请求失败 (${response.status})`), { apiError: true });
    }
    return await response.json();
  } finally { clearTimeout(timeout); }
}

async function updateStage(task, stage, diagnostics) {
  await apiRequest(task, '', { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stage, diagnostics }) });
}

async function failCapture(task, message, diagnostics) {
  await apiRequest(task, '/fail', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: message.slice(0, 500), diagnostics }),
  }).catch(() => undefined);
}

function sourceKey(url) { return `source:${capturePageUrl(url)}`; }

async function sourceTab(sourcePageUrl) {
  const key = sourceKey(sourcePageUrl);
  const saved = (await chrome.storage.session.get(key))[key];
  if (saved?.expiresAt > Date.now()) {
    const tab = await chrome.tabs.get(saved.tabId).catch(() => null);
    if (tab && (capturePageUrl(tab.url) === capturePageUrl(sourcePageUrl)
      || (douyinVideoId(sourcePageUrl) && douyinVideoId(tab.url) === douyinVideoId(sourcePageUrl)))) {
      return { tab, created: saved.created, navigateTo: '' };
    }
  }
  const tab = await chrome.tabs.create({ url: capturePageUrl(sourcePageUrl), active: false });
  await chrome.storage.session.set({ [key]: { tabId: tab.id, created: true, expiresAt: Date.now() + 30 * 60_000 } });
  return { tab, created: true, navigateTo: '' };
}

function header(headers, name) {
  return (headers || []).find((item) => item.name?.toLowerCase() === name)?.value || '';
}

chrome.webRequest.onHeadersReceived.addListener((details) => {
  const candidates = mediaByTab.get(details.tabId);
  if (!candidates || details.statusCode < 200 || details.statusCode >= 300) return;
  const type = header(details.responseHeaders, 'content-type').split(';')[0];
  if (/\.(m3u8|mpd|m4s|ts)(?:$|[?#])/i.test(details.url)) return;
  if (!type.startsWith('video/') && !(type === 'application/octet-stream' && looksLikeVideo(details.url))) return;
  candidates.set(details.url, { url: details.url, source: 'network.media', score: 200 });
  while (candidates.size > 30) candidates.delete(candidates.keys().next().value);
}, { urls: ['<all_urls>'], types: ['media', 'xmlhttprequest', 'other'] }, ['responseHeaders']);

chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === 'loading') mediaByTab.get(tabId)?.clear();
});
chrome.tabs.onRemoved.addListener((tabId) => mediaByTab.delete(tabId));

async function extractionFromTab(tabId, targetId) {
  const tab = await chrome.tabs.get(tabId);
  const resolvedId = targetId || douyinVideoId(tab.url);
  const page = await tabRequest(tabId, { type: 'EXTRACT_VIDEO', videoId: resolvedId }).catch(() => null);
  const platform = page?.platform || platformNameForUrl(tab.url);
  const combined = new Map((page?.candidates || []).map((item) => [item.url, item]));
  // Unbound network responses on Douyin may be ads or prefetched recommendations.
  if (platform !== 'douyin') {
    for (const item of mediaByTab.get(tabId)?.values() || []) {
      if (!combined.has(item.url)) combined.set(item.url, item);
    }
  }
  const candidates = [...combined.values()].filter((item) => platform !== 'douyin' || (resolvedId && item.videoId === resolvedId));
  return { platform, videoId: resolvedId, pageUrl: page?.pageUrl || tab.url, title: page?.title || tab.title || '', interaction: page?.interaction || '', candidates: candidates.sort((a, b) => b.score - a.score) };
}

async function downloadInPage(tabId, candidate, task, maxBytes) {
  const id = `${task.captureId}:${crypto.randomUUID()}`;
  try {
    const meta = await tabRequest(tabId, { type: 'DOWNLOAD_VIDEO', id, url: candidate.url, maxBytes }, 65_000);
    if (!Number.isInteger(meta.size) || meta.size <= 0 || meta.size > maxBytes) throw Object.assign(new Error('too_large'), { code: 'too_large' });
    const parts = [];
    for (let offset = 0; offset < meta.size; offset += 512 * 1024) {
      const result = await tabRequest(tabId, { type: 'VIDEO_CHUNK', id, offset });
      const bytes = Uint8Array.from(atob(result.data), (char) => char.charCodeAt(0));
      if (bytes.length !== Math.min(512 * 1024, meta.size - offset)) throw Object.assign(new Error('partial_video'), { code: 'partial_video' });
      parts.push(bytes);
    }
    return { blob: new Blob(parts, { type: meta.contentType }), contentType: meta.contentType };
  } finally {
    await tabRequest(tabId, { type: 'RELEASE_VIDEO', id }).catch(() => undefined);
  }
}

async function downloadInBackground(candidate, maxBytes) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60_000);
  try {
    const response = await fetch(candidate.url, { credentials: 'include', cache: 'no-store', signal: controller.signal });
    return await readVideoResponse(response, maxBytes);
  } finally { clearTimeout(timeout); }
}

async function recordAttempt(diagnostics, candidate, context, error, video) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(candidate.url));
  const urlKey = [...new Uint8Array(hash)].slice(0, 8).map((byte) => byte.toString(16).padStart(2, '0')).join('');
  const code = video ? 'downloaded' : error?.code || (error?.name === 'AbortError' ? 'download_timeout' : 'network_error');
  diagnostics.attempts.push({
    source: candidate.source, context, resource_host: new URL(candidate.url).hostname,
    url_key: urlKey, code, status: error?.status || 0, mime: error?.mime || video?.contentType || '',
    size: error?.size || video?.blob.size || 0, body_kind: error?.kind || '',
  });
  diagnostics.attempts = diagnostics.attempts.slice(-24);
  diagnostics.last_code = code;
}

async function locateAndDownload(task, tabId, diagnostics) {
  const deadline = Date.now() + 240_000;
  const attempts = new Map();
  const started = Date.now();
  let reloaded = false;
  let stage = 'locating_video';
  let targetId = douyinVideoId(task.sourcePageUrl);
  let lastCode = 'no_target_video';
  await updateStage(task, stage, diagnostics);
  while (Date.now() < deadline) {
    const extraction = await extractionFromTab(tabId, targetId);
    targetId ||= extraction.videoId;
    diagnostics.target_video_id = targetId;
    diagnostics.candidate_count = extraction.candidates.length;
    if (extraction.interaction) throw Object.assign(new Error(extraction.interaction), { code: extraction.interaction });
    const candidates = extraction.candidates.filter((item) => {
      const tried = attempts.get(item.url);
      return !tried || (tried.count < 2 && Date.now() - tried.at > 30_000);
    }).slice(0, 2);
    for (const candidate of candidates) {
      if (stage !== 'downloading') { stage = 'downloading'; await updateStage(task, stage, diagnostics); }
      attempts.set(candidate.url, { count: (attempts.get(candidate.url)?.count || 0) + 1, at: Date.now() });
      for (const context of ['page', 'extension']) {
        if (Date.now() >= deadline) break;
        let video;
        try {
          const maxBytes = Math.min(task.maxBytes || MAX_VIDEO_BYTES, MAX_VIDEO_BYTES);
          video = context === 'page' ? await downloadInPage(tabId, candidate, task, maxBytes) : await downloadInBackground(candidate, maxBytes);
        } catch (error) {
          await recordAttempt(diagnostics, candidate, context, error);
          lastCode = diagnostics.last_code;
          await updateStage(task, stage, diagnostics);
          if (lastCode === 'too_large') throw Object.assign(new Error(lastCode), { code: lastCode });
          continue;
        }
        await recordAttempt(diagnostics, candidate, context, null, video);
        return { video, extraction };
      }
    }
    if (stage !== 'locating_video') { stage = 'locating_video'; await updateStage(task, stage, diagnostics); }
    if (!reloaded && Date.now() - started > 30_000) {
      reloaded = true;
      await chrome.tabs.reload(tabId);
    }
    if (Date.now() - started > 90_000 && !candidates.length) break;
    await delay(1000);
  }
  throw Object.assign(new Error(lastCode), { code: lastCode });
}

const recovery = chrome.storage.session.get(null).then(async (stored) => {
  for (const [key, value] of Object.entries(stored)) {
    if (!key.startsWith('capture-live:')) continue;
    await failCapture(value.task, MESSAGES.interrupted, { version: chrome.runtime.getManifest().version, last_code: 'interrupted' });
    await chrome.storage.session.remove(key);
  }
});

async function runCapture(task) {
  await recovery;
  if (activeCaptures.has(task.captureId)) return;
  activeCaptures.add(task.captureId);
  const key = `capture-live:${task.captureId}`;
  const diagnostics = { version: chrome.runtime.getManifest().version, build: globalThis.EnruiCaptureBuild.build, browser: navigator.userAgent.slice(0, 256), target_video_id: '', candidate_count: 0, last_code: '', attempts: [] };
  let source;
  let succeeded = false;
  let heartbeat;
  try {
    await chrome.storage.session.set({ [key]: { task } });
    // Chrome API activity keeps a pending transfer alive; persisted tasks detect worker restarts.
    heartbeat = setInterval(() => { void chrome.storage.session.set({ [key]: { task } }); }, 10_000);
    await updateStage(task, 'opening_source', diagnostics);
    source = await sourceTab(task.sourcePageUrl);
    mediaByTab.set(source.tab.id, new Map());
    if (source.navigateTo) await chrome.tabs.update(source.tab.id, { url: source.navigateTo, active: false });
    const { video, extraction } = await locateAndDownload(task, source.tab.id, diagnostics);
    await updateStage(task, 'uploading', diagnostics);
    const suffix = video.contentType === 'video/webm' ? 'webm' : 'mp4';
    await apiRequest(task, '/upload', {
      method: 'POST', headers: {
        'Content-Type': video.contentType, 'X-Video-Filename': `${extraction.platform}-${Date.now()}.${suffix}`,
        'X-Source-Title': encodeURIComponent(extraction.title.slice(0, 200)),
      }, body: video.blob,
    });
    succeeded = true;
    await chrome.action.setBadgeText({ text: '' });
  } catch (error) {
    diagnostics.last_code = error.code || diagnostics.last_code || 'network_error';
    await failCapture(task, error.apiError ? error.message : MESSAGES[diagnostics.last_code] || MESSAGES.network_error, diagnostics);
    await chrome.action.setBadgeBackgroundColor({ color: '#b42318' }).catch(() => undefined);
    await chrome.action.setBadgeText({ text: '!' }).catch(() => undefined);
  } finally {
    clearInterval(heartbeat);
    if (source?.tab.id) {
      mediaByTab.delete(source.tab.id);
      await tabRequest(source.tab.id, { type: 'STOP_CAPTURE' }).catch(() => undefined);
      const { autoClose = true } = await chrome.storage.sync.get('autoClose');
      if (succeeded) {
        await chrome.storage.session.remove(sourceKey(task.sourcePageUrl));
        if (source.created && autoClose) await chrome.tabs.remove(source.tab.id).catch(() => undefined);
      }
    }
    await chrome.storage.session.remove(key);
    activeCaptures.delete(task.captureId);
  }
}

async function openSource(url) {
  const source = await sourceTab(url);
  await chrome.tabs.update(source.tab.id, { ...(source.navigateTo ? { url: source.navigateTo } : {}), active: true });
  await chrome.windows.update(source.tab.windowId, { focused: true });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!['START_CAPTURE', 'OPEN_SOURCE', 'OPEN_APP'].includes(message?.type)) return;
  void (async () => {
    if (message.type === 'OPEN_APP' && sender.id === chrome.runtime.id && !sender.tab) {
      const stored = await chrome.storage.sync.get('appBase');
      await chrome.tabs.create({ url: `${trustedAppOrigin(stored.appBase) || DEFAULT_APP_BASE}/director-inspiration` });
      return;
    }
    const origin = trustedAppOrigin(sender.url);
    if (!origin || sender.id !== chrome.runtime.id) throw new Error('采集请求来源无效');
    const task = message.payload || {};
    const sourceUrl = new URL(task.sourcePageUrl);
    if (!['http:', 'https:'].includes(sourceUrl.protocol) || sourceUrl.username || sourceUrl.password) throw new Error('视频页面地址无效');
    if (message.type === 'OPEN_SOURCE') { await openSource(task.sourcePageUrl); return; }
    if (task.apiBase !== origin || !/^[a-f0-9]{32}$/.test(task.captureId || '') || !task.captureToken) throw new Error('采集任务参数无效');
    void runCapture(task).catch(() => undefined);
  })().then(() => sendResponse({ ok: true })).catch((error) => sendResponse({ ok: false, error: error.message }));
  return true;
});
