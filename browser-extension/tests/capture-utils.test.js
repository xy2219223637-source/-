const test = require('node:test');
const assert = require('node:assert/strict');

const {
  capturePageUrl,
  looksLikeVideo,
  normalizedMediaUrl,
  platformNameForUrl,
  videoMimeFromBytes,
} = require('../capture-utils.js');

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
