(() => {
  function platformNameForUrl(value) {
    try {
      const host = new URL(value).hostname.toLowerCase();
      if (host === 'douyin.com' || host.endsWith('.douyin.com')) return 'douyin';
      if (host === 'xiaohongshu.com' || host.endsWith('.xiaohongshu.com') || host === 'xhslink.com' || host.endsWith('.xhslink.com')) return 'xiaohongshu';
      if (host === 'kuaishou.com' || host.endsWith('.kuaishou.com')) return 'kuaishou';
      if (host === 'tiktok.com' || host.endsWith('.tiktok.com')) return 'tiktok';
    } catch {
      return 'generic';
    }
    return 'generic';
  }

  function capturePageUrl(value) {
    try {
      const url = new URL(value);
      if (platformNameForUrl(url.href) !== 'douyin') return url.href;
      const modalId = url.searchParams.get('modal_id') || '';
      if (/^\d{15,24}$/.test(modalId) && !/^\/video\/\d+/.test(url.pathname)) {
        return `https://www.douyin.com/video/${modalId}`;
      }
      return url.href;
    } catch {
      return value;
    }
  }

  function decodeEscapedUrl(value) {
    let decoded = value.trim()
      .replaceAll('\\u002F', '/')
      .replaceAll('\\u002f', '/')
      .replaceAll('\\u0026', '&')
      .replaceAll('\\u003D', '=')
      .replaceAll('\\u003d', '=')
      .replaceAll('\\u003F', '?')
      .replaceAll('\\u003f', '?')
      .replaceAll('\\/', '/')
      .replaceAll('&amp;', '&');
    if (/^https?%3a%2f%2f/i.test(decoded)) {
      try {
        decoded = decodeURIComponent(decoded);
      } catch {
        // Keep the original value when it contains malformed percent escapes.
      }
    }
    return decoded;
  }

  function normalizedMediaUrl(value, baseUrl) {
    if (typeof value !== 'string' || !value.trim()) return '';
    let url = decodeEscapedUrl(value);
    if (url.startsWith('//')) {
      try {
        url = `${new URL(baseUrl).protocol}${url}`;
      } catch {
        url = `https:${url}`;
      }
    }
    try {
      url = new URL(url, baseUrl).href;
    } catch {
      return '';
    }
    return url.startsWith('http://') || url.startsWith('https://') ? url : '';
  }

  function looksLikeVideo(value) {
    if (typeof value !== 'string') return false;
    const lower = value.toLowerCase();
    return /\.(mp4|mov|m4v|webm|m3u8)(?:$|[?#])/.test(lower)
      || lower.includes('/video/tos')
      || lower.includes('/aweme/v1/play')
      || lower.includes('video_id=')
      || lower.includes('videoplayback')
      || lower.includes('play_addr')
      || lower.includes('playwm')
      || lower.includes('mime_type=video')
      || lower.includes('douyinvod.com')
      || lower.includes('bytevcloudvod.com')
      || /sns-video-[^.]+\.xhscdn\.com/.test(lower)
      || (lower.includes('kwaicdn.com') && lower.includes('video'))
      || (lower.includes('video') && lower.includes('play'));
  }

  const captureUtils = Object.freeze({
    capturePageUrl,
    looksLikeVideo,
    normalizedMediaUrl,
    platformNameForUrl,
  });

  globalThis.EnruiCaptureUtils = captureUtils;
  if (typeof module === 'object' && module.exports) module.exports = captureUtils;
})();
