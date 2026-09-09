async function initialize() {
  const settings = await chrome.storage.sync.get(['autoClose', 'appBase']);
  const autoClose = document.querySelector('#auto-close');
  autoClose.checked = settings.autoClose !== false;
  document.querySelector('#page-title').textContent = `版本 ${chrome.runtime.getManifest().version}`;
  document.querySelector('#page-url').textContent = `构建 ${globalThis.EnruiCaptureBuild.build}`;
  document.querySelector('#platform').textContent = globalThis.EnruiCaptureUtils.trustedAppOrigin(settings.appBase) || globalThis.EnruiCaptureUtils.DEFAULT_APP_BASE;
  autoClose.addEventListener('change', () => { void chrome.storage.sync.set({ autoClose: autoClose.checked }); });
  document.querySelector('#collect').addEventListener('click', async () => {
    try {
      const response = await chrome.runtime.sendMessage({ type: 'OPEN_APP' });
      if (!response?.ok) throw new Error(response?.error || '无法打开编导灵感');
      window.close();
    } catch (error) { document.querySelector('#status').textContent = error.message; }
  });
}

void initialize();
