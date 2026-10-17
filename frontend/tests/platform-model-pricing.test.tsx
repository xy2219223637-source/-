import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import PlatformModelPricing from '../src/pages/PlatformModelPricing';
import { api, authApi } from '../src/api/client';

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), post: vi.fn() }, authApi: { me: vi.fn() } }));
const fixed = { id: 'fixed-1', feature_code: 'mock_fixed', billing_mode: 'FIXED', pricing_version: 1, status: 'ACTIVE', fixed_credits: '20.0000', metrics: [] };
const usage = { id: 'usage-1', feature_code: 'mock_text', billing_mode: 'USAGE', pricing_version: 1, status: 'ACTIVE', fixed_credits: null, metrics: [
  { metric: 'input_tokens', unit_size: 1000, credits_per_unit: '0.20' },
  { metric: 'output_tokens', unit_size: 1000, credits_per_unit: '0.80' },
] };
const draft = { ...fixed, id: 'fixed-2', pricing_version: 2, fixed_credits: '30.0000', status: 'DRAFT' };
beforeEach(() => {
  vi.mocked(authApi.me).mockReset(); vi.mocked(api.get).mockReset(); vi.mocked(api.post).mockReset();
  vi.mocked(authApi.me).mockResolvedValue({ user_id: 'platform', phone: '', tenant_id: 'a', tenant_name: 'A', role: 'owner', permissions: ['*'], is_platform_admin: true });
  vi.mocked(api.get).mockImplementation(async path => ({ data: path.endsWith('/summary') ? { items: [
    { feature_code: 'mock_fixed', billing_mode: 'FIXED', active: fixed, next_version: 3 },
    { feature_code: 'mock_text', billing_mode: 'USAGE', active: usage, next_version: 2 },
  ] } : { items: [draft, fixed, usage], total: 3 } }));
  vi.mocked(api.post).mockResolvedValue({ data: draft });
});
afterEach(cleanup);

describe('平台模型费用管理', () => {
  it('展示两种当前费率，手动调整按次金额仅保存草稿', async () => {
    render(<PlatformModelPricing />);
    const amount = await screen.findByLabelText('每次扣除积分');
    expect((amount as HTMLInputElement).value).toBe('20.0000');
    expect((screen.getByLabelText('每单位输入 Token 积分') as HTMLInputElement).value).toBe('0.20');
    fireEvent.change(amount, { target: { value: '35.5000' } });
    fireEvent.click(screen.getByRole('button', { name: '保存按次新版本' }));
    await screen.findByRole('status');
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/platform-admin/billing/price-rules', { feature_code: 'mock_fixed', billing_mode: 'FIXED', version: 3, fixed_credits: '35.5000' });
  });
  it('输入输出分别换算，提交精确金额字符串和整数单位数量', async () => {
    render(<PlatformModelPricing />);
    await screen.findByLabelText('每次扣除积分');
    fireEvent.change(screen.getByLabelText('输入 Token 单位数量'), { target: { value: '10000' } });
    fireEvent.change(screen.getByLabelText('每单位输入 Token 积分'), { target: { value: '2.5000' } });
    fireEvent.change(screen.getByLabelText('每单位输出 Token 积分'), { target: { value: '4.0000' } });
    fireEvent.click(screen.getByRole('button', { name: '保存Token新版本' }));
    await screen.findByRole('status');
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/platform-admin/billing/price-rules', { feature_code: 'mock_text', billing_mode: 'USAGE', version: 2, metrics: [
      { metric: 'input_tokens', unit_size: 10000, credits_per_unit: '2.5000' },
      { metric: 'output_tokens', unit_size: 1000, credits_per_unit: '4.0000' },
    ] });
  });
  it('仅通过明确启用操作发布已核对的版本', async () => {
    render(<PlatformModelPricing />);
    fireEvent.click(await screen.findByRole('button', { name: '启用按次功能（测试）版本2' }));
    await screen.findByText('版本 v2 已启用，将用于该功能的新订单。');
    expect(api.post).toHaveBeenCalledExactlyOnceWith('/platform-admin/billing/price-rules/fixed-2/activate');
  });
  it('企业管理员不能查看费率或发送配置请求', async () => {
    vi.mocked(authApi.me).mockResolvedValue({ user_id: 'owner', phone: '', tenant_id: 'a', tenant_name: 'A', role: 'owner', permissions: ['*'], is_platform_admin: false });
    render(<PlatformModelPricing />);
    await screen.findByRole('alert');
    expect(api.get).not.toHaveBeenCalled(); expect(api.post).not.toHaveBeenCalled();
  });
  it('重复版本或不确定响应提示核对，保留输入且不自动启用', async () => {
    vi.mocked(api.post).mockRejectedValue({ isAxiosError: true, response: { status: 409, data: { detail: { message: '费率版本已存在' } } } });
    render(<PlatformModelPricing />);
    fireEvent.click(await screen.findByRole('button', { name: '保存按次新版本' }));
    await screen.findByText('费率版本已存在');
    expect((screen.getByLabelText('每次扣除积分') as HTMLInputElement).value).toBe('20.0000');
    expect(api.post).toHaveBeenCalledTimes(1);
  });
});
