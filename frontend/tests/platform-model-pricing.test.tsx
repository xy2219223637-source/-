import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import PlatformModelPricing from '../src/pages/PlatformModelPricing';
import { api, authApi } from '../src/api/client';

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), put: vi.fn() }, authApi: { me: vi.fn() } }));
const configuration = { usage_mode: 'REAL_TOKENS', metrics: [
  { metric: 'input_tokens', unit_size: 1000, credits_per_unit: '0.2000' },
  { metric: 'output_tokens', unit_size: 1000, credits_per_unit: '0.8000' },
], conversion: null };
const item = { model_key: 'a'.repeat(32), provider: 'offline:fixture', model_id: 'fixture-text', provider_model_name: null,
  model_types: ['TEXT', 'VISION'], connection_status: 'NOT_TESTED', configured: true,
  supported_usage_modes: ['REAL_TOKENS'], supported_raw_metrics: [],
  pricing: { model_key: 'a'.repeat(32), configuration, updated_at: '2026-10-19T00:00:00Z' } };
beforeEach(() => {
  vi.mocked(authApi.me).mockReset(); vi.mocked(api.get).mockReset(); vi.mocked(api.put).mockReset();
  vi.mocked(authApi.me).mockResolvedValue({ user_id: 'platform', phone: '', tenant_id: 'a', tenant_name: 'A', role: 'owner', permissions: ['*'], is_platform_admin: true });
  vi.mocked(api.get).mockResolvedValue({ data: { items: [item], total: 1, page: 1, page_size: 20 } });
  vi.mocked(api.put).mockResolvedValue({ data: item.pricing });
});
afterEach(cleanup);

describe('平台模型当前费率', () => {
  it('保存当前费率使用完整替换和幂等键，成功后刷新核对', async () => {
    render(<PlatformModelPricing />);
    const amount = await screen.findByLabelText('每单位输入 Token 积分');
    fireEvent.change(amount, { target: { value: '2.5000' } });
    fireEvent.click(screen.getByRole('button', { name: '保存当前费率' }));
    await screen.findByRole('status');
    expect(api.put).toHaveBeenCalledExactlyOnceWith('/platform-admin/billing/price-rules/' + item.model_key,
      { ...configuration, metrics: [{ ...configuration.metrics[0], credits_per_unit: '2.5000' }, configuration.metrics[1]] },
      { headers: { 'Idempotency-Key': expect.stringMatching(/^[0-9a-f-]{36}$/) } });
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('费率版本记录')).toBeNull();
  });
  it('同一未确定保存重试复用幂等键', async () => {
    vi.mocked(api.put).mockRejectedValue({ isAxiosError: true, response: { data: { detail: { message: '暂不可用' } } } });
    render(<PlatformModelPricing />);
    fireEvent.click(await screen.findByRole('button', { name: '保存当前费率' }));
    await screen.findByRole('alert');
    fireEvent.click(screen.getByRole('button', { name: '保存当前费率' }));
    await vi.waitFor(() => expect(api.put).toHaveBeenCalledTimes(2));
    expect(vi.mocked(api.put).mock.calls[0][2]).toEqual(vi.mocked(api.put).mock.calls[1][2]);
  });
  it('没有已登记协议和视频未实现时仅展示原因', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [{ ...item, supported_usage_modes: [], pricing: null, connection_status: 'NOT_IMPLEMENTED' }], total: 1 } });
    render(<PlatformModelPricing />);
    await screen.findByText('当前能力未实现。');
    expect(screen.queryByRole('button', { name: '保存当前费率' })).toBeNull();
  });
  it('支持折算Token完整结构但不填默认比例和金额', async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { items: [{ ...item, supported_usage_modes: ['FOLDED_TOKENS'], supported_raw_metrics: ['image_count'], pricing: null }], total: 1 } });
    render(<PlatformModelPricing />);
    expect((await screen.findByLabelText('每单位折算 Token 积分') as HTMLInputElement).value).toBe('');
    expect((screen.getByLabelText('每单位原始用量折算 Token') as HTMLInputElement).value).toBe('');
  });
  it('企业管理员没有费率读取或修改请求', async () => {
    vi.mocked(authApi.me).mockResolvedValue({ user_id: 'owner', phone: '', tenant_id: 'a', tenant_name: 'A', role: 'owner', permissions: ['*'], is_platform_admin: false });
    render(<PlatformModelPricing />);
    await screen.findByRole('alert');
    expect(api.get).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
  });
});
