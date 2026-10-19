import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ModelOrders from '../src/components/ModelOrders';
import PlatformModelPricing from '../src/pages/PlatformModelPricing';
import { api, authApi } from '../src/api/client';

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), put: vi.fn() }, authApi: { me: vi.fn() } }));
const configuration = { usage_mode: 'FOLDED_TOKENS', metrics: [
  { metric: 'converted_tokens', unit_size: 1, credits_per_unit: '0.0001' }],
  conversion: { source_metric: 'image_count', source_unit_size: 10, tokens_per_unit: 1 } };
const model = { model_key: 'b'.repeat(32), provider: 'offline:image', model_id: 'local-image',
  model_types: ['IMAGE'], configured: true, connection_status: 'NOT_TESTED',
  supported_usage_modes: ['FOLDED_TOKENS'], supported_raw_metrics: ['image_count'],
  pricing: { model_key: 'b'.repeat(32), configuration, updated_at: '2026-10-19T00:00:00Z' } };
const order = { id: 'a'.repeat(32), request_id: 'request', tenant_id: 'tenant', user_id: 'member',
  model_key: model.model_key, provider: model.provider, model_id: model.model_id, execution_mode: 'MOCK',
  status: 'SETTLED', reconciliation_status: 'RESOLVED', usage: { source: 'FOLDED_TOKENS',
    input_tokens: null, output_tokens: null, converted_tokens: '0.3', raw_metric: 'image_count', raw_quantity: 3 },
  supplier_cost: { status: 'NOT_INCURRED', amount: null, currency: null },
  charged_credits: '0.0001', reserved_credits: '0.0000', pricing_snapshot: configuration,
  provider_request_id: 'mock-image', created_at: '2026-10-19T00:00:00Z' };
beforeEach(() => {
  vi.mocked(api.get).mockReset(); vi.mocked(api.put).mockReset();
  vi.mocked(authApi.me).mockResolvedValue({ user_id: 'platform', phone: '', tenant_id: 'tenant', tenant_name: 'A',
    role: 'owner', permissions: ['*'], is_platform_admin: true });
  vi.mocked(api.get).mockImplementation(async path => ({ data: String(path).includes('price-rules') ?
    { items: [model], total: 1, page: 1, page_size: 20 } : String(path).endsWith(order.id) ?
    order : { items: [order], total: 1, page: 1, page_size: 20 } }) as never);
  vi.mocked(api.put).mockResolvedValue({ data: model.pricing });
});
afterEach(cleanup);

it('图像费率保存和回显完整折算规则，只开放已登记图片来源', async () => {
  render(<PlatformModelPricing />);
  const amount = await screen.findByLabelText('每单位折算 Token 积分');
  expect((amount as HTMLInputElement).value).toBe('0.0001');
  expect((screen.getByLabelText('原始用量单位数量') as HTMLSelectElement).value).toBe('10');
  expect((screen.getByLabelText('每单位原始用量折算 Token') as HTMLInputElement).value).toBe('1');
  expect(screen.queryByRole('option', { name: '真实 Token' })).toBeNull();
  expect(screen.queryByRole('option', { name: '音频毫秒' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: '保存当前费率' }));
  await screen.findByRole('status');
  expect(api.put).toHaveBeenCalledWith('/platform-admin/billing/price-rules/' + model.model_key,
    configuration, { headers: { 'Idempotency-Key': expect.any(String) } });
  expect((screen.getByLabelText('每单位折算 Token 积分') as HTMLInputElement).value).toBe('0.0001');
});

it.each([false, true])('图像消费展示有效图片、精确折算、MOCK、积分与快照 enterprise=%s', async enterprise => {
  render(<ModelOrders enterprise={enterprise} refreshKey={0} />);
  expect(await screen.findByText('模拟折算 Token 0.3')).toBeTruthy();
  expect(screen.getByText('原始依据：有效图片数 3')).toBeTruthy();
  expect(screen.getByText('模拟（MOCK）')).toBeTruthy();
  expect(screen.getByText('模拟，无厂商费用')).toBeTruthy();
  expect(screen.getByText('0.0001')).toBeTruthy();
  expect(screen.getByText('已结算')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '查看详情' }));
  const prefix = enterprise ? '/tenant-admin/billing/model-orders' : '/me/billing/model-orders';
  await waitFor(() => expect(api.get).toHaveBeenCalledWith(prefix + '/' + order.id));
  expect(await screen.findByText('折算依据：每 10 image_count 折算 1 Token')).toBeTruthy();
  expect(screen.queryByText('真实 Token')).toBeNull();
});

it('图像未知用量显示待核对及保留预占，不伪造有效图片零', async () => {
  const pending = { ...order, status: 'RESERVED', reconciliation_status: 'NEEDS_REVIEW',
    charged_credits: '0.0000', reserved_credits: '0.3000', usage: { source: 'UNKNOWN',
      input_tokens: null, output_tokens: null, converted_tokens: null, raw_metric: null, raw_quantity: null } };
  vi.mocked(api.get).mockResolvedValue({ data: { items: [pending], total: 1, page: 1, page_size: 20 } });
  render(<ModelOrders enterprise={false} refreshKey={0} />);
  expect(await screen.findByText('待核对（预占保留）')).toBeTruthy();
  expect(screen.getByText('用量未知')).toBeTruthy();
  expect(screen.getByText('0.3000')).toBeTruthy();
  expect(screen.queryByText('原始依据：有效图片数 0')).toBeNull();
});
