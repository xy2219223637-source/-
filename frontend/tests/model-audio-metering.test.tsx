import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ModelOrders from '../src/components/ModelOrders';
import PlatformModelPricing from '../src/pages/PlatformModelPricing';
import { api, authApi } from '../src/api/client';

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), put: vi.fn() }, authApi: { me: vi.fn() } }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

function fixture(kind: 'TTS' | 'ASR') {
  const metric = kind === 'TTS' ? 'character_count' : 'audio_milliseconds';
  const configuration = { usage_mode: 'FOLDED_TOKENS', metrics: [
    { metric: 'converted_tokens', unit_size: 1, credits_per_unit: '1.0000' }],
    conversion: { source_metric: metric, source_unit_size: 1000, tokens_per_unit: 1 } };
  const model = { model_key: kind === 'TTS' ? 'a'.repeat(32) : 'b'.repeat(32), provider: 'offline:' + kind.toLowerCase(),
    model_id: 'local-' + kind.toLowerCase(), model_types: [kind], configured: true,
    connection_status: 'NOT_TESTED', supported_usage_modes: ['FOLDED_TOKENS'], supported_raw_metrics: [metric],
    pricing: { configuration, updated_at: '2026-10-19T00:00:00Z' } };
  const order = { id: 'c'.repeat(32), request_id: 'synthetic-audio', tenant_id: 'tenant', user_id: 'member',
    model_key: model.model_key, provider: model.provider, model_id: model.model_id, execution_mode: 'MOCK',
    status: 'SETTLED', reconciliation_status: 'RESOLVED', usage: { source: 'FOLDED_TOKENS', input_tokens: null,
      output_tokens: null, converted_tokens: kind === 'TTS' ? '0.006' : '1', raw_metric: metric,
      raw_quantity: kind === 'TTS' ? 6 : 1000 }, supplier_cost: { status: 'NOT_INCURRED', amount: null, currency: null },
    charged_credits: kind === 'TTS' ? '0.0060' : '1.0000', reserved_credits: '0.0000', pricing_snapshot: configuration,
    provider_request_id: 'mock-audio', created_at: '2026-10-19T00:00:00Z' };
  return { model, order, configuration };
}

it.each(['TTS', 'ASR'] as const)('%s 仅保存本能力已登记的原始用量与折算规则并回显', async kind => {
  const { model, configuration } = fixture(kind);
  vi.mocked(authApi.me).mockResolvedValue({ is_platform_admin: true } as never);
  vi.mocked(api.get).mockResolvedValue({ data: { items: [model], total: 1, page: 1, page_size: 20 } });
  vi.mocked(api.put).mockResolvedValue({ data: model.pricing });
  render(<PlatformModelPricing />);
  await screen.findByLabelText('每单位折算 Token 积分');
  const correct = kind === 'TTS' ? '原始文本字符数' : '音频毫秒';
  const incorrect = kind === 'TTS' ? '音频毫秒' : '原始文本字符数';
  expect(screen.getByRole('option', { name: correct })).toBeTruthy();
  expect(screen.queryByRole('option', { name: incorrect })).toBeNull();
  expect(screen.queryByRole('option', { name: '真实 Token' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '保存当前费率' }));
  await screen.findByRole('status');
  expect(api.put).toHaveBeenCalledWith('/platform-admin/billing/price-rules/' + model.model_key,
    configuration, { headers: { 'Idempotency-Key': expect.any(String) } });
  expect((screen.getByLabelText('原始用量单位数量') as HTMLSelectElement).value).toBe('1000');
});

it.each([['TTS', false], ['TTS', true], ['ASR', false], ['ASR', true]] as const)(
  '%s 消费列表与详情区分原始用量、折算和 MOCK enterprise=%s', async (kind, enterprise) => {
    const { order } = fixture(kind);
    vi.mocked(api.get).mockImplementation(async path => ({ data: String(path).endsWith(order.id) ? order :
      { items: [order], total: 1, page: 1, page_size: 20 } }) as never);
    render(<ModelOrders enterprise={enterprise} refreshKey={0} />);
    expect(await screen.findByText('模拟折算 Token ' + order.usage.converted_tokens)).toBeTruthy();
    expect(screen.getByText(kind === 'TTS' ? '原始依据：原始字符数 6' : '原始依据：音频毫秒数 1000')).toBeTruthy();
    expect(screen.getByText('模拟（MOCK）')).toBeTruthy();
    expect(screen.getByText('模拟，无厂商费用')).toBeTruthy();
    expect(screen.getByText(order.charged_credits)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
    const prefix = enterprise ? '/tenant-admin/billing/model-orders' : '/me/billing/model-orders';
    await waitFor(() => expect(api.get).toHaveBeenCalledWith(prefix + '/' + order.id));
    expect(await screen.findByText(`折算依据：每 1000 ${order.usage.raw_metric} 折算 1 Token`)).toBeTruthy();
  });

it.each(['TTS', 'ASR'] as const)('%s 缺用量显示待核对，不把零结算解释为免费', async kind => {
  const { order } = fixture(kind);
  const pending = { ...order, status: 'RESERVED', reconciliation_status: 'NEEDS_REVIEW', charged_credits: '0.0000',
    reserved_credits: order.charged_credits, usage: { source: 'UNKNOWN', input_tokens: null, output_tokens: null,
      converted_tokens: null, raw_metric: null, raw_quantity: null } };
  vi.mocked(api.get).mockResolvedValue({ data: { items: [pending], total: 1, page: 1, page_size: 20 } });
  render(<ModelOrders enterprise={false} refreshKey={0} />);
  expect(await screen.findByText('待核对（预占保留）')).toBeTruthy();
  expect(screen.getByText('用量未知')).toBeTruthy();
  expect(screen.queryByText(/原始依据：/)).toBeNull();
  expect(screen.getByText(order.charged_credits)).toBeTruthy();
});
