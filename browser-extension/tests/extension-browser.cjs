const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const { mkdtemp, readFile } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { resolve, join } = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

test('installed extension validates media and retries retained source pages without foreground navigation', { timeout: 60_000 }, async () => {
  const extension = resolve(__dirname, '..');
  const profile = await mkdtemp(join(tmpdir(), 'enrui-extension-test-'));
  const target = '7666807663723875611';
  const verificationTarget = '7518007249358490940';
  const captureId = 'a'.repeat(32);
  const records = [];
  let uploaded;
  let verification = false;
  const server = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    if (request.url.endsWith('/upload')) uploaded = body;
    else if (request.method !== 'GET') records.push(JSON.parse(body.toString()));
    response.setHeader('Content-Type', request.url.startsWith('/api/') ? 'application/json' : 'text/html');
    response.end(request.url.startsWith('/api/') ? '{}' : '<html><head><title>Capture test</title></head><body>Capture test</body></html>');
  });
  let port;
  for (const candidate of [8080, 5173]) {
    try {
      await new Promise((resolve, reject) => {
        const fail = (error) => { server.off('listening', ready); reject(error); };
        const ready = () => { server.off('error', fail); resolve(); };
        server.once('error', fail);
        server.once('listening', ready);
        server.listen(candidate, '127.0.0.1');
      });
      port = candidate;
      break;
    } catch (error) { if (error.code !== 'EADDRINUSE') throw error; }
  }
  assert.ok(port, 'A trusted local app port (8080 or 5173) must be available');
  let context;
  try {
    context = await chromium.launchPersistentContext(profile, {
      channel: 'chromium', headless: process.env.HEADED !== '1',
      proxy: { server: 'http://127.0.0.1:9', bypass: '127.0.0.1,localhost' },
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`],
    });
    const errors = [];
    context.on('weberror', (error) => errors.push(error.error().message));
    const mp4 = Buffer.from([0, 0, 0, 24, 102, 116, 121, 112, 105, 115, 111, 109, ...new Array(1012).fill(0)]);
    const item = (id, url) => ({ aweme_id: id, desc: 'Target video', video: { play_addr: { url_list: [url] } } });
    let originalRequests = 0;
    await context.route('https://www.douyin.com/**', async (route) => {
      const requestUrl = new URL(route.request().url());
      const fixtureTarget = requestUrl.searchParams.get('aweme_id') || requestUrl.pathname.match(/\/video\/(\d+)/)?.[1] || target;
      if (route.request().url().includes('/aweme/v1/web/aweme/detail')) {
        originalRequests++;
        await route.fulfill({ json: { aweme_detail: item(fixtureTarget, 'https://media.example/target.mp4?signature=secret-value') } });
        return;
      }
      if (verification) {
        await route.fulfill({ contentType: 'text/html; charset=utf-8', body: '<html><body><div role="dialog">请完成安全验证</div></body></html>' });
        return;
      }
      await route.fulfill({ contentType: 'text/html', body: `<html><head><title>Target</title></head><body><script>
        window._ROUTER_DATA = {items: ${JSON.stringify([item('7517519984810659132', 'https://media.example/recommendation.mp4'), item(fixtureTarget, 'https://media.example/config?signature=private-value')])}};
        setTimeout(() => fetch('/aweme/v1/web/aweme/detail/?aweme_id=${fixtureTarget}').then(r=>r.json()), 2500);
      </script></body></html>` });
    });
    let recommendationRequested = false;
    await context.route('https://media.example/**', async (route) => {
      const url = route.request().url();
      if (url.includes('recommendation')) recommendationRequested = true;
      await route.fulfill({
        status: 200, contentType: 'video/mp4',
        headers: { 'Access-Control-Allow-Origin': 'https://www.douyin.com', 'Access-Control-Allow-Credentials': 'true' },
        body: url.includes('target.mp4') ? mp4 : Buffer.from('<html>not a video</html>'),
      });
    });
    const app = await context.newPage();
    // Playwright cannot reliably route the first navigation of extension-created tabs.
    // Seed retained tabs so every platform response is a fixture, with external traffic blocked.
    const retain = async (url) => {
      const page = await context.newPage();
      await page.goto(url);
      const worker = context.serviceWorkers()[0] || await context.waitForEvent('serviceworker');
      await worker.evaluate(async (url) => {
        const tabs = await chrome.tabs.query({});
        const tab = tabs.find(tab => tab.url === url);
        await chrome.storage.session.set({ [`source:${url}`]: { tabId: tab.id, created: true, expiresAt: Date.now() + 60_000 } });
      }, url);
      return page;
    };
    await retain(`https://www.douyin.com/video/${target}`);
    await app.goto(`http://127.0.0.1:${port}/director-inspiration`);
    await app.bringToFront();
    const result = await app.evaluate(({ target, captureId }) => new Promise((resolve) => {
      const requestId = crypto.randomUUID();
      window.addEventListener('message', function receive(event) {
        if (event.data.requestId !== requestId || event.data.type !== 'START_CAPTURE_RESULT') return;
        window.removeEventListener('message', receive);
        resolve(event.data.payload);
      });
      window.postMessage({ channel: 'enrui-video-capture-v1', type: 'START_CAPTURE', requestId, payload: {
        captureId, captureToken: 'test-only-token', sourcePageUrl: `https://www.douyin.com/video/${target}`,
        apiBase: location.origin, projectId: 1, maxBytes: 2 * 1024 * 1024,
      } }, location.origin);
    }), { target, captureId });
    assert.equal(result.ok, true);
    const deadline = Date.now() + 30_000;
    while (!uploaded && Date.now() < deadline) await new Promise((resolve) => setTimeout(resolve, 100));
    if (!uploaded) {
      console.log('Capture diagnostics', records, errors);
      for (const page of context.pages()) console.log('Page state', page.url(), await Promise.race([page.evaluate(() => ({ title: document.title, state: window._ROUTER_DATA, helper: !!window.EnruiCaptureUtils })).catch(() => null), new Promise(r => setTimeout(() => r('unresponsive'), 2000))]));
      for (const worker of context.serviceWorkers()) console.log('Worker state', await worker.evaluate(async () => {
        const tabs = await chrome.tabs.query({});
        return Promise.all(tabs.map(async (tab) => ({ url: tab.url, result: await Promise.race([chrome.tabs.sendMessage(tab.id, { type: 'EXTRACT_VIDEO' }).catch((error) => error.message), new Promise(r => setTimeout(() => r('no response'), 2000))]) })));
      }));
    }
    assert.ok(uploaded, JSON.stringify(records));
    assert.deepEqual(uploaded, mp4);
    assert.equal(recommendationRequested, false);
    assert.ok(originalRequests > 0);
    assert.ok(records.some((record) => record.diagnostics?.attempts.some((attempt) => attempt.code === 'not_video')));
    assert.ok(records.some((record) => record.diagnostics?.attempts.some((attempt) => attempt.code === 'downloaded' && attempt.context === 'page')));
    assert.ok(!JSON.stringify(records).includes('secret-value'));
    assert.ok(!JSON.stringify(records).includes('private-value'));
    assert.equal(await app.evaluate(() => document.visibilityState), 'visible');
    await new Promise((resolve) => setTimeout(resolve, 800));
    assert.equal(context.pages().some((page) => page.url().includes('douyin.com')), false);
    assert.deepEqual(errors, []);
    const build = JSON.parse(await readFile(resolve(extension, '../frontend/src/lib/videoCaptureBuild.json'), 'utf8'));
    assert.equal(records.at(-1).diagnostics.build, build.build);

    const send = (type, payload) => app.evaluate(({ type, payload }) => new Promise((resolve, reject) => {
      const requestId = crypto.randomUUID();
      const timeout = setTimeout(() => reject(new Error('Extension bridge timeout')), 5000);
      window.addEventListener('message', function receive(event) {
        if (event.data.requestId !== requestId || event.data.type !== `${type}_RESULT`) return;
        window.removeEventListener('message', receive);
        clearTimeout(timeout);
        resolve(event.data.payload);
      });
      window.postMessage({ channel: 'enrui-video-capture-v1', type, requestId, payload }, location.origin);
    }), { type, payload });
    verification = true;
    uploaded = undefined;
    records.length = 0;
    const nextTask = { captureId: 'b'.repeat(32), captureToken: 'second-test-token', sourcePageUrl: `https://www.douyin.com/video/${verificationTarget}`, apiBase: `http://127.0.0.1:${port}`, projectId: 2, maxBytes: 2 * 1024 * 1024 };
    const retainedPage = await retain(nextTask.sourcePageUrl);
    await app.bringToFront();
    assert.equal((await send('START_CAPTURE', nextTask)).ok, true);
    const failedDeadline = Date.now() + 10_000;
    while (!records.some(record => record.message) && Date.now() < failedDeadline) await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(records.find(record => record.message)?.diagnostics.last_code, 'verification_required', JSON.stringify({ records, errors, pages: context.pages().map(page => page.url()) }));
    assert.equal(uploaded, undefined);
    assert.ok(context.pages().includes(retainedPage));
    assert.equal(await app.evaluate(() => document.visibilityState), 'visible');
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.equal((await send('OPEN_SOURCE', { sourcePageUrl: nextTask.sourcePageUrl })).ok, true);
    assert.equal(await retainedPage.evaluate(() => document.visibilityState), 'visible');
    verification = false;
    await retainedPage.reload();
    await app.bringToFront();
    assert.equal((await send('START_CAPTURE', { ...nextTask, captureId: 'c'.repeat(32) })).ok, true);
    const retryDeadline = Date.now() + 10_000;
    while (!uploaded && Date.now() < retryDeadline) await new Promise(resolve => setTimeout(resolve, 100));
    assert.deepEqual(uploaded, mp4);
    assert.equal(await app.evaluate(() => document.visibilityState), 'visible');
    assert.deepEqual(errors, []);
  } finally {
    await context?.close();
    await new Promise((resolve) => server.close(resolve));
  }
});
