const test = require('node:test');
const assert = require('node:assert/strict');
require('../capture-utils.js');
const { readVideoResponse } = require('../capture-download.js');

const mp4 = Uint8Array.from([0, 0, 0, 24, 102, 116, 121, 112, 105, 115, 111, 109, ...new Array(1012).fill(0)]);

test('accepts a binary video response without a video MIME type', async () => {
  const video = await readVideoResponse(new Response(mp4, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Length': String(mp4.length) } }));
  assert.equal(video.contentType, 'video/mp4');
  assert.equal(video.blob.size, mp4.length);
});

test('rejects HTML or JSON even when the server claims video/mp4', async () => {
  for (const [body, kind] of [['<!doctype html><html>verify</html>', 'html'], ['{"error":"login required"}', 'json']]) {
    await assert.rejects(readVideoResponse(new Response(body, { headers: { 'Content-Type': 'video/mp4' } })), (error) => error.code === 'not_video' && error.kind === kind);
  }
});

test('rejects partial responses and truncated full responses', async () => {
  await assert.rejects(readVideoResponse(new Response(mp4, { status: 206, headers: { 'Content-Range': 'bytes 1024-2047/4096' } })), { code: 'partial_video' });
  await assert.rejects(readVideoResponse(new Response(mp4, { headers: { 'Content-Length': '4096' } })), { code: 'partial_video' });
});

test('enforces the limit before buffering an oversized response', async () => {
  await assert.rejects(readVideoResponse(new Response(mp4, { headers: { 'Content-Length': '4096' } }), 2048), { code: 'too_large' });
  await assert.rejects(readVideoResponse(new Response(mp4), 512), { code: 'too_large' });
});

test('keeps HTTP rejection separate from invalid media', async () => {
  await assert.rejects(readVideoResponse(new Response('forbidden', { status: 403 })), (error) => error.code === 'http_error' && error.status === 403);
});
