import { useEffect, useState } from 'react';
import axios from 'axios';
import { Link } from 'react-router-dom';
import { api, authApi } from '../api/client';
import TenantAdminPage from '../components/TenantAdminPage';

type Quota = { user_id: string; name?: string; authorized: boolean; period_key: string | null; limit_credits: string; used_credits: string; reserved_credits: string; remaining_credits: string };
type Ledger = { id: string; event_type: string; delta_credits: string; balance_after_credits: string; created_at: string; reason: string };
type Order = { id: string; feature_code: string; status: string; charged_credits: string; reserved_credits: string; created_at: string };
type Balance = { balance_credits: string; reserved_credits: string; available_credits: string };
type Page<T> = { items: T[]; total: number };

function errorMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    return typeof detail === 'string' ? detail : detail?.message || '操作失败，请稍后重试';
  }
  return '加载失败';
}

export default function MyBilling({ enterprise = false }: { enterprise?: boolean }) {
  const [quota, setQuota] = useState<Quota | null>(null);
  const [balance, setBalance] = useState<Balance | null>(null);
  const [members, setMembers] = useState<Page<Quota>>({ items: [], total: 0 });
  const [ledger, setLedger] = useState<Page<Ledger>>({ items: [], total: 0 });
  const [orders, setOrders] = useState<Page<Order>>({ items: [], total: 0 });
  const [page, setPage] = useState(1);
  const [memberPage, setMemberPage] = useState(1);
  const [canEdit, setCanEdit] = useState(false);
  const [canView, setCanView] = useState(false);
  const [canMock, setCanMock] = useState(false);
  const [mock, setMock] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Quota | null>(null);
  const [limit, setLimit] = useState('');
  const [reason, setReason] = useState('');
  // Keep the same request key after an ambiguous failure; a retry must not charge twice.
  const [keys, setKeys] = useState({ fixed: crypto.randomUUID(), text: crypto.randomUUID() });

  async function load() {
    if (enterprise) {
      const [b, m, l] = await Promise.all([
        api.get<Balance>('/tenant-admin/billing/credits'),
        api.get<Page<Quota>>('/tenant-admin/billing/members/usage', { params: { page: memberPage } }),
        api.get<Page<Ledger>>('/tenant-admin/billing/ledger', { params: { page } }),
      ]);
      setBalance(b.data); setMembers(m.data); setLedger(l.data);
    } else {
      const [q, o] = await Promise.all([api.get<Quota>('/me/billing/quota'), api.get<Page<Order>>('/me/billing/orders', { params: { page } })]);
      setQuota(q.data); setOrders(o.data);
    }
  }

  useEffect(() => {
    setError('');
    void load().catch(e => setError(errorMessage(e)));
  }, [enterprise, page, memberPage]);

  useEffect(() => {
    void authApi.me().then(me => {
      setCanEdit(me.role === 'owner' || me.permissions.includes('billing.quota.manage'));
      setCanView(me.role === 'owner' || me.permissions.includes('billing.view'));
      setCanMock(me.role === 'owner' || me.permissions.includes('billing.mock.use'));
    }).catch(e => setError(errorMessage(e)));
    void api.get<{ mock_enabled: boolean }>('/me/billing/config').then(r => setMock(r.data.mock_enabled)).catch(() => setMock(false));
  }, []);

  async function saveQuota(event: React.FormEvent) {
    event.preventDefault(); if (!editing) return;
    setBusy(true); setError('');
    try {
      await api.put(`/tenant-admin/billing/members/${editing.user_id}/quota`, { total_limit_credits: limit, reason });
      setEditing(null); setNotice('月度总限额已更新，企业积分余额不变'); await load();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }

  async function runMock(kind: 'fixed' | 'text') {
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await api.post(`/ai/mock/${kind}`, { text: '积分测试' }, { headers: { 'Idempotency-Key': keys[kind] } });
      if (response.status === 202) { setNotice('请求处理中，可使用同一按钮查询重试'); }
      else { setNotice(`完成，消费 ${response.data.charged_credits} 积分`); setKeys(previous => ({ ...previous, [kind]: crypto.randomUUID() })); }
      await load();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }

  const total = enterprise ? ledger.total : orders.total;
  return <TenantAdminPage enterprise={enterprise} title={enterprise ? '企业积分与成员额度' : '我的消费额度'}
    description="企业共用积分池；成员月度额度是消费上限。周期按北京时间计算，跨月订单沿用预占月份。"
    actions={<>{(enterprise || canView) && <Link className="text-sm text-[var(--accent)]" to={enterprise ? '/my-billing' : '/tenant-admin/billing'}>{enterprise ? '查看我的额度' : '企业积分管理'}</Link>}<button className="btn-secondary" disabled={busy} onClick={() => { setError(''); void load().catch(e => setError(errorMessage(e))); }}>刷新</button></>}>
    {error && <div role="alert" className="p-3 bg-red-50 text-red-700 rounded">{error}</div>}
    {notice && <div role="status" className="p-3 bg-emerald-50 text-emerald-800 rounded">{notice}</div>}
    {enterprise && balance && <section className="panel p-5 grid sm:grid-cols-3 gap-4">{[['企业总积分', balance.balance_credits], ['预占积分', balance.reserved_credits], ['企业可用积分', balance.available_credits]].map(([label, value]) => <div key={label}><p className="text-sm text-[var(--muted)]">{label}</p><b className="text-xl">{value}</b></div>)}</section>}
    {!enterprise && quota && <section className="panel p-5 space-y-3"><h2>{quota.period_key || '本月'} 消费额度</h2>{!quota.authorized && <p>尚未获得成员额度，请联系企业管理员。</p>}<div className="grid sm:grid-cols-4 gap-4">{[['月度总限额', quota.limit_credits], ['已消费', quota.used_credits], ['预占', quota.reserved_credits], ['剩余额度', quota.remaining_credits]].map(([label, value]) => <div key={label}><p className="text-sm text-[var(--muted)]">{label}</p><b>{value}</b></div>)}</div></section>}
    {enterprise && <section className="panel p-5 overflow-x-auto"><h2 className="font-semibold mb-3">成员本月额度</h2><table className="w-full text-sm"><thead><tr className="text-left"><th>成员</th><th>月总限额</th><th>已用</th><th>预占</th><th>剩余</th><th>操作</th></tr></thead><tbody>{members.items.map(m => <tr key={m.user_id} className="border-t border-[var(--border)]"><td className="py-3">{m.name || m.user_id}</td><td>{m.authorized ? m.limit_credits : '未授权'}</td><td>{m.used_credits}</td><td>{m.reserved_credits}</td><td>{m.remaining_credits}</td><td>{canEdit && <button className="text-[var(--accent)]" onClick={() => { setEditing(m); setLimit(m.limit_credits); setReason(''); }}>设置总限额</button>}</td></tr>)}</tbody></table><div className="flex gap-3 mt-3"><button disabled={memberPage <= 1} onClick={() => setMemberPage(memberPage - 1)}>上一页</button><span>第 {memberPage} 页，共 {members.total} 人</span><button disabled={memberPage * 20 >= members.total} onClick={() => setMemberPage(memberPage + 1)}>下一页</button></div></section>}
    {editing && <form className="panel p-5 flex flex-wrap items-end gap-4" onSubmit={saveQuota}><label className="field"><span>{editing.name} 本月总限额</span><input required inputMode="decimal" pattern="[0-9]+(\.[0-9]{1,4})?" value={limit} onChange={e => setLimit(e.target.value)} /></label><label className="field"><span>修改原因</span><input required maxLength={200} value={reason} onChange={e => setReason(e.target.value)} /></label><button className="btn-primary" disabled={busy}>保存</button><button type="button" className="btn-secondary" onClick={() => setEditing(null)}>取消</button></form>}
    {!enterprise && mock && canMock && <section className="panel p-5 space-y-3"><h2 className="font-semibold">积分测试功能</h2><p className="text-sm text-[var(--muted)]">使用测试费率消费企业积分。</p><div className="flex gap-3"><button className="btn-secondary" disabled={busy} onClick={() => void runMock('fixed')}>按次测试</button><button className="btn-secondary" disabled={busy} onClick={() => void runMock('text')}>Token 测试</button></div></section>}
    <section className="panel p-5 overflow-x-auto"><h2 className="font-semibold mb-3">{enterprise ? '企业积分流水（默认本月）' : '我的消费记录'}</h2><table className="w-full text-sm"><thead><tr className="text-left"><th>时间</th><th>类型/功能</th><th>变化/扣费积分</th><th>{enterprise ? '结余' : '状态'}</th></tr></thead><tbody>{enterprise ? ledger.items.map(l => <tr key={l.id} className="border-t border-[var(--border)]"><td className="py-3">{new Date(l.created_at).toLocaleString()}</td><td>{l.event_type === 'GRANT' ? '平台发放' : l.event_type === 'REVOKE' ? '平台扣回' : '业务消费'}</td><td>{l.delta_credits}</td><td>{l.balance_after_credits}</td></tr>) : orders.items.map(o => <tr key={o.id} className="border-t border-[var(--border)]"><td className="py-3">{new Date(o.created_at).toLocaleString()}</td><td>{o.feature_code}</td><td>{o.charged_credits}</td><td>{o.status}</td></tr>)}</tbody></table>{total === 0 && <p className="py-5 text-[var(--muted)]">暂无记录</p>}<div className="flex gap-3 mt-3"><button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页，共 {total} 条</span><button disabled={page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button></div></section>
  </TenantAdminPage>;
}
