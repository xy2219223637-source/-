import {useEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {isAxiosError} from 'axios';
import {Loader2, X} from 'lucide-react';
import {qianchuanApi, type QianchuanUpload, type QianchuanAccounts} from '../api/client';

const statusLabels: Record<string, string> = {
  authorizing: '等待授权', sync_pending: '等待同步账户', syncing: '正在同步账户',
  active: '授权已连接', sync_failed: '账户同步失败', reauthorization_required: '需要重新授权', disabled: '已停用',
};

export function qianchuanError(error: unknown): string {
  return isAxiosError(error) && typeof error.response?.data?.detail === 'string'
    ? error.response.data.detail : '请求失败，请稍后重试';
}

export default function QianchuanUploadModal({videoAssetId, filename: originalFilename, onClose, onUploaded}: {
  videoAssetId: number; filename: string; onClose: () => void; onUploaded: (upload: QianchuanUpload) => void;
}) {
  const [data, setData] = useState<QianchuanAccounts | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [selection, setSelection] = useState('');
  const [filename, setFilename] = useState(originalFilename.slice(0, 255));
  const [aigc, setAigc] = useState('');
  const [revision, setRevision] = useState(0);
  const dialogRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    return () => { if (opener?.isConnected) opener.focus(); };
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const result = await qianchuanApi.accounts();
        if (!active) return;
        setData(result);
        setError('');
        if (result.connections.some(c => ['sync_pending', 'syncing'].includes(c.status)) && !document.hidden) {
          timer = setTimeout(load, 2000);
        }
      } catch (err) { if (active) setError(qianchuanError(err)); }
    }
    function visibility() {
      if (timer) clearTimeout(timer);
      if (!document.hidden) void load();
    }
    void load();
    document.addEventListener('visibilitychange', visibility);
    return () => { active = false; if (timer) clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [revision]);

  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) onClose();
      if (event.key !== 'Tab') return;
      const controls = dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)');
      const first = controls?.[0], last = controls?.[controls.length - 1];
      if (!first || !last) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener('keydown', keyboard);
    return () => document.removeEventListener('keydown', keyboard);
  }, [busy, onClose]);

  async function perform(action: () => Promise<unknown>) {
    setBusy(true); setError('');
    try { await action(); setRevision(value => value + 1); }
    catch (err) { setError(qianchuanError(err)); }
    finally { setBusy(false); }
  }
  function authorize(connectionId?: string) {
    return perform(async () => {
      const result = await qianchuanApi.authorize(videoAssetId, connectionId);
      window.location.assign(result.authorization_url);
    });
  }
  const selected = data?.connections.flatMap(c => c.status === 'active' ? c.accounts : []).find(a => a.id === selection);
  const ready = data?.configuration.status === 'ready';
  const canUpload = Boolean(selected && ready && !busy && filename.trim() && aigc);
  async function upload() {
    if (!canUpload || !selected) return;
    setBusy(true); setError('');
    try {
      const result = await qianchuanApi.upload(videoAssetId, selected.id, filename.trim(), aigc === 'yes');
      onUploaded(result); onClose();
    } catch (err) { setError(qianchuanError(err)); }
    finally { setBusy(false); }
  }

  return createPortal(<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
    <section ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="qianchuan-title" className="panel max-h-[85vh] w-full max-w-2xl overflow-y-auto p-6 outline-none">
      <header className="flex items-center justify-between gap-4">
        <h2 id="qianchuan-title" className="text-xl font-medium">连接千川账户</h2>
        <button className="btn-secondary" aria-label="关闭" onClick={onClose} disabled={busy}><X size={16}/></button>
      </header>
      <p className="mt-3 text-sm text-[var(--muted)]">选择目标账户，将本条成片上传至千川素材库。支持不超过 50 MB 的 MP4；上传不会创建投放计划。</p>
      {error && <p role="alert" className="mt-4 text-sm text-red-600">{error}</p>}
      {!data && !error && <div className="flex items-center gap-2 py-8 text-sm"><Loader2 size={16} className="animate-spin"/>正在读取账户</div>}
      {data && !ready && <div className="mt-4 rounded bg-amber-50 p-4 text-sm text-amber-900">千川授权功能尚未启用或配置不完整，请管理员配置应用凭据和回调地址。</div>}
      {data?.can_manage && ready && <button className="btn-primary mt-4" disabled={busy} onClick={() => void authorize()}>添加官方授权</button>}
      {data && !data.can_manage && !data.connections.some(c => c.accounts.length > 0) && <p className="mt-4 text-sm">当前没有可选账户，请联系企业管理员完成授权。</p>}
      <div className="mt-4 space-y-4">
        {data?.connections.map((connection, index) => <div key={connection.id} className="rounded border border-[var(--border)] p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-medium">授权连接 {index + 1} · {statusLabels[connection.status] || '状态待确认'}</span>
            {data.can_manage && <div className="flex flex-wrap gap-3 text-xs">
              <button className="underline" disabled={busy || !ready || !['active', 'sync_failed'].includes(connection.status)}
                onClick={() => void perform(() => qianchuanApi.sync(connection.id))}>刷新账户</button>
              <button className="underline" disabled={busy || !ready} onClick={() => void authorize(connection.id)}>重新授权</button>
              <button className="underline" disabled={busy || connection.status === 'disabled'} onClick={() => {
                if (window.confirm('停用本系统连接后需要重新授权；这不会撤销平台上的应用授权。')) {
                  void perform(() => qianchuanApi.disable(connection.id));
                }
              }}>停用连接</button>
              <button className="text-red-600 underline disabled:opacity-50" disabled={busy} onClick={() => {
                if (window.confirm('删除此授权连接及其账户列表？已有上传记录和千川素材会保留；再次使用需添加新的授权。')) {
                  void perform(async () => {
                    await qianchuanApi.remove(connection.id);
                    if (connection.accounts.some(account => account.id === selection)) setSelection('');
                    setData(current => current ? {...current, connections: current.connections.filter(item => item.id !== connection.id)} : current);
                  });
                }
              }}>删除</button>
            </div>}
          </div>
          {connection.error && <p className="mt-2 text-sm text-red-600">{connection.error}</p>}
          {connection.warnings.map(warning => <p key={warning} className="mt-2 text-xs text-amber-700">{warning}</p>)}
          {connection.status === 'active' && !connection.accounts.length && <p className="mt-3 text-sm text-[var(--muted)]">本次授权未解析到可选千川账户，请核对授权范围。</p>}
          {connection.accounts.map(account => <label key={account.id} className="mt-3 flex cursor-pointer items-start gap-3 rounded bg-[var(--bg)] p-3">
            <input type="radio" name="qianchuan-account" className="mt-1" checked={selection === account.id}
              onChange={() => setSelection(account.id)} disabled={busy || !ready}/>
            <span className="text-sm"><span className="block font-medium">{account.advertiser_name || '千川账户'} · {account.advertiser_id}</span>
              <span className="mt-1 block text-xs text-[var(--muted)]">授权来源：{account.source_account_name || account.source_account_type}（{account.source_account_id}）</span></span>
          </label>)}
        </div>)}
      </div>
      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="text-sm">素材名称<input className="mt-2 w-full rounded border border-[var(--border)] p-2" value={filename}
          maxLength={255} disabled={busy} onChange={event => setFilename(event.target.value)}/></label>
        <label className="text-sm">素材是否包含 AI 生成内容<select className="input-select mt-2 block w-full" value={aigc}
          disabled={busy} onChange={event => setAigc(event.target.value)}>
          <option value="">请选择</option><option value="yes">是，包含 AI 生成内容</option><option value="no">否</option>
        </select></label>
      </div>
      <footer className="mt-5 flex justify-end gap-3">
        <button className="btn-secondary" disabled={busy} onClick={() => setRevision(value => value + 1)}>重新读取</button>
        <button className="btn-primary qianchuan-upload-button" disabled={!canUpload} onClick={() => void upload()}>
          {busy && <Loader2 size={16} className="animate-spin"/>}上传素材</button>
      </footer>
    </section>
  </div>, document.body);
}
