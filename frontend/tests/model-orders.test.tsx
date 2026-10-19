import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import MyBilling from '../src/pages/MyBilling';
import { api, authApi } from '../src/api/client';

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), put: vi.fn() }, authApi: { me: vi.fn() } }));
const order = { id: 'a'.repeat(32), request_id: 'request', tenant_id: 'tenant', user_id: 'member',
  model_key: 'b'.repeat(32), provider: 'offline:text', model_id: 'text-vision-model', execution_mode: 'MOCK',
  status: 'SETTLED', reconciliation_status: 'RESOLVED', usage: { source: 'REAL_TOKENS', input_tokens: 2000,
    output_tokens: 1000, converted_tokens: null, raw_metric: null, raw_quantity: null },
  supplier_cost: { status: 'NOT_INCURRED', amount: null, currency: null }, charged_credits: '5.0000', reserved_credits: '0.0000',
  pricing_snapshot: { usage_mode: 'REAL_TOKENS', metrics: [{ metric: 'input_tokens', unit_size: 1000, credits_per_unit: '1.0000' }], conversion: null },
  provider_request_id: 'mock-text', created_at: '2026-10-19T00:00:00Z' };
beforeEach(() => {
  vi.mocked(api.get).mockReset();
  vi.mocked(authApi.me).mockResolvedValue({ user_id: 'member', tenant_id: 'tenant', tenant_name: 'A', role: 'owner', phone: '', permissions: ['*'], is_platform_admin: false });
  vi.mocked(api.get).mockImplementation(async path => ({ data: String(path).includes('model-orders') ?
    String(path).endsWith(order.id) ? order : { items: [order], total: 1, page: 1, page_size: 20 } :
    String(path).endsWith('/quota') ? { authorized: true, remaining_credits: '95.0000', period_key: '2026-10' } :
    String(path).endsWith('/credits') ? { balance_credits: '995.0000', reserved_credits: '0.0000', available_credits: '995.0000' } : { items: [], total: 0 } }) as never);
});
afterEach(cleanup);

it('本人消费展示模拟来源、Token、积分及无厂商费用，详情沿本人查询', async () => {
  render(<MemoryRouter><MyBilling /></MemoryRouter>);
  expect(await screen.findByText('text-vision-model')).toBeTruthy();
  expect(screen.getByText('模拟 Token（真实 Token 格式）')).toBeTruthy();
  expect(screen.getByText('输入 2000 / 输出 1000')).toBeTruthy();
  expect(screen.getByText('模拟，无厂商费用')).toBeTruthy();
  expect(screen.getByText('已结算')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
  await waitFor(() => expect(api.get).toHaveBeenCalledWith('/me/billing/model-orders/' + order.id));
  expect(await screen.findByText('订单计算依据')).toBeTruthy();
  expect(screen.queryByRole('button', { name: '按次测试' })).toBeNull();
});

it('企业页面使用企业模型查询，未知用量保留预占并显示待核对', async () => {
  const pending = { ...order, status: 'RESERVED', reconciliation_status: 'NEEDS_REVIEW', charged_credits: '0.0000', reserved_credits: '9.0000',
    usage: { source: 'UNKNOWN', input_tokens: null, output_tokens: null, converted_tokens: null, raw_metric: null, raw_quantity: null } };
  const original = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(async (...args) => String(args[0]).includes('model-orders') ?
    { data: { items: [pending], total: 1 } } as never : original(...args));
  render(<MemoryRouter><MyBilling enterprise /></MemoryRouter>);
  expect(await screen.findByText('待核对（预占保留）')).toBeTruthy();
  expect(screen.getByText('用量未知')).toBeTruthy();
  expect(screen.getByText('9.0000')).toBeTruthy();
  expect(api.get).toHaveBeenCalledWith('/tenant-admin/billing/model-orders', { params: { page: 1, page_size: 20 } });
});

it('折算来源与真实Token分开，REAL未知供应商成本不能显示为零', async () => {
  const folded = { ...order, execution_mode: 'REAL', usage: { source: 'FOLDED_TOKENS', input_tokens: null, output_tokens: null,
    converted_tokens: '1500', raw_metric: 'image_count', raw_quantity: 1 }, supplier_cost: { status: 'UNKNOWN', amount: null, currency: null } };
  const original = vi.mocked(api.get).getMockImplementation()!;
  vi.mocked(api.get).mockImplementation(async (...args) => String(args[0]).includes('model-orders') ?
    { data: { items: [folded], total: 1 } } as never : original(...args));
  render(<MemoryRouter><MyBilling /></MemoryRouter>);
  expect(await screen.findByText('折算 Token 1500')).toBeTruthy();
  expect(screen.getByText('原始依据：有效图片数 1')).toBeTruthy();
  expect(screen.getByText('供应商成本未知')).toBeTruthy();
});
