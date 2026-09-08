(() => {
  const { videoMimeFromBytes } = globalThis.EnruiCaptureUtils;
  const MAX_VIDEO_BYTES = 300 * 1024 * 1024;

  function downloadError(code, status = 0, mime = '', size = 0, kind = '') {
    return Object.assign(new Error(code), { code, status, mime: mime.slice(0, 80), size, kind });
  }

  function bodyKind(bytes) {
    const mime = videoMimeFromBytes(bytes);
    if (mime) return mime === 'video/webm' ? 'webm' : 'mp4';
    const text = new TextDecoder().decode(bytes.slice(0, 128)).trimStart().toLowerCase();
    if (text.startsWith('<!doctype') || text.startsWith('<html')) return 'html';
    if (text.startsWith('{') || text.startsWith('[')) return 'json';
    if (text.startsWith('#extm3u') || text.includes('<mpd')) return 'playlist';
    return 'unknown';
  }

  async function readVideoResponse(response, maxBytes = MAX_VIDEO_BYTES) {
    const mime = (response.headers.get('content-type') || '').split(';')[0].toLowerCase();
    const declared = Number(response.headers.get('content-length')) || 0;
    const range = response.headers.get('content-range') || '';
    const fullRange = range.match(/^bytes 0-(\d+)\/(\d+)$/);
    let reader;
    let complete = false;
    const fail = (code, size = 0, kind = '') => { throw downloadError(code, response.status, mime, size, kind); };
    try {
      if (!response.ok) fail('http_error');
      if (declared > maxBytes) fail('too_large', declared);
      if (response.status === 206 && (!fullRange || Number(fullRange[1]) + 1 !== Number(fullRange[2]))) fail('partial_video');
      if (!response.body) fail('empty_video');
      reader = response.body.getReader();
      const chunks = [];
      let size = 0;
      let prefix = new Uint8Array();
      for (;;) {
        const result = await reader.read();
        if (result.done) break;
        size += result.value.byteLength;
        if (size > maxBytes) fail('too_large', size);
        chunks.push(result.value);
        if (prefix.length < 512) {
          const next = new Uint8Array(Math.min(512, prefix.length + result.value.length));
          next.set(prefix);
          next.set(result.value.slice(0, next.length - prefix.length), prefix.length);
          prefix = next;
        }
        if (prefix.length >= 512 && !videoMimeFromBytes(prefix)) fail('not_video', size, bodyKind(prefix));
      }
      if (!size) fail('empty_video');
      const detected = videoMimeFromBytes(prefix);
      if (!detected) fail('not_video', size, bodyKind(prefix));
      if ((fullRange && size !== Number(fullRange[2]))
        || (!response.headers.get('content-encoding') && declared && size !== declared)) fail('partial_video', size);
      complete = true;
      return { blob: new Blob(chunks, { type: detected }), contentType: detected };
    } finally {
      if (!complete) {
        if (reader) await reader.cancel().catch(() => undefined);
        else await response.body?.cancel().catch(() => undefined);
      }
    }
  }

  globalThis.EnruiCaptureDownload = { MAX_VIDEO_BYTES, readVideoResponse, bodyKind };
  if (typeof module === 'object' && module.exports) module.exports = globalThis.EnruiCaptureDownload;
})();
