const CHANNEL = 'enrui-video-capture-v1';

interface BridgeResponse {
  ok?: boolean;
  error?: string;
}

export interface CaptureStartPayload {
  captureId: string;
  captureToken: string;
  projectId: number;
  sourcePageUrl: string;
  apiBase: string;
}

function bridgeRequest(type: string, payload: Record<string, unknown>, timeoutMs: number): Promise<BridgeResponse> {
  const requestId = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', onMessage);
      reject(new Error('未检测到视频采集插件'));
    }, timeoutMs);

    function onMessage(event: MessageEvent) {
      if (event.source !== window || event.origin !== window.location.origin) return;
      const message = event.data;
      if (!message || message.channel !== CHANNEL || message.requestId !== requestId || message.type !== `${type}_RESULT`) return;
      window.clearTimeout(timer);
      window.removeEventListener('message', onMessage);
      if (message.payload?.ok) resolve(message.payload);
      else reject(new Error(message.payload?.error || '视频采集插件响应失败'));
    }

    window.addEventListener('message', onMessage);
    window.postMessage({ channel: CHANNEL, type, requestId, payload }, window.location.origin);
  });
}

export async function hasVideoCaptureExtension(): Promise<boolean> {
  try {
    await bridgeRequest('PING', {}, 900);
    return true;
  } catch {
    return false;
  }
}

export async function startVideoCapture(payload: CaptureStartPayload): Promise<void> {
  await bridgeRequest('START_CAPTURE', payload as unknown as Record<string, unknown>, 2500);
}

export function extractSharedVideoUrl(value: string): string {
  const match = value.trim().match(/https?:\/\/[^\s]+/i);
  return (match?.[0] || value.trim()).replace(/[\])}>，。！？、；;'"']+$/u, '');
}

export function isDirectVideoUrl(value: string): boolean {
  try {
    return /\.(mp4|mov|m4v|webm)$/i.test(new URL(value).pathname);
  } catch {
    return false;
  }
}
