import {useEffect, useState} from 'react';
import {qianchuanApi, type QianchuanUpload} from '../api/client';
import {qianchuanError} from './QianchuanUploadModal';

export function uploadStatus(row?: QianchuanUpload) {
  if (!row) return '尚未上传素材';
  if (row.status === 'running') return row.phase === 'uploading' ? '正在上传素材' : '正在准备成片';
  return ({queued: '等待上传', uploaded: '已上传，待核验', unknown: '上传结果待确认',
    succeeded: '素材上传成功', failed: '上传未完成'} as Record<string, string>)[row.status] || '状态待确认';
}

export function useQianchuanUploads(videoId: number, revision: number) {
  const [uploads, setUploads] = useState<QianchuanUpload[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setUploads([]); setError('');
    async function load() {
      if (timer) clearTimeout(timer);
      if (document.hidden || !videoId) return;
      try {
        const rows = await qianchuanApi.uploads(videoId);
        if (!active) return;
        setUploads(rows); setError('');
        if (rows.some(row => row.auto_pending)) timer = setTimeout(load, 2000);
      } catch (err) { if (active) setError(qianchuanError(err)); }
    }
    void load();
    document.addEventListener('visibilitychange', load);
    return () => { active = false; if (timer) clearTimeout(timer); document.removeEventListener('visibilitychange', load); };
  }, [videoId, revision]);
  return {uploads, error};
}

export default function QianchuanUploadHistory({uploads, error, reload}: {
  uploads: QianchuanUpload[]; error: string; reload: () => void;
}) {
  const [busy, setBusy] = useState('');
  const [actionError, setActionError] = useState('');
  async function retry(row: QianchuanUpload) {
    setBusy(row.id); setActionError('');
    try { await qianchuanApi.retryUpload(row.id); reload(); }
    catch (err) { setActionError(qianchuanError(err)); }
    finally { setBusy(''); }
  }
  if (!uploads.length && !error) return null;
  return <section className="panel p-5">
    <div className="flex items-center justify-between"><h2 className="text-lg font-medium">千川素材上传记录</h2>
      <button className="btn-secondary" onClick={reload}>刷新记录</button></div>
    {(error || actionError) && <p role="alert" className="mt-3 text-sm text-red-600">{error || actionError}</p>}
    <div className="mt-4 space-y-3">{uploads.map(row => <div key={row.id} className="rounded border border-[var(--border)] p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-3"><span>{row.advertiser_name || '千川账户'} · {row.advertiser_id}</span>
        <strong>{uploadStatus(row)}</strong></div>
      <p className="mt-2 break-all text-[var(--muted)]">{row.filename} · {row.is_aigc ? '含 AI 生成内容' : '非 AI 生成内容'}</p>
      {row.material_id && <p className="mt-2 break-all">素材 ID：{row.material_id}</p>}
      {row.video_id && <p className="mt-1 break-all">视频 ID：{row.video_id}</p>}
      {row.error_message && <p className="mt-2 text-amber-700">{row.error_message}</p>}
      <div className="mt-3 flex items-center justify-between"><time className="text-xs text-[var(--muted)]">
        {new Date(row.updated_at.endsWith('Z') ? row.updated_at : row.updated_at + 'Z').toLocaleString('zh-CN')}</time>
        {(row.can_retry || row.can_verify) && <button className="btn-secondary" disabled={Boolean(busy)} onClick={() => void retry(row)}>
          {row.can_verify ? '重新核验' : '重试上传'}</button>}</div>
    </div>)}</div>
  </section>;
}
