import { useEffect, useRef, useState, type FormEvent } from 'react';
import axios from 'axios';
import { api } from '../api/client';

type PendingGrant = { key: string; amount_credits: string; reason: string; uncertain: boolean };
type Grant = { event_type: string; id: string; delta_credits: string; balance_after_credits: string; reason: string; created_at: string };

function message(error: unknown) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    return typeof detail === 'string' ? detail : detail?.message || '请求失败，请稍后重试';
  }
  return '请求失败，请稍后重试';
}

export default function PlatformCreditGrants({ tenant, actorId, onClose, mode = 'grant' }: {
  tenant: { id: string; name: string }; actorId: string; onClose: () => void; mode?: 'grant' | 'revoke';
}) {
  const verb = mode === 'revoke' ? '扣回' : '发放';
  const storageKey = `billing:pending-${mode}:${actorId}:${tenant.id}`;
  const [pending, setPending] = useState<PendingGrant | null>(() => {
    try {
      const raw = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
      if (raw && typeof raw.key === 'string' && typeof raw.amount_credits === 'string' && typeof raw.reason === 'string') {
        return { ...raw, uncertain: true };
      }
    } catch { /* An invalid saved form cannot authorize a grant. */ }
    return null;
  });
  const [amount, setAmount] = useState(pending?.amount_credits || '');
  const [reason, setReason] = useState(pending?.reason || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [rows, setRows] = useState<Grant[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const inFlight = useRef(false);
  const base = `/platform-admin/tenants/${tenant.id}/credits`;
  const path = `${base}/${mode === 'revoke' ? 'revocations' : 'grants'}`;
  const [balance, setBalance] = useState<{ balance_credits: string; reserved_credits: string; available_credits: string } | null>(null);

  async function loadHistory() {
    setHistoryError('');
    try {
      const { data } = await api.get<{ items: Grant[]; total: number }>(`${base}/adjustments`, { params: { page, page_size: 20 } });
      setRows(data.items); setTotal(data.total);
      if (mode === 'revoke') { const result = await api.get(base); setBalance(result.data); }
    } catch (err) { setHistoryError(message(err)); }
  }
  useEffect(() => { void loadHistory(); }, [tenant.id, page]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (inFlight.current) return;
    if (!pending && (!/^(0|[1-9][0-9]*)(\.[0-9]{1,4})?$/.test(amount) || !/[1-9]/.test(amount) || !reason.trim())) {
      setError(`请输入大于0、最多四位小数的积分，并填写${verb}原因`); return;
    }
    const request = pending || { key: crypto.randomUUID(), amount_credits: amount, reason: reason.trim(), uncertain: false };
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    try {
      // Save before sending: closing/reopening the panel must not issue a second grant.
      sessionStorage.setItem(storageKey, JSON.stringify(request)); setPending(request);
      const { data } = await api.post(path, { amount_credits: request.amount_credits, reason: request.reason }, {
        headers: { 'Idempotency-Key': request.key },
      });
      sessionStorage.removeItem(storageKey); setPending(null); setAmount(''); setReason('');
      setNotice(`已向「${tenant.name}」${verb} ${data.amount_credits} 积分，${verb}后企业余额 ${data.balance_after_credits}`);
      // History failures must never make a committed grant appear to have failed.
      await loadHistory();
    } catch (err) {
      const status = axios.isAxiosError(err) ? err.response?.status : undefined;
      const insufficient = axios.isAxiosError(err) && err.response?.data?.detail?.code === 'INSUFFICIENT_AVAILABLE_CREDITS';
      if (!request.uncertain && status && status >= 400 && status < 500 && (status !== 409 || insufficient)) {
        sessionStorage.removeItem(storageKey); setPending(null);
      } else {
        const retry = { ...request, uncertain: true };
        sessionStorage.setItem(storageKey, JSON.stringify(retry)); setPending(retry);
      }
      setError(message(err));
    } finally { inFlight.current = false; setBusy(false); }
  }

  return <section className="panel p-5 space-y-4" aria-label={`向${tenant.name}${verb}积分`}>
    <div className="flex items-center justify-between gap-3"><div><h2 className="font-semibold">向「{tenant.name}」{verb}企业积分</h2><p className="text-sm text-[var(--muted)] mt-1">{mode === 'revoke' ? '纠正多发积分；最多扣回当前可用积分，已预占积分受保护，不改变成员月度额度。' : '增加企业共享池余额，不改变成员月度额度。'}</p></div><button className="btn-secondary" type="button" onClick={onClose}>关闭</button></div>
    {notice && <p role="status" className="rounded bg-emerald-50 p-3 text-emerald-800">{notice}</p>}
    {error && <p role="alert" className="rounded bg-red-50 p-3 text-red-700">{error}</p>}
    {pending && !busy && <p className="text-sm text-amber-700">这笔请求尚未取得确定结果。请重试本次操作，将沿用原请求标识，不重复入账；关闭后重新打开也会保留。</p>}
    {mode === 'revoke' && <p className="text-sm text-[var(--muted)]">企业余额 {balance?.balance_credits ?? '—'} · 已预占 {balance?.reserved_credits ?? '—'} · 可扣回 {balance?.available_credits ?? '—'}</p>}
    <form onSubmit={submit} className="grid gap-4 md:grid-cols-[1fr_2fr_auto] items-end">
      <label className="field"><span>{verb}积分</span><input required inputMode="decimal" maxLength={24} value={amount} disabled={busy || !!pending} onChange={e => setAmount(e.target.value)} placeholder="例如 1000.0000" /></label>
      <label className="field"><span>{verb}原因</span><input required maxLength={200} value={reason} disabled={busy || !!pending} onChange={e => setReason(e.target.value)} placeholder={mode === 'revoke' ? '例如 纠正多发测试积分' : '例如 测试额度补充'} /></label>
      <button className="btn-primary" type="submit" disabled={busy}>{busy ? `${verb}中…` : pending ? `重试本次${verb}` : `确认${verb}`}</button>
    </form>
    <div className="border-t border-[var(--border)] pt-4"><div className="flex items-center justify-between"><h3 className="font-medium">本月积分调整记录</h3><button className="btn-secondary" type="button" onClick={() => void loadHistory()}>刷新记录</button></div>
      {historyError && <p className="text-sm text-red-600 mt-2">记录加载失败：{historyError}</p>}
      <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left"><th className="py-3">时间（北京时间）</th><th>类型</th><th>变动积分</th><th>调整后余额</th><th>原因</th></tr></thead><tbody>{rows.map(row => <tr key={row.id} className="border-t border-[var(--border)]"><td className="py-3">{new Date(row.created_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}</td><td>{row.event_type === 'REVOKE' ? '平台扣回' : '平台发放'}</td><td>{row.delta_credits}</td><td>{row.balance_after_credits}</td><td>{row.reason}</td></tr>)}</tbody></table></div>
      {!rows.length && !historyError && <p className="text-sm text-[var(--muted)] py-3">本月暂无积分调整记录</p>}
      <div className="flex gap-3 mt-3 text-sm"><button disabled={page <= 1} onClick={() => setPage(page-1)}>上一页</button><span>第 {page} 页，共 {total} 条</span><button disabled={page*20 >= total} onClick={() => setPage(page+1)}>下一页</button></div>
    </div>
  </section>;
}
