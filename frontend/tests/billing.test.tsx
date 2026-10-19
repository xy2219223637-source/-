import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import MyBilling from '../src/pages/MyBilling';
import Layout from '../src/components/Layout';
import { api, authApi } from '../src/api/client';

vi.mock('../src/api/client', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn() }, authApi: { me: vi.fn() } }));
const quota = { user_id: 'member', name: '测试成员', authorized: true, period_key: '2026-10', limit_credits: '4000.0000', used_credits: '21.2000', reserved_credits: '0.0000', remaining_credits: '3978.8000' };

beforeEach(() => {
  vi.mocked(authApi.me).mockReset();
  vi.mocked(api.get).mockReset(); vi.mocked(api.put).mockReset(); vi.mocked(api.post).mockReset();
  vi.mocked(authApi.me).mockResolvedValue({ user_id: 'member', phone: '', role: 'owner', tenant_id: 'a', tenant_name: 'A', is_platform_admin: false, permissions: ['*'] });
  vi.mocked(api.get).mockImplementation(async (path) => {
    const data = path === '/me/billing/config' ? { mock_enabled: true } : path === '/me/billing/quota' ? quota : path === '/tenant-admin/billing/credits' ? { balance_credits: '9978.8000', reserved_credits: '0.0000', available_credits: '9978.8000' } : path === '/tenant-admin/billing/members/usage' ? { items: [quota], total: 1 } : { items: [], total: 0 };
    return { data } as never;
  });
});
afterEach(cleanup);

describe('企业共享积分界面', () => {
  it('企业积分页面归入企业管理后台侧栏入口', async () => {
    localStorage.setItem('access_token','test-only-token');
    render(<MemoryRouter initialEntries={['/tenant-admin/billing']}><Layout /></MemoryRouter>);
    const admin = await screen.findByRole('link',{name:'企业管理后台'});
    expect(admin.getAttribute('aria-current')).toBe('page');
    expect(screen.queryByRole('link',{name:'企业积分管理'})).toBeNull();
    localStorage.clear();
  });
  it('企业后台页签包含积分管理并准确高亮', async () => {
    render(<MemoryRouter initialEntries={['/tenant-admin/billing']}><MyBilling enterprise /></MemoryRouter>);
    const tabs = within(screen.getByRole('navigation', { name: '企业管理功能' }));
    expect((await tabs.findByRole('link', { name: '企业积分管理' })).getAttribute('aria-current')).toBe('page');
    expect(tabs.getByRole('link', { name: '用户' }).getAttribute('aria-current')).toBeNull();
    expect(tabs.getByRole('link', { name: '角色' })).toBeTruthy();
  });
  it('仅有积分查看权限的成员从企业后台入口直达积分页', async () => {
    localStorage.setItem('access_token','test-only-token');
    vi.mocked(authApi.me).mockResolvedValue({ user_id: 'member', phone: '', role: 'member', tenant_id: 'a', tenant_name: 'A', is_platform_admin: false, permissions: ['billing.view'] });
    render(<MemoryRouter initialEntries={['/tenant-admin/billing']}><Layout /></MemoryRouter>);
    expect((await screen.findByRole('link',{name:'企业管理后台'})).getAttribute('href')).toBe('/tenant-admin/billing');
    localStorage.clear();
  });
  it('普通成员在侧栏能找到本人额度入口', async () => {
    localStorage.setItem('access_token','test-only-token');
    vi.mocked(authApi.me).mockResolvedValue({ user_id: 'member', phone: '', role: 'member', tenant_id: 'a', tenant_name: 'A', is_platform_admin: false, permissions: ['billing.mock.use'] });
    render(<MemoryRouter initialEntries={['/my-billing']}><Layout /></MemoryRouter>);
    expect(await screen.findByRole('link',{name:'我的消费额度'})).toBeTruthy();
    expect(screen.queryByRole('link',{name:'企业积分管理'})).toBeNull();
    localStorage.clear();
  });
  it('显示本人剩余额度，普通成员不会获得企业管理入口', async () => {
    vi.mocked(authApi.me).mockResolvedValue({ user_id: 'member', phone: '', role: 'member', tenant_id: 'a', tenant_name: 'A', is_platform_admin: false, permissions: ['billing.mock.use'] });
    render(<MemoryRouter><MyBilling /></MemoryRouter>);
    expect(await screen.findByText('3978.8000')).toBeTruthy();
    expect(screen.queryByText('个人积分余额')).toBeNull();
    expect(screen.queryByRole('link', { name: '企业积分管理' })).toBeNull();
  });

  it('额度编辑提交覆盖总值和原因，不发送钱包划拨字段', async () => {
    vi.mocked(api.put).mockResolvedValue({ data: quota });
    render(<MemoryRouter><MyBilling enterprise /></MemoryRouter>);
    fireEvent.click(await screen.findByText('设置总限额'));
    fireEvent.change(screen.getByLabelText('测试成员 本月总限额'), { target: { value: '6000.0000' } });
    fireEvent.change(screen.getByLabelText('修改原因'), { target: { value: '扩大月度额度' } });
    fireEvent.click(screen.getByText('保存'));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/tenant-admin/billing/members/member/quota', { total_limit_credits: '6000.0000', reason: '扩大月度额度' }));
    expect(await screen.findByText('月度总限额已更新，企业积分余额不变')).toBeTruthy();
  });

  it('旧独立mock消费已停用，页面保留历史消费查询且不提供测试消费按钮', async () => {
    render(<MemoryRouter><MyBilling /></MemoryRouter>);
    await screen.findByText('3978.8000');
    expect(screen.queryByRole('button', { name: '按次测试' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Token 测试' })).toBeNull();
    expect(api.post).not.toHaveBeenCalled();
    expect(api.get).toHaveBeenCalledWith('/me/billing/orders', { params: { page: 1 } });
  });
});
