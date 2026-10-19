import { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { modelOrdersApi } from '../api/modelOrders';
import type { ModelOrder, ModelOrderPage } from '../api/modelOrders';

function errorMessage(error: unknown) {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    return typeof detail === 'string' ? detail : detail?.message || '模型消费记录加载失败';
  }
  return '模型消费记录加载失败';
}

function Usage({ order }: { order: ModelOrder }) {
  const usage = order.usage;
  if (usage.source === 'UNKNOWN') return <span>用量未知</span>;
  if (usage.source === 'FOLDED_TOKENS') {
    const names = { image_count: '有效图片数', audio_milliseconds: '音频毫秒数', character_count: '原始字符数' };
    return <><div>{order.execution_mode === 'MOCK' ? '模拟折算 Token' : '折算 Token'} {usage.converted_tokens}</div><small>原始依据：{usage.raw_metric ? names[usage.raw_metric] : ''} {usage.raw_quantity}</small></>;
  }
  return <><div>{order.execution_mode === 'MOCK' ? '模拟 Token（真实 Token 格式）' : '真实 Token'}</div><small>{[usage.input_tokens !== null && `输入 ${usage.input_tokens}`, usage.output_tokens !== null && `输出 ${usage.output_tokens}`].filter(Boolean).join(' / ')}</small></>;
}

function cost(order: ModelOrder) {
  if (order.execution_mode === 'MOCK') return '模拟，无厂商费用';
  return order.supplier_cost.status === 'KNOWN' ? `${order.supplier_cost.amount} ${order.supplier_cost.currency}` : '供应商成本未知';
}

function status(order: ModelOrder) {
  if (order.reconciliation_status === 'NEEDS_REVIEW') return '待核对（预占保留）';
  return { SETTLED: '已结算', CANCELED: '已取消', RESERVED: '已预占，尚未结算' }[order.status];
}

export default function ModelOrders({ enterprise, refreshKey }: { enterprise: boolean; refreshKey: number }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ModelOrderPage>({ items: [], total: 0, page: 1, page_size: 20 });
  const [detail, setDetail] = useState<ModelOrder | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    setLoading(true); setError(''); setDetail(null); setData({ items: [], total: 0, page, page_size: 20 });
    void modelOrdersApi.list(enterprise, page).then(value => { if (generation.current === current) setData(value); })
      .catch(e => { if (generation.current === current) setError(errorMessage(e)); })
      .finally(() => { if (generation.current === current) setLoading(false); });
    return () => { generation.current++; };
  }, [enterprise, page, refreshKey]);

  async function showDetail(id: string) {
    const current = generation.current;
    setError(''); setDetail(null);
    try {
      const value = await modelOrdersApi.detail(enterprise, id);
      if (generation.current === current) setDetail(value);
    } catch (e) { if (generation.current === current) setError(errorMessage(e)); }
  }
  return <section className="panel p-5 space-y-4" aria-label="模型消费记录">
    <h2 className="font-semibold">{enterprise ? '企业模型消费记录' : '我的模型消费记录'}</h2>
    <p className="text-sm text-[var(--muted)]">供应商成本与企业积分分别记录；模拟用量用于验证计费流程。待核对订单的零结算金额不代表免费消费。</p>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    {loading && <p role="status">正在加载模型消费记录…</p>}
    <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="text-left"><th>时间 / 模型</th><th>执行模式 / 用量来源</th><th>扣费积分</th><th>预占积分</th><th>结算状态</th><th>供应商成本</th><th>操作</th></tr></thead>
      <tbody>{data.items.map(order => <tr className="border-t border-[var(--border)]" key={order.id}>
        <td className="py-3"><div>{order.model_id}</div><small>{new Date(order.created_at).toLocaleString()}</small>{enterprise && <div>成员：{order.user_id}</div>}</td>
        <td><div>{order.execution_mode === 'MOCK' ? '模拟（MOCK）' : '真实（REAL）'}</div><Usage order={order} /></td>
        <td>{order.charged_credits}</td><td>{order.reserved_credits}</td><td>{status(order)}</td><td>{cost(order)}</td>
        <td><button className="text-[var(--accent)]" onClick={() => void showDetail(order.id)}>查看详情</button></td>
      </tr>)}</tbody></table></div>
    {!loading && !error && data.total === 0 && <p>暂无模型消费记录</p>}
    <div className="flex gap-3"><button disabled={loading || page <= 1} onClick={() => setPage(page - 1)} aria-label="模型记录上一页">上一页</button><span>第 {page} 页，共 {data.total} 条模型记录</span><button disabled={loading || page * 20 >= data.total} onClick={() => setPage(page + 1)} aria-label="模型记录下一页">下一页</button></div>
    {detail && <div className="border-t border-[var(--border)] pt-4 space-y-2"><div className="flex justify-between"><h3 className="font-semibold">订单计算依据</h3><button onClick={() => setDetail(null)}>关闭详情</button></div>
      <p className="break-all">订单：{detail.id}</p><p>模型：{detail.provider} / {detail.model_id}</p><p>状态：{status(detail)}</p>
      <p>原始请求标识：{detail.provider_request_id || '未知'}</p>
      {detail.pricing_snapshot.metrics.map(rate => <p key={rate.metric}>{rate.metric}：每 {rate.unit_size} Token / {rate.credits_per_unit} 积分</p>)}
      {detail.pricing_snapshot.conversion && <p>折算依据：每 {detail.pricing_snapshot.conversion.source_unit_size} {detail.pricing_snapshot.conversion.source_metric} 折算 {detail.pricing_snapshot.conversion.tokens_per_unit} Token</p>}
    </div>}
  </section>;
}
