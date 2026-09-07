const DEFAULT_APP_BASE = 'http://127.0.0.1:8080';
const platformLabels = {
  douyin: '抖音视频',
  xiaohongshu: '小红书视频',
  kuaishou: '快手视频',
  tiktok: 'TikTok 视频',
  generic: '网页视频',
};

function platform(url) {
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host === 'douyin.com' || host.endsWith('.douyin.com')) return 'douyin';
    if (host.includes('xiaohongshu.com') || host.includes('xhslink.com')) return 'xiaohongshu';
    if (host === 'kuaishou.com' || host.endsWith('.kuaishou.com')) return 'kuaishou';
    if (host === 'tiktok.com' || host.endsWith('.tiktok.com')) return 'tiktok';
  } catch {
    return 'generic';
  }
  return 'generic';
}

async function initialize() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const controls = {
    appBase: document.querySelector('#app-base'),
    autoClose: document.querySelector('#auto-close'),
    collect: document.querySelector('#collect'),
    pageTitle: document.querySelector('#page-title'),
    pageUrl: document.querySelector('#page-url'),
    platform: document.querySelector('#platform'),
    status: document.querySelector('#status'),
  };
  const settings = await chrome.storage.sync.get(['appBase', 'autoClose']);
  controls.appBase.value = settings.appBase || DEFAULT_APP_BASE;
  controls.autoClose.checked = settings.autoClose !== false;
  controls.pageTitle.textContent = tab?.title || '当前页面';
  controls.pageUrl.textContent = tab?.url || '';
  controls.platform.textContent = platformLabels[platform(tab?.url || '')];
  controls.collect.disabled = !tab?.url || !/^https?:\/\//i.test(tab.url);

  controls.collect.addEventListener('click', async () => {
    controls.status.textContent = '';
    controls.collect.disabled = true;
    try {
      const appBase = new URL(controls.appBase.value).origin;
      await chrome.storage.sync.set({ appBase, autoClose: controls.autoClose.checked });
      const response = await chrome.runtime.sendMessage({ type: 'OPEN_CURRENT_IN_APP', sourcePageUrl: tab.url, sourceTabId: tab.id });
      if (!response?.ok) throw new Error(response?.error || '无法打开编导灵感');
      window.close();
    } catch (error) {
      controls.status.textContent = error?.message || '应用地址无效';
      controls.collect.disabled = false;
    }
  });
}

void initialize();
