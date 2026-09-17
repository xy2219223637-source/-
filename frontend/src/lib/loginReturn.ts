// Only same-origin paths may be used after login, including encoded inputs.
export function safeReturnPath(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//') || /[\\\u0000-\u0020]/.test(value)) return '/';
  try {
    const decoded = decodeURIComponent(value);
    if (decoded.startsWith('//') || /[\\\u0000-\u001f]/.test(decoded)) return '/';
    const parsed = new URL(value, window.location.origin);
    // Normalizing /a/..//host produces //host; never return that as a navigation target.
    if (parsed.origin !== window.location.origin || parsed.pathname.startsWith('//') || /^\/login(?:\/|$)/.test(decodeURIComponent(parsed.pathname))) return '/';
    return parsed.pathname + parsed.search + parsed.hash;
  } catch {
    return '/';
  }
}

export function loginUrl(): string {
  const path = safeReturnPath(window.location.pathname + window.location.search + window.location.hash);
  return `/login?returnTo=${encodeURIComponent(path)}`;
}
