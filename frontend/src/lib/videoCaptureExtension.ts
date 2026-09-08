import { directorApi } from '../api/client';
import expectedBuild from './videoCaptureBuild.json';

const CHANNEL = 'enrui-video-capture-v1';

interface BridgeResponse {
  ok?: boolean;
  error?: string;
  version?: string;
  build?: string;
}

export interface CaptureStartPayload {
  captureId: string;
  captureToken: string;
  projectId: number;
  sourcePageUrl: string;
  apiBase: string;
  maxBytes: number;
}

export class CaptureExtensionError extends Error {}

function bridgeRequest(type: string, payload: Record<string, unknown>, timeoutMs: number): Promise<BridgeResponse> {
  const requestId = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      window.removeEventListener('message', onMessage);
      reject(new CaptureExtensionError('未检测到视频采集插件, 请安装或更新后刷新页面'));
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

async function requireExtension(): Promise<void> {
  let response: BridgeResponse;
  try { response = await bridgeRequest('PING', {}, 1500); }
  catch { response = await bridgeRequest('PING', {}, 1500); }
  if (response.version !== expectedBuild.version || response.build !== expectedBuild.build) {
    throw new CaptureExtensionError(`请更新视频采集插件至 ${expectedBuild.version}, 并在扩展管理页重新加载后刷新当前页面`);
  }
}

export async function startVideoCapture(payload: CaptureStartPayload): Promise<void> {
  await bridgeRequest('START_CAPTURE', payload as unknown as Record<string, unknown>, 5000);
}

export async function createVideoCapture(sourcePageUrl: string, projectId?: number) {
  await requireExtension();
  const session = await directorApi.createCapture(sourcePageUrl, '', projectId);
  try {
    await startVideoCapture({
      captureId: session.capture.id, captureToken: session.capture.token, projectId: session.project.id,
      sourcePageUrl: session.capture.source_page_url, apiBase: window.location.origin, maxBytes: session.capture.max_bytes,
    });
  } catch (error) {
    await directorApi.failCapture(session.capture.id, session.capture.token, error instanceof Error ? error.message : '插件启动失败').catch(() => undefined);
    throw error;
  }
  return session.project;
}

export async function openCaptureSource(sourcePageUrl: string): Promise<void> {
  await requireExtension();
  await bridgeRequest('OPEN_SOURCE', { sourcePageUrl }, 10_000);
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
