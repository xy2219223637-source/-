import { useEffect, useRef, useState, type FormEvent } from 'react';
import axios from 'axios';
import { api, authApi } from '../api/client';

type Metric = { metric: 'input_tokens' | 'output_tokens'; unit_size: number; credits_per_unit: string };
type Price = { id: string; feature_code: string; billing_mode: string; pricing_version: number; status: string; fixed_credits: string | null; metrics: Metric[] };
type Summary = { feature_code: string; billing_mode: string; active: Price | null; next_version: number };
const endpoint = '/platform-admin/billing/price-rules';
const names: Record<string, string> = { mock_fixed: '按次功能（测试）', mock_text: 'Token 功能（测试）' };
const states: Record<string, string> = { DRAFT: '待启用', ACTIVE: '生效中', RETIRED: '已停用' };
function errorText(error: unknown) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    return typeof detail === 'string' ? detail : detail?.message || '请求失败，请刷新核对版本后重试';
  }
  return '请求失败，请刷新核对版本后重试';
}
function rateText(price: Price) {
  return price.billing_mode === 'FIXED' ? `${price.fixed_credits} 积分 / 次` : price.metrics.map(rate =>
    `${rate.metric === 'input_tokens' ? '输入' : '输出'}：${rate.credits_per_unit} 积分 / ${rate.unit_size} Token`).join('；');
}
function PriceEditor({ item, saving, save }: { item: Summary; saving: boolean; save: (payload: object) => Promise<void> }) {
  const fixed = item.billing_mode === 'FIXED';
  const input = item.active?.metrics.find(rate => rate.metric === 'input_tokens');
  const output = item.active?.metrics.find(rate => rate.metric === 'output_tokens');
  const [fixedCredits, setFixedCredits] = useState(item.active?.fixed_credits || '');
  const [inputUnit, setInputUnit] = useState(String(input?.unit_size || 1000));
  const [outputUnit, setOutputUnit] = useState(String(output?.unit_size || 1000));
  const [inputCredits, setInputCredits] = useState(input?.credits_per_unit || '');
  const [outputCredits, setOutputCredits] = useState(output?.credits_per_unit || '');
  async function submit(event: FormEvent) {
    event.preventDefault();
    await save({ feature_code: item.feature_code, billing_mode: item.billing_mode, version: item.next_version,
      ...(fixed ? { fixed_credits: fixedCredits } : { metrics: [
        { metric: 'input_tokens', unit_size: Number(inputUnit), credits_per_unit: inputCredits },
        { metric: 'output_tokens', unit_size: Number(outputUnit), credits_per_unit: outputCredits },
      ] }) });
  }
  const amountProps = { required: true, inputMode: 'decimal' as const, pattern: '(0|[1-9][0-9]*)(\\.[0-9]{1,4})?', maxLength: 24, disabled: saving };
  return <section className="panel p-5 space-y-4">
    <div><h2 className="text-lg font-semibold">{fixed ? '按次固定积分' : 'Token 与积分换算'}</h2>
      <p className="mt-1 text-sm text-[var(--muted)]">{names[item.feature_code]} · {item.active ? `当前版本 v${item.active.pricing_version}` : '尚无生效规则'}</p></div>
    <p className="rounded bg-[var(--bg-elevated)] p-3 text-sm">当前费率：{item.active ? rateText(item.active) : '未设置，暂不能消费'}</p>
    <form onSubmit={submit} className="space-y-4">
      {fixed ? <label className="field"><span>每次扣除积分</span><input {...amountProps} value={fixedCredits} onChange={event => setFixedCredits(event.target.value)} /></label> : <>
        <div className="grid gap-3 sm:grid-cols-2"><label className="field"><span>输入 Token 单位数量</span><input required type="number" min={1} max={1000000000} step={1} disabled={saving} value={inputUnit} onChange={event => setInputUnit(event.target.value)} /></label><label className="field"><span>每单位输入 Token 积分</span><input {...amountProps} value={inputCredits} onChange={event => setInputCredits(event.target.value)} /></label></div>
        <div className="grid gap-3 sm:grid-cols-2"><label className="field"><span>输出 Token 单位数量</span><input required type="number" min={1} max={1000000000} step={1} disabled={saving} value={outputUnit} onChange={event => setOutputUnit(event.target.value)} /></label><label className="field"><span>每单位输出 Token 积分</span><input {...amountProps} value={outputCredits} onChange={event => setOutputCredits(event.target.value)} /></label></div>
        <p className="text-xs text-[var(--muted)]">输入和输出分别换算后相加；不足一个单位按实际 Token 比例计费，总额向上取整到 0.0001 积分。</p>
      </>}
      <p className="text-xs text-[var(--muted)]">保存为新版本 v{item.next_version}，启用后仅对新订单生效。积分必须大于 0，最多四位小数。</p>
      <button className="btn-primary" disabled={saving}>保存{fixed ? '按次' : 'Token'}新版本</button>
    </form>
  </section>;
}

export default function PlatformModelPricing() {
  const [access, setAccess] = useState<'loading' | 'allowed' | 'denied'>('loading');
  const [summary, setSummary] = useState<Summary[]>([]);
  const [rows, setRows] = useState<Price[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  async function load() {
    const [current, history] = await Promise.all([
      api.get<{ items: Summary[] }>(`${endpoint}/summary`),
      api.get<{ items: Price[]; total: number }>(endpoint, { params: { page, page_size: 20 } }),
    ]);
    setSummary(current.data.items); setRows(history.data.items); setTotal(history.data.total);
  }
  useEffect(() => {
    let alive = true;
    void authApi.me().then(me => { if (alive) setAccess(me.is_platform_admin ? 'allowed' : 'denied'); }).catch(() => { if (alive) setAccess('denied'); });
    return () => { alive = false; };
  }, []);
  useEffect(() => { if (access === 'allowed') void load().catch(err => setError(errorText(err))); }, [access, page]);
  async function mutate(action: () => Promise<unknown>, success: string) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    try {
      await action(); setNotice(success);
      try { await load(); } catch { setError('操作已成功，但列表刷新失败，请刷新核对。'); }
    } catch (err) { setError(errorText(err)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  if (access === 'loading') return <p className="text-[var(--muted)]">正在验证平台管理权限…</p>;
  if (access === 'denied') return <p role="alert">仅平台管理员可以管理模型费用。</p>;
  return <div className="space-y-6">
    <header className="flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-2xl font-semibold">平台模型费用管理</h1><p className="mt-2 text-sm text-[var(--muted)]">统一设置企业积分消费费率。目前仅开放测试功能，尚未接入真实模型；这里配置的是向企业扣除的积分。</p></div><button className="btn-secondary" disabled={busy} onClick={() => { setError(''); void load().catch(err => setError(errorText(err))); }}>刷新费率</button></header>
    {error && <p role="alert" className="rounded bg-red-50 p-3 text-red-700">{error}</p>}
    {notice && <p role="status" className="rounded bg-emerald-50 p-3 text-emerald-800">{notice}</p>}
    <div className="grid items-start gap-5 xl:grid-cols-2">{summary.map(item => <PriceEditor key={`${item.feature_code}:${item.active?.id || 'unset'}`} item={item} saving={busy} save={payload => mutate(() => api.post(endpoint, payload), '新版本已保存，当前费率未改变。请在版本记录中核对并启用。')} />)}</div>
    <section className="panel overflow-hidden"><div className="border-b border-[var(--border)] p-5"><h2 className="font-semibold">费率版本记录</h2><p className="mt-1 text-sm text-[var(--muted)]">启用新版本会自动停用同功能旧版本；已有订单按创建时的费率结算。</p></div>
      <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left text-[var(--muted)]"><th className="p-4">功能</th><th>版本</th><th>扣费设置</th><th>状态</th><th className="p-4">操作</th></tr></thead><tbody>{rows.map(row => <tr key={row.id} className="border-t border-[var(--border)]"><td className="p-4">{names[row.feature_code] || row.feature_code}</td><td>v{row.pricing_version}</td><td className="min-w-64 py-4">{rateText(row)}</td><td>{states[row.status] || row.status}</td><td className="p-4">{row.status === 'DRAFT' && <button className="btn-secondary whitespace-nowrap" disabled={busy} aria-label={`启用${names[row.feature_code] || row.feature_code}版本${row.pricing_version}`} onClick={() => void mutate(() => api.post(`${endpoint}/${row.id}/activate`), `版本 v${row.pricing_version} 已启用，将用于该功能的新订单。`)}>启用此版本</button>}</td></tr>)}</tbody></table></div>
      {!rows.length && <p className="p-5 text-sm text-[var(--muted)]">暂无费率版本，请先配置并保存。</p>}
      <div className="flex items-center justify-end gap-3 border-t border-[var(--border)] p-4 text-sm"><span>共 {total} 条</span><button className="btn-secondary" disabled={busy || page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页</span><button className="btn-secondary" disabled={busy || page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button></div>
    </section>
  </div>;
}
