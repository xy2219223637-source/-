import { useEffect, useRef, useState, type FormEvent } from 'react';
import axios from 'axios';
import { api, authApi } from '../api/client';

type Mode = 'REAL_TOKENS' | 'FOLDED_TOKENS';
type Metric = 'input_tokens' | 'output_tokens' | 'converted_tokens';
type Rate = { metric: Metric; unit_size: number; credits_per_unit: string };
type Configuration = { usage_mode: Mode; metrics: Rate[]; conversion: { source_metric: string; source_unit_size: number; tokens_per_unit: number } | null };
type Model = { model_key: string; provider: string; model_id: string; model_types: string[]; connection_status: string; configured: boolean;
  supported_usage_modes: Mode[]; supported_raw_metrics: string[]; pricing: { configuration: Configuration; updated_at: string } | null };
const endpoint = '/platform-admin/billing/price-rules';
const statuses: Record<string, string> = { NOT_IMPLEMENTED: '未实现', NOT_CONFIGURED: '未配置', NOT_TESTED: '尚未验证', PASSED: '接入验证通过', FAILED: '接入验证失败' };
const metricNames: Record<Metric, string> = { input_tokens: '输入', output_tokens: '输出', converted_tokens: '折算' };
const rawNames: Record<string, string> = { image_count: '有效图片数量', audio_milliseconds: '音频毫秒', character_count: '原始文本字符数' };
function errorText(error: unknown) {
  if (axios.isAxiosError(error)) return error.response?.data?.detail?.message || '保存结果不确定，请重试同一保存或刷新核对。';
  return '请求失败，请刷新核对。';
}

function PriceEditor({ item, saving, save }: { item: Model; saving: boolean; save: (body: Configuration, key: string) => Promise<boolean> }) {
  const current = item.pricing?.configuration;
  const [mode, setMode] = useState<Mode>(current?.usage_mode || item.supported_usage_modes[0] || 'REAL_TOKENS');
  const [enabled, setEnabled] = useState<Metric[]>(current?.metrics.map(rate => rate.metric) || []);
  const [rates, setRates] = useState<Record<Metric, { unit: string; credits: string }>>(() => Object.fromEntries(
    (Object.keys(metricNames) as Metric[]).map(metric => { const rate = current?.metrics.find(value => value.metric === metric); return [metric, { unit: rate ? String(rate.unit_size) : '', credits: rate?.credits_per_unit || '' }]; }),
  ) as Record<Metric, { unit: string; credits: string }>);
  const [source, setSource] = useState(current?.conversion?.source_metric || item.supported_raw_metrics[0] || '');
  const [sourceUnit, setSourceUnit] = useState(current?.conversion ? String(current.conversion.source_unit_size) : '');
  const [tokens, setTokens] = useState(current?.conversion ? String(current.conversion.tokens_per_unit) : '');
  const intent = useRef<{ body: string; key: string } | null>(null);
  const editable = item.configured && item.connection_status !== 'NOT_IMPLEMENTED' && item.supported_usage_modes.length > 0;
  async function submit(event: FormEvent) {
    event.preventDefault();
    const metrics = (mode === 'FOLDED_TOKENS' ? ['converted_tokens'] as Metric[] : enabled.filter(value => value !== 'converted_tokens')).map(metric => ({ metric, unit_size: Number(rates[metric].unit), credits_per_unit: rates[metric].credits }));
    const body: Configuration = { usage_mode: mode, metrics, conversion: mode === 'REAL_TOKENS' ? null : { source_metric: source, source_unit_size: Number(sourceUnit), tokens_per_unit: Number(tokens) } };
    const serialized = JSON.stringify(body);
    if (!intent.current || intent.current.body !== serialized) intent.current = { body: serialized, key: crypto.randomUUID() };
    if (await save(body, intent.current.key)) intent.current = null;
  }
  const metricFields = (metric: Metric) => <div key={metric} className="grid gap-3 sm:grid-cols-2">
    <label className="field"><span>{metricNames[metric]} Token 单位数量</span><input required type="number" min={1} max={1000000000} step={1} disabled={saving} value={rates[metric].unit} onChange={event => setRates({ ...rates, [metric]: { ...rates[metric], unit: event.target.value } })} /></label>
    <label className="field"><span>每单位{metricNames[metric]} Token 积分</span><input required inputMode="decimal" pattern="(0|[1-9][0-9]*)(\.[0-9]{1,4})?" maxLength={24} disabled={saving} value={rates[metric].credits} onChange={event => setRates({ ...rates, [metric]: { ...rates[metric], credits: event.target.value } })} /></label>
  </div>;
  return <section className="panel p-5 space-y-4" aria-label={`${item.provider} ${item.model_id}`}>
    <div><h2 className="text-lg font-semibold break-all">{item.model_id}</h2><p className="text-sm text-[var(--muted)]">{item.provider} · {item.model_types.join(' / ')} · {statuses[item.connection_status] || item.connection_status}</p></div>
    <p className="text-sm">当前费率：{current ? current.metrics.map(rate => `${metricNames[rate.metric]} ${rate.credits_per_unit} 积分 / ${rate.unit_size} Token`).join('；') : '未设置，暂不能消费'}</p>
    {editable ? <form onSubmit={submit} className="space-y-4">
      <label className="field"><span>计费 Token 来源</span><select disabled={saving} value={mode} onChange={event => setMode(event.target.value as Mode)}>{item.supported_usage_modes.map(value => <option key={value} value={value}>{value === 'REAL_TOKENS' ? '真实 Token' : '折算 Token'}</option>)}</select></label>
      {mode === 'REAL_TOKENS' ? <><fieldset disabled={saving}><legend>适用方向（与服务端已登记协议一致）</legend>{(['input_tokens', 'output_tokens'] as Metric[]).map(metric => <label key={metric} className="mr-4"><input type="checkbox" checked={enabled.includes(metric)} onChange={event => setEnabled(event.target.checked ? [...enabled, metric].sort() : enabled.filter(value => value !== metric))} />{metricNames[metric]} Token</label>)}</fieldset>{enabled.filter(value => value !== 'converted_tokens').map(metricFields)}</> : <>
        {metricFields('converted_tokens')}
        <label className="field"><span>折算原始用量</span><select required disabled={saving} value={source} onChange={event => setSource(event.target.value)}>{item.supported_raw_metrics.map(value => <option key={value} value={value}>{rawNames[value] || value}</option>)}</select></label>
        <label className="field"><span>原始用量单位数量</span><select required disabled={saving} value={sourceUnit} onChange={event => setSourceUnit(event.target.value)}><option value="">请选择已核实比例</option>{[1,10,100,1000,10000,100000,1000000].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
        <label className="field"><span>每单位原始用量折算 Token</span><input required type="number" min={1} max={1000000000} step={1} disabled={saving} value={tokens} onChange={event => setTokens(event.target.value)} /></label>
      </>}
      <p className="text-xs text-[var(--muted)]">保存立即影响新请求；已有请求沿用发起时费率。最终总额向上取整到 0.0001 积分。费率与比例须自行核实。</p>
      <button className="btn-primary" disabled={saving || (mode === 'REAL_TOKENS' && !enabled.some(value => value !== 'converted_tokens'))}>保存当前费率</button>
    </form> : <p className="text-sm text-[var(--muted)]">{item.connection_status === 'NOT_IMPLEMENTED' ? '当前能力未实现。' : !item.configured ? '模型连接配置不完整。' : '用量协议尚未登记，暂不能配置可消费费率。'}</p>}
  </section>;
}

export default function PlatformModelPricing() {
  const [access, setAccess] = useState<'loading' | 'allowed' | 'denied'>('loading');
  const [rows, setRows] = useState<Model[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [modelType, setModelType] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  const generation = useRef(0);
  async function load() {
    const request = ++generation.current;
    const response = await api.get<{ items: Model[]; total: number }>(endpoint, { params: { page, page_size: 20, ...(modelType ? { model_type: modelType } : {}) } });
    if (generation.current === request) { setRows(response.data.items); setTotal(response.data.total); }
  }
  useEffect(() => { let alive = true; void authApi.me().then(me => { if (alive) setAccess(me.is_platform_admin ? 'allowed' : 'denied'); }).catch(() => { if (alive) setAccess('denied'); }); return () => { alive = false; }; }, []);
  useEffect(() => { if (access === 'allowed') void load().catch(err => setError(errorText(err))); return () => { generation.current++; }; }, [access, page, modelType]);
  async function save(item: Model, body: Configuration, key: string) {
    if (inFlight.current) return false;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    try {
      await api.put(`${endpoint}/${item.model_key}`, body, { headers: { 'Idempotency-Key': key } });
      setNotice('当前费率已保存。请核对刷新后的配置；并发保存以最后提交为准。');
      try { await load(); } catch { setError('保存已成功，但刷新失败，请手动刷新核对。'); }
      return true;
    } catch (err) { setError(errorText(err)); return false; }
    finally { inFlight.current = false; setBusy(false); }
  }
  if (access === 'loading') return <p>正在验证平台管理权限…</p>;
  if (access === 'denied') return <p role="alert">仅平台管理员可以管理模型费用。</p>;
  return <div className="space-y-6"><header className="flex flex-wrap justify-between gap-4"><div><h1 className="text-2xl font-semibold">平台模型费用管理</h1><p className="mt-2 text-sm text-[var(--muted)]">每个供应商与模型共用一套当前积分费率。供应商成本单独记录；接入状态与计费验收分别核对。</p></div><button className="btn-secondary" disabled={busy} onClick={() => { setError(''); void load().catch(err => setError(errorText(err))); }}>刷新费率</button></header>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <label className="field"><span>模型类型</span><select disabled={busy} value={modelType} onChange={event => { setModelType(event.target.value); setPage(1); }}><option value="">全部</option>{['TEXT','VISION','IMAGE','EMBEDDING','TTS','ASR','VIDEO'].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    <div className="grid items-start gap-5 xl:grid-cols-2">{rows.map(item => <PriceEditor key={`${item.model_key}:${item.pricing?.updated_at || 'unset'}`} item={item} saving={busy} save={(body, key) => save(item, body, key)} />)}</div>
    {!rows.length && <p>暂无符合条件的模型。</p>}
    <div className="flex items-center justify-end gap-3"><span>共 {total} 个模型</span><button className="btn-secondary" disabled={busy || page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页</span><button className="btn-secondary" disabled={busy || page * 20 >= total} onClick={() => setPage(page + 1)}>下一页</button></div>
  </div>;
}
