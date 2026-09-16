import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, RefreshCw, Search, Send, X } from 'lucide-react';
import { directorApi } from '../api/client';
import type { DirectorProject, ShootingDirectory, ShootingTask } from '../types';
import { isNotificationOutcomeUnknown } from '../lib/notificationStatus';

export default function FeishuPushDialog({ project, onClose, onResult }: {
  project: DirectorProject;
  onClose: () => void;
  onResult: (task: ShootingTask) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [kind, setKind] = useState<'user' | 'chat'>('user');
  const [directory, setDirectory] = useState<ShootingDirectory | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const inFlight = useRef(false);
  const request = useRef({ signature: '', id: '' });

  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setDirectory(null);
    setSelected('');
    setError('');
    directorApi.shootingDirectory(kind, refresh > 0)
      .then((result) => { if (active) setDirectory(result); })
      .catch((err) => { if (active) setError(typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : '飞书名单加载失败，请重试'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [kind, refresh]);

  const items = directory?.type === kind ? directory.items : [];
  const filtered = items.filter((item) => `${item.name} ${item.department_name} ${item.id}`.toLowerCase().includes(query.trim().toLowerCase()));
  const recipient = items.find((item) => item.id === selected);
  const canSend = directory?.can_send !== false;
  const finish = (task: ShootingTask) => { onResult(task); onClose(); };

  async function send() {
    if (!recipient || loading || !canSend || inFlight.current || uncertain) return;
    inFlight.current = true;
    setBusy(true);
    setError('');
    const signature = JSON.stringify([project.id, kind, recipient.id, note.trim()]);
    if (request.current.signature !== signature) request.current = { signature, id: crypto.randomUUID() };
    try {
      const task = await directorApi.createShootingTask(project.id, {
        recipient_type: kind, recipient_id: recipient.id, note: note.trim(), request_id: request.current.id,
      });
      finish(task);
    } catch (err: any) {
      if (isNotificationOutcomeUnknown(err)) {
        // The server may have committed/sent even when the browser did not receive its response.
        try {
          const tasks = await directorApi.shootingTasks(project.id);
          const task = tasks.find((item) => item.notification_uuid === request.current.id);
          if (task) { finish(task); return; }
        } catch { /* Keep an explicit uncertain state instead of blindly sending again. */ }
        setUncertain(true);
        setError('暂时无法确认推送结果，请查看拍摄任务并核对飞书消息，避免重复发送。');
      } else {
        setError(typeof err.response.data?.detail === 'string' ? err.response.data.detail : '推送失败，请刷新名单后重试');
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return <dialog ref={dialog} aria-labelledby="feishu-dialog-title" className="m-auto w-[calc(100%-2rem)] max-w-lg max-h-[90dvh] overflow-hidden open:flex open:flex-col rounded-xl border border-[var(--border)] bg-white p-0 text-[var(--text)] shadow-xl backdrop:bg-black/40"
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
      <h2 id="feishu-dialog-title" className="font-medium">推送脚本到飞书</h2>
      <button onClick={onClose} disabled={busy} aria-label="关闭推送弹窗" className="p-1 disabled:opacity-40"><X size={18} /></button>
    </div>
    <div className="min-h-0 overflow-y-auto space-y-4 p-5">
      <p className="truncate text-sm" title={project.name}>脚本：{project.name}</p>
      {!canSend && <p role="alert" className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">{directory?.send_blocked_reason || '暂不具备发送条件，请联系管理员检查配置'}</p>}
      <div className="flex gap-2" role="group" aria-label="接收对象类型">
        {(['user', 'chat'] as const).map((type) => <button key={type} disabled={busy || uncertain} aria-pressed={kind === type}
          className={`rounded-md border px-4 py-2 text-sm ${kind === type ? 'bg-black text-white border-black' : 'border-[var(--border)]'}`}
          onClick={() => { setKind(type); setQuery(''); setSelected(''); }}>{type === 'user' ? '员工' : '群聊'}</button>)}
      </div>
      <div className="flex gap-2">
        <label className="flex flex-1 items-center gap-2 rounded-md border border-[var(--border)] px-3">
          <Search size={15} /><input aria-label="搜索飞书接收对象" className="h-10 min-w-0 w-full bg-transparent text-sm outline-none" placeholder={kind === 'user' ? '搜索员工姓名' : '搜索群名'} value={query} onChange={(event) => setQuery(event.target.value)} disabled={busy || uncertain} />
        </label>
        <button className="btn-secondary" aria-label="刷新飞书名单" disabled={loading || busy || uncertain} onClick={() => setRefresh((value) => value + 1)}><RefreshCw size={15} /></button>
      </div>
      <div className="max-h-48 min-h-24 overflow-y-auto rounded-md border border-[var(--border)]" aria-busy={loading}>
        {loading ? <p className="flex items-center justify-center gap-2 py-8 text-sm text-[var(--muted)]"><Loader2 size={16} className="animate-spin" />正在加载飞书名单</p> : filtered.map((item) => <label key={item.id} className="flex cursor-pointer items-center gap-3 px-3 py-2.5 hover:bg-[var(--bg-elevated)]">
          <input type="radio" name="feishu-recipient" value={item.id} checked={selected === item.id} disabled={busy || uncertain} onChange={() => setSelected(item.id)} />
          <span className="min-w-0 flex-1 text-sm"><span className="block truncate">{item.name}</span><span className="text-xs text-[var(--muted)]">{item.department_name || (kind === 'chat' ? '内部群' : '授权员工')} · {item.id.slice(-6)}</span></span>
        </label>)}
        {!loading && !filtered.length && <p className="px-4 py-7 text-center text-sm text-[var(--muted)]">{error ? '名单暂不可用，可点击刷新重试' : query ? '没有匹配的接收对象' : kind === 'chat' ? '暂无内部群，请管理员先将机器人加入群聊' : '暂无可选员工，请检查通讯录授权范围'}</p>}
      </div>
      <label className="block text-sm">备注（选填）<textarea className="mt-2 w-full rounded-md border border-[var(--border)] p-3 text-sm" rows={2} maxLength={2000} value={note} disabled={busy || uncertain} onChange={(event) => setNote(event.target.value)} placeholder="例如：请周五前完成拍摄" /></label>
      <div className="rounded-md bg-[var(--bg-elevated)] p-3 text-sm leading-6">
        <p className="mb-1 text-xs text-[var(--muted)]">通知预览</p>
        <p>【拍摄任务】{project.name}</p><p>脚本结构：{project.sections.length} 段</p><p>发起人：当前登录用户</p>
        {note.trim() && <p className="whitespace-pre-wrap break-words">备注：{note.trim()}</p>}
        <p>查看脚本：附系统脚本链接</p>
      </div>
      <p className="text-xs text-[var(--muted)]">接收者需有本系统账号及脚本访问权限。{kind === 'chat' && '群通知不会自动指定拍摄负责人。'}</p>
      {error && <p role="alert" className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>}
    </div>
    <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-[var(--border)] px-5 py-4">
      {(error || uncertain) && <Link className="mr-auto text-sm underline" to="/shooting-tasks">查看拍摄任务</Link>}
      <button className="btn-secondary" disabled={busy} onClick={onClose}>取消</button>
      <button className="btn-primary" disabled={!recipient || loading || !canSend || busy || uncertain} onClick={() => void send()}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}{busy ? '正在发送' : '发送并创建任务'}</button>
    </div>
  </dialog>;
}
