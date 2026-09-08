const test = require('node:test');
const assert = require('node:assert/strict');

const {
  DEFAULT_APP_BASE,
  capturePageUrl,
  looksLikeVideo,
  normalizedMediaUrl,
  platformNameForUrl,
  trustedAppOrigin,
  videoMimeFromBytes,
  douyinCandidates,
  douyinVideoId,
} = require('../capture-utils.js');

test('uses production as the default app and accepts only configured app origins', () => {
  assert.equal(DEFAULT_APP_BASE, 'https://waynepartners.xyz');
  assert.equal(trustedAppOrigin('https://waynepartners.xyz/director-inspiration'), 'https://waynepartners.xyz');
  assert.equal(trustedAppOrigin('https://www.waynepartners.xyz/director-inspiration'), 'https://www.waynepartners.xyz');
  assert.equal(trustedAppOrigin('http://127.0.0.1:5173/director-inspiration'), 'http://127.0.0.1:5173');
  assert.equal(trustedAppOrigin('https://untrusted.example/director-inspiration'), '');
});

test('converts a Douyin jingxuan modal link to its canonical video page', () => {
  const source = 'https://www.douyin.com/jingxuan/search/test?aid=abc&modal_id=7621469168370856306&type=general';
  assert.equal(capturePageUrl(source), 'https://www.douyin.com/video/7621469168370856306');
});

test('does not rewrite non-Douyin or malformed modal links', () => {
  assert.equal(capturePageUrl('https://www.xiaohongshu.com/explore/abc'), 'https://www.xiaohongshu.com/explore/abc');
  assert.equal(capturePageUrl('https://www.douyin.com/jingxuan?modal_id=bad'), 'https://www.douyin.com/jingxuan?modal_id=bad');
});

test('recognizes platform CDN video URLs', () => {
  assert.equal(looksLikeVideo('https://v3-web.douyinvod.com/example/tos-cn-video.mp4'), true);
  assert.equal(looksLikeVideo('https://sns-video-hw.xhscdn.com/example'), true);
  assert.equal(looksLikeVideo('https://example.com/image.jpg'), false);
});

test('normalizes escaped media URLs', () => {
  assert.equal(
    normalizedMediaUrl('https:\\/\\/example.com\\/video.mp4?x=1\\u0026y=2', 'https://example.com/page'),
    'https://example.com/video.mp4?x=1&y=2',
  );
  assert.equal(normalizedMediaUrl('blob:https://example.com/id', 'https://example.com/page'), '');
});

test('detects supported platforms', () => {
  assert.equal(platformNameForUrl('https://www.douyin.com/video/1'), 'douyin');
  assert.equal(platformNameForUrl('https://example.com/video.mp4'), 'generic');
});

test('detects MP4 and WebM file signatures', () => {
  assert.equal(videoMimeFromBytes(Uint8Array.from([0, 0, 0, 24, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d])), 'video/mp4');
  assert.equal(videoMimeFromBytes(Uint8Array.from([0x1a, 0x45, 0xdf, 0xa3, 0x9f])), 'video/webm');
  assert.equal(videoMimeFromBytes(Uint8Array.from([0x7b, 0x22, 0x64, 0x61, 0x74, 0x61, 0x22, 0x3a])), '');
});

test('matches exact target IDs and rejects advertisements, recommendations and playlists', () => {
  const target = '7666807663723875611';
  const item = (id, url, extra = {}) => ({ aweme_id: id, video: { play_addr: { url_list: [url] } }, ...extra });
  const rows = [
    item('7517519984810659132', 'https://cdn.example/other.mp4'),
    item(target, 'https://cdn.example/ad.mp4', { is_ads: true }),
    item(target, 'https://cdn.example/index.m3u8'),
    item(target, 'https://cdn.example/opaque?sign=abc'),
  ];
  const result = douyinCandidates({ aweme_list: rows }, target, 'https://www.douyin.com');
  assert.deepEqual(result.map((entry) => entry.url), ['https://cdn.example/opaque?sign=abc']);
  assert.equal(douyinCandidates(rows, '', 'https://www.douyin.com').length, 0);
  assert.equal(douyinVideoId(`https://www.douyin.com/jingxuan?modal_id=${target}`), target);
  assert.equal(douyinVideoId(`https://evil.example/video/${target}`), '');
});

test('reads camelCase route state and preserves 64-bit IDs without numeric rounding', () => {
  const target = '7666807663723875611';
  const payload = { loaderData: { detail: { awemeId: target, video: { playAddr: [{ urlList: [] }] } } } };
  payload.loaderData.detail.video.playAddr = { urlList: ['https://cdn.example/target.mp4'] };
  assert.equal(douyinCandidates(payload, target, 'https://www.douyin.com').length, 1);
  payload.loaderData.detail.video.playAddr = ['https://cdn.example/target.mp4'];
  payload.loaderData.detail.video.bitRateList = { unexpected: true };
  assert.equal(douyinCandidates(payload, target, 'https://www.douyin.com').length, 1);
  payload.loaderData.detail.awemeId = Number(target);
  assert.equal(douyinCandidates(payload, target, 'https://www.douyin.com').length, 0);
});

test('rejects broad video/play keyword matches and unsupported stream segments', () => {
  for (const url of ['https://example.com/api/video/play/config', 'https://example.com/a.m3u8', 'https://example.com/video.m4s', 'https://example.com/video.mpd']) {
    assert.equal(looksLikeVideo(url), false);
  }
});
