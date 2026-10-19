import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import MyBilling from '../src/pages/MyBilling';
import PlatformModelPricing from '../src/pages/PlatformModelPricing';
import { api, authApi } from '../src/api/client';

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), put: vi.fn() }, authApi: { me: vi.fn() } }));
const quota = { user_id: 'member', name: '嵌入测试成员', authorized: true, period_key: '2026-10',
  limit_credits: '100.0000', used_credits: '2.0000', reserved_credits: '0.0000', remaining_credits: '98.0000' };
const configuration = { usage_mode: 'REAL_TOKENS', metrics: [{ metric: 'input_tokens', unit_size: 1000, credits_per_unit: '2.0000' }], conversion: null };
const order = { id: 'a'.repeat(32), request_id: 'request', tenant_id: 'a', user_id: 'member',
  model_key: 'b'.repeat(32), provider: 'openai_compatible:llm', model_id: 'fixture-embedding', execution_mode: 'MOCK',
  status: 'SETTLED', reconciliation_status: 'RESOLVED', usage: { source: 'REAL_TOKENS', input_tokens: 1000,
    output_tokens: null, converted_tokens: null, raw_metric: null, raw_quantity: null },
  supplier_cost: { status: 'NOT_INCURRED', amount: null, currency: null }, charged_credits: '2.0000', reserved_credits: '0.0000',
  pricing_snapshot: configuration, provider_request_id: null, created_at: '2026-10-19T00:00:00Z' };

beforeEach(() => {
  vi.mocked(api.get).mockReset(); vi.mocked(api.put).mockReset();
  vi.mocked(authApi.me).mockResolvedValue({ user_id: 'member', phone: '', role: 'owner', tenant_id: 'a', tenant_name: 'A', is_platform_admin: false, permissions: ['*'] });
  vi.mocked(api.get).mockImplementation(async path => ({ data: String(path).includes('model-orders') ?
    String(path).endsWith(order.id) ? order : { items: [order], total: 1, page: 1, page_size: 20 } :
    String(path).endsWith('/quota') ? quota : String(path).endsWith('/credits') ?
      { balance_credits: '998.0000', reserved_credits: '0.0000', available_credits: '998.0000' } :
    String(path).includes('/members/usage') ? { items: [quota], total: 1 } :
    String(path).endsWith('/ledger') ? { items: [{ id: 'ledger', event_type: 'CHARGE', delta_credits: '-2.0000', balance_after_credits: '998.0000', created_at: order.created_at }], total: 1 } :
      { items: [], total: 0 } }) as never);
});
afterEach(cleanup);

it('嵌入仅显示输入用量与MOCK成本，成员已用及订单扣费一致', async () => {
  render(<MemoryRouter><MyBilling /></MemoryRouter>);
  expect(await screen.findByText('fixture-embedding')).toBeTruthy();
  const records = within(screen.getByRole('region', { name: '模型消费记录' }));
  expect(records.getByText('输入 1000')).toBeTruthy();
  expect(records.queryByText(/输出/)).toBeNull();
  expect(records.getByText('模拟（MOCK）')).toBeTruthy();
  expect(records.getByText('模拟，无厂商费用')).toBeTruthy();
  expect(records.getByText('2.0000')).toBeTruthy();
  const usedLabel = screen.getByText('已消费');
  expect(usedLabel.parentElement?.textContent).toContain('2.0000');
  fireEvent.click(records.getByRole('button', { name: '查看详情' }));
  expect(await screen.findByText('input_tokens：每 1000 Token / 2.0000 积分')).toBeTruthy();
  expect(screen.queryByText(/output_tokens/)).toBeNull();
});

it('企业页面同时核对企业扣额、成员已用及唯一模型订单', async () => {
  render(<MemoryRouter><MyBilling enterprise /></MemoryRouter>);
  expect(await screen.findByText('fixture-embedding')).toBeTruthy();
  expect(screen.getByText('企业总积分').parentElement?.textContent).toContain('998.0000');
  const memberRow = screen.getByText('嵌入测试成员').closest('tr')!;
  expect(within(memberRow).getByText('2.0000')).toBeTruthy();
  expect(screen.getByText('-2.0000')).toBeTruthy();
  expect(api.get).toHaveBeenCalledWith('/tenant-admin/billing/model-orders', { params: { page: 1, page_size: 20 } });
});

it('嵌入未知输入用量保留预占，不伪造输入或输出零', async () => {
  const pending = { ...order, status: 'RESERVED', reconciliation_status: 'NEEDS_REVIEW', charged_credits: '0.0000', reserved_credits: '4.0000',
    usage: { source: 'UNKNOWN', input_tokens: null, output_tokens: null, converted_tokens: null, raw_metric: null, raw_quantity: null } };
  const original = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(async (...args) => String(args[0]).includes('model-orders') ?
    { data: { items: [pending], total: 1 } } as never : original(...args));
  render(<MemoryRouter><MyBilling /></MemoryRouter>);
  expect(await screen.findByText('待核对（预占保留）')).toBeTruthy();
  const records = within(screen.getByRole('region', { name: '模型消费记录' }));
  expect(records.getByText('用量未知')).toBeTruthy();
  expect(records.getByText('4.0000')).toBeTruthy();
  expect(records.queryByText(/输入 0|输出 0/)).toBeNull();
});

it('当前费用编辑器接受已登记的仅输入嵌入费率', async () => {
  vi.mocked(authApi.me).mockResolvedValue({ user_id: 'platform', phone: '', role: 'owner', tenant_id: 'a', tenant_name: 'A', is_platform_admin: true, permissions: ['*'] });
  const model = { model_key: order.model_key, provider: order.provider, model_id: order.model_id, model_types: ['EMBEDDING'], configured: true,
    connection_status: 'NOT_TESTED', supported_usage_modes: ['REAL_TOKENS'], supported_raw_metrics: [], pricing: { configuration, updated_at: order.created_at } };
  vi.mocked(api.get).mockResolvedValue({ data: { items: [model], total: 1 } });
  vi.mocked(api.put).mockResolvedValue({ data: model.pricing });
  render(<PlatformModelPricing />);
  fireEvent.click(await screen.findByRole('button', { name: '保存当前费率' }));
  expect(await screen.findByText(/当前费率已保存/)).toBeTruthy();
  expect(api.put).toHaveBeenCalledWith('/platform-admin/billing/price-rules/' + order.model_key,
    configuration, { headers: { 'Idempotency-Key': expect.any(String) } });
});
