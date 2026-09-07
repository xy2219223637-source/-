importScripts('capture-utils.js');

const DEFAULT_APP_BASE = 'http://127.0.0.1:8080';
const activeCaptures = new Set();
const trackedCaptureTabs = new Set();
const mediaRequestsByTab = new Map();
const { capturePageUrl, looksLikeVideo, platformNameForUrl } = globalThis.EnruiCaptureUtils;

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function apiRequest(task, path, options = {}) {
  const response = await fetch(`${task.apiBase}/api/director-projects/capture-sessions/${task.captureId}${path}`, {
    ...options,
    headers: {
      'X-Capture-Token': task.captureToken,
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    let message = `采集服务请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      message = payload.detail || message;
    } catch {
      // Keep the status-based message when the response is not JSON.
    }
    throw new Error(message);
  }
  return response;
}

async function updateStage(task, stage) {
  await apiRequest(task, '', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stage }),
  });
}

async function failCapture(task, message) {
  try {
    await apiRequest(task, '/fail', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
  } catch {
    // The original error is more useful than a secondary reporting failure.
  }
}

function comparableUrl(value) {
  try {
    const url = new URL(value);
    url.hash = '';
    return `${url.origin}${url.pathname}${url.search}`;
  } catch {
    return value;
  }
}

async function sourceTab(sourcePageUrl) {
  const { pendingSource } = await chrome.storage.session.get('pendingSource');
  if (pendingSource?.tabId && pendingSource.sourcePageUrl === sourcePageUrl && pendingSource.expiresAt > Date.now()) {
    try {
      const tab = await chrome.tabs.get(pendingSource.tabId);
      await chrome.storage.session.remove('pendingSource');
      return { tab, created: false };
    } catch {
      await chrome.storage.session.remove('pendingSource');
    }
  }
  const normalizedSourceUrl = capturePageUrl(sourcePageUrl);
  const expected = comparableUrl(normalizedSourceUrl);
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((tab) => tab.id && tab.url && comparableUrl(tab.url) === expected);
  if (existing) return { tab: existing, created: false };
  return { tab: await chrome.tabs.create({ url: normalizedSourceUrl, active: false }), created: true };
}

function responseHeader(headers, name) {
  const header = (headers || []).find((item) => item.name?.toLowerCase() === name);
  return header?.value || '';
}

function responseSize(headers) {
  const contentRange = responseHeader(headers, 'content-range');
  const rangeTotal = Number(contentRange.match(/\/(\d+)$/)?.[1] || 0);
  return rangeTotal || Number(responseHeader(headers, 'content-length')) || 0;
}

function networkCandidate(details) {
  if (details.tabId < 0 || !trackedCaptureTabs.has(details.tabId)) return null;
  if (details.statusCode < 200 || details.statusCode >= 400) return null;
  if (/\.m3u8(?:$|[?#])/i.test(details.url)) return null;

  const contentType = responseHeader(details.responseHeaders, 'content-type').split(';', 1)[0].toLowerCase();
  const urlMatch = looksLikeVideo(details.url);
  let score = 0;
  if (contentType.startsWith('video/')) score = 320;
  else if (details.type === 'media' && (urlMatch || contentType === 'application/octet-stream')) score = 285;
  else if (urlMatch && contentType === 'application/octet-stream') score = 260;
  else if (urlMatch && ['media', 'xmlhttprequest', 'other'].includes(details.type)) score = 220;
  if (!score) return null;

  const sizeBytes = responseSize(details.responseHeaders);
  if (sizeBytes >= 1_000_000) score += Math.min(60, Math.floor(Math.log10(sizeBytes) * 8));
  return {
    url: details.url,
    score,
    source: `webRequest.${details.type}`,
    contentType,
    sizeBytes,
    capturedAt: Date.now(),
  };
}

function rememberNetworkCandidate(details) {
  const candidate = networkCandidate(details);
  if (!candidate) return;
  const candidates = mediaRequestsByTab.get(details.tabId) || new Map();
  const existing = candidates.get(candidate.url);
  if (!existing || candidate.score > existing.score) candidates.set(candidate.url, candidate);
  const sorted = [...candidates.values()].sort((left, right) => right.score - left.score).slice(0, 30);
  mediaRequestsByTab.set(details.tabId, new Map(sorted.map((item) => [item.url, item])));
}

chrome.webRequest.onHeadersReceived.addListener(
  rememberNetworkCandidate,
  { urls: ['<all_urls>'], types: ['media', 'xmlhttprequest', 'other'] },
  ['responseHeaders'],
);

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (trackedCaptureTabs.has(tabId) && changeInfo.status === 'loading') mediaRequestsByTab.delete(tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  trackedCaptureTabs.delete(tabId);
  mediaRequestsByTab.delete(tabId);
});

async function extractFromTab(tabId) {
  let pageResult = null;
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: 'EXTRACT_VIDEO' });
    pageResult = response?.ok ? response.result : null;
  } catch {
    // Network-level capture can still succeed when a content script is unavailable.
  }
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  const combined = new Map();
  for (const candidate of pageResult?.candidates || []) combined.set(candidate.url, candidate);
  for (const candidate of mediaRequestsByTab.get(tabId)?.values() || []) {
    const existing = combined.get(candidate.url);
    if (!existing || candidate.score > existing.score) combined.set(candidate.url, candidate);
  }
  const candidates = [...combined.values()].sort((left, right) => right.score - left.score).slice(0, 30);
  return {
    platform: pageResult?.platform || platformNameForUrl(tab?.url || ''),
    title: pageResult?.title || tab?.title || '',
    pageUrl: pageResult?.pageUrl || tab?.url || '',
    canonicalUrl: pageResult?.canonicalUrl || tab?.url || '',
    candidates,
    best: candidates[0] || null,
  };
}

async function waitForVideo(tabId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await extractFromTab(tabId);
    if (result?.best?.url) {
      await delay(750);
      return await extractFromTab(tabId) || result;
    }
    await delay(1000);
  }
  return null;
}

function videoFilename(mediaUrl, platform) {
  try {
    const name = decodeURIComponent(new URL(mediaUrl).pathname.split('/').pop() || '');
    if (/\.(mp4|mov|m4v|webm)$/i.test(name)) return name.slice(-120);
  } catch {
    // Use the platform fallback below.
  }
  return `${platform || 'captured'}-${Date.now()}.mp4`;
}

async function downloadVideo(task, extraction) {
  const candidates = extraction.candidates?.length ? extraction.candidates : [extraction.best];
  let lastError = null;
  for (const candidate of candidates.filter(Boolean).slice(0, 8)) {
    const mediaUrl = candidate.url;
    if (/\.m3u8(?:$|[?#])/i.test(mediaUrl)) continue;
    try {
      const response = await fetch(mediaUrl, {
        credentials: 'include',
        cache: 'no-store',
        referrer: extraction.pageUrl || task.sourcePageUrl,
      });
      if (!response.ok) throw new Error(`视频下载失败 (${response.status})`);
      const contentType = (response.headers.get('content-type') || candidate.contentType || 'video/mp4').split(';', 1)[0];
      if (!contentType.startsWith('video/') && contentType !== 'application/octet-stream') {
        throw new Error('识别到的资源不是视频文件');
      }
      const blob = await response.blob();
      if (!blob.size) throw new Error('下载到的视频文件为空');
      return { blob, contentType, filename: videoFilename(response.url || mediaUrl, extraction.platform) };
    } catch (error) {
      lastError = error;
    }
  }
  throw new Error(`${lastError?.message || '未找到可下载的视频资源'}, 请打开原视频并开始播放后重试`);
}

async function uploadVideo(task, video, extraction) {
  await apiRequest(task, '/upload', {
    method: 'POST',
    headers: {
      'Content-Type': video.contentType,
      'X-Video-Filename': encodeURIComponent(video.filename),
      'X-Source-Title': encodeURIComponent((extraction.title || '').slice(0, 200)),
    },
    body: video.blob,
  });
}

async function runCapture(task) {
  if (activeCaptures.has(task.captureId)) return;
  activeCaptures.add(task.captureId);
  let tabId = null;
  let createdTab = false;
  try {
    await updateStage(task, 'opening_source');
    const source = await sourceTab(task.sourcePageUrl);
    tabId = source.tab.id;
    createdTab = source.created;
    if (!tabId) throw new Error('无法创建视频页面');
    trackedCaptureTabs.add(tabId);
    mediaRequestsByTab.delete(tabId);

    await updateStage(task, 'locating_video');
    let extraction = await waitForVideo(tabId, 25_000);
    if (!extraction) {
      await chrome.tabs.update(tabId, { active: true });
      extraction = await waitForVideo(tabId, 60_000);
    }
    if (!extraction?.best?.url) throw new Error('未识别到可下载的视频, 请确认平台已登录并打开视频详情页');

    await updateStage(task, 'downloading');
    const video = await downloadVideo(task, extraction);
    await updateStage(task, 'uploading');
    await uploadVideo(task, video, extraction);
    await chrome.action.setBadgeText({ text: '' });

    const { autoClose = true } = await chrome.storage.sync.get('autoClose');
    if (createdTab && autoClose && tabId) await chrome.tabs.remove(tabId).catch(() => undefined);
  } catch (error) {
    const message = error?.message || '视频采集失败';
    await chrome.action.setBadgeBackgroundColor({ color: '#b42318' });
    await chrome.action.setBadgeText({ text: '!' });
    await failCapture(task, message);
  } finally {
    if (tabId) {
      trackedCaptureTabs.delete(tabId);
      mediaRequestsByTab.delete(tabId);
    }
    activeCaptures.delete(task.captureId);
  }
}

async function appBase() {
  const stored = await chrome.storage.sync.get('appBase');
  try {
    return new URL(stored.appBase || DEFAULT_APP_BASE).origin;
  } catch {
    return DEFAULT_APP_BASE;
  }
}

async function openCurrentInApp(sourcePageUrl, sourceTabId) {
  const base = await appBase();
  if (sourceTabId) {
    await chrome.storage.session.set({
      pendingSource: { sourcePageUrl, tabId: sourceTabId, expiresAt: Date.now() + 5 * 60_000 },
    });
  }
  const targetUrl = `${base}/director-inspiration?capture=1&capture_url=${encodeURIComponent(sourcePageUrl)}`;
  const tabs = await chrome.tabs.query({});
  const existing = tabs.find((tab) => tab.id && tab.url?.startsWith(`${base}/`));
  if (existing?.id) {
    await chrome.tabs.update(existing.id, { url: targetUrl, active: true });
    if (existing.windowId) await chrome.windows.update(existing.windowId, { focused: true });
    return;
  }
  await chrome.tabs.create({ url: targetUrl, active: true });
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'START_CAPTURE') {
    const task = message.payload || {};
    if (!task.captureId || !task.captureToken || !task.sourcePageUrl || !task.apiBase) {
      sendResponse({ ok: false, error: '采集任务参数不完整' });
      return false;
    }
    void runCapture(task);
    sendResponse({ ok: true });
    return false;
  }
  if (message?.type === 'OPEN_CURRENT_IN_APP') {
    void openCurrentInApp(message.sourcePageUrl, message.sourceTabId)
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error?.message || '无法打开编导灵感' }));
    return true;
  }
  return undefined;
});
