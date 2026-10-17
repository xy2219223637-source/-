import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { lazy, Suspense } from 'react';
import { MemoryRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import RouteTitle from '../src/components/RouteTitle';
import Layout from '../src/components/Layout';
import Login from '../src/pages/Login';
import App from '../src/App';
import { api, authApi } from '../src/api/client';
import { COMPANY_NAME } from '../src/lib/siteIdentity';
import { getRouteTitle, routeTitles } from '../src/lib/routeTitles';

vi.mock('../src/api/client', () => ({
  api: { get: vi.fn().mockRejectedValue({ response: { status: 404 } }) },
  authApi: { me: vi.fn().mockResolvedValue({ role: 'owner', is_platform_admin: false, permissions: ['*'] }) },
  dashboardApi: { stats: vi.fn().mockRejectedValue(new Error('Test data unavailable')) },
}));

beforeEach(() => {
  vi.mocked(api.get).mockRejectedValue({ response: { status: 404 } });
  vi.mocked(authApi.me).mockResolvedValue({ role: 'owner', is_platform_admin: false, permissions: ['*'] } as Awaited<ReturnType<typeof authApi.me>>);
  localStorage.clear();
  document.title = 'Previous page';
});
afterEach(() => {
  cleanup();
  localStorage.clear();
  window.history.replaceState(null, '', '/');
});

// Optional read-only visual fixtures, using the real Layout and mocked session.
function exportLayoutFixture(filename: string) {
  const directory = process.env.SITE_IDENTITY_VISUAL_DIR;
  if (!directory) return;
  mkdirSync(directory, { recursive: true });
  writeFileSync(resolve(directory, filename), `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><base href="http://localhost:5173/"><link rel="stylesheet" href="/src/index.css"><title>${COMPANY_NAME}</title></head><body>${document.body.innerHTML}</body></html>`, 'utf8');
}

describe('集中路由标题', () => {
  it.each(routeTitles)('$path 使用固定功能名称及公司全称', ({ path, pageName }) => {
    const pathname = path === '*' ? '/unknown/page' : path.replace(':id', '123');
    expect(getRouteTitle(pathname)).toBe(pageName ? `${pageName} - ${COMPANY_NAME}` : COMPANY_NAME);
  });

  it('覆盖 App 全部路由, 并防止路由与标题清单漂移', () => {
    const source = readFileSync(resolve('src/App.tsx'), 'utf8');
    const paths = [...source.matchAll(/<Route path="([^"]+)"/g)].map(([, path]) => path === '*' ? '*' : `/${path}`);
    expect(source).toContain('<Route index');
    expect(routeTitles.map(({ path }) => path).sort()).toEqual(['/', ...paths].sort());
  });

  it('入口 HTML 默认标题与公司名称一致', () => {
    const html = readFileSync(resolve('index.html'), 'utf8');
    expect(html.match(/<title>(.*?)<\/title>/)?.[1]).toBe(COMPANY_NAME);
  });

  it('静态创建页优先于详情页, 查询参数和用户输入不进入标题', () => {
    expect(getRouteTitle('/products/new')).toBe(`创建商品 - ${COMPANY_NAME}`);
    expect(getRouteTitle('/products/user-project-name')).toBe(`商品详情 - ${COMPANY_NAME}`);
    render(<MemoryRouter initialEntries={['/creative-projects/123?name=Untrusted#preview']}><RouteTitle /></MemoryRouter>);
    expect(document.title).toBe(`创意画布 - ${COMPANY_NAME}`);
    expect(getRouteTitle('/LOGIN/')).toBe(`登录 - ${COMPANY_NAME}`);
  });

  it('直接访问及重新挂载后恢复正确标题', () => {
    const mount = () => render(<MemoryRouter initialEntries={['/change-password']}><RouteTitle /></MemoryRouter>);
    const first = mount();
    expect(document.title).toBe(`修改密码 - ${COMPANY_NAME}`);
    first.unmount();
    document.title = 'Stale title';
    mount();
    expect(document.title).toBe(`修改密码 - ${COMPANY_NAME}`);
  });

  it('切换, 后退, 前进及登录落地后的标题跟随当前路由', () => {
    function Controls() {
      const navigate = useNavigate();
      return <>
        <button onClick={() => navigate('/products')}>商品</button>
        <button onClick={() => navigate('/change-password')}>密码</button>
        <button onClick={() => navigate('/qianchuan/oauth/result?session_id=test')}>授权</button>
        <button onClick={() => navigate('/')}>首页</button>
        <button onClick={() => navigate(-1)}>后退</button>
        <button onClick={() => navigate(1)}>前进</button>
      </>;
    }
    render(<MemoryRouter initialEntries={['/login']}><RouteTitle /><Controls /></MemoryRouter>);
    expect(document.title).toBe(`登录 - ${COMPANY_NAME}`);
    fireEvent.click(screen.getByText('商品'));
    expect(document.title).toBe(`商品管理 - ${COMPANY_NAME}`);
    fireEvent.click(screen.getByText('密码'));
    expect(document.title).toBe(`修改密码 - ${COMPANY_NAME}`);
    fireEvent.click(screen.getByText('后退'));
    expect(document.title).toBe(`商品管理 - ${COMPANY_NAME}`);
    fireEvent.click(screen.getByText('前进'));
    expect(document.title).toBe(`修改密码 - ${COMPANY_NAME}`);
    fireEvent.click(screen.getByText('授权'));
    expect(document.title).toBe(`千川授权结果 - ${COMPANY_NAME}`);
    fireEvent.click(screen.getByText('首页'));
    expect(document.title).toBe(COMPANY_NAME);
  });

  it.each([
    ['/video-taxonomy', '/tenant-admin/taxonomy', '视频分类管理'],
    ['/performance-video-projects', '/smart-video-compose', '智能成片'],
    ['/video-assets', '/semi-finished-assets', '半成品素材库'],
    ['/unknown', '/', ''],
  ])('重定向 %s 使用最终落地标题', (from, to, pageName) => {
    render(<MemoryRouter initialEntries={[from]}><RouteTitle /><Routes>
      <Route path={from} element={<Navigate to={to} replace />} />
      <Route path={to} element={<div>Destination</div>} />
    </Routes></MemoryRouter>);
    expect(document.title).toBe(pageName ? `${pageName} - ${COMPANY_NAME}` : COMPANY_NAME);
  });

  it('懒加载中的页面也立即更新标题', () => {
    const Pending = lazy(() => new Promise<{ default: () => null }>(() => {}));
    render(<MemoryRouter initialEntries={['/manual-video-compose/123']}><RouteTitle /><Suspense fallback={<p>Loading</p>}><Pending /></Suspense></MemoryRouter>);
    expect(screen.getByText('Loading')).toBeTruthy();
    expect(document.title).toBe(`视频编排编辑 - ${COMPANY_NAME}`);
  });

  it('真实 App 的登录与修改密码页不依赖业务 Layout 更新标题', async () => {
    window.history.replaceState(null, '', '/login?returnTo=/products');
    const login = render(<App />);
    expect(document.title).toBe(`登录 - ${COMPANY_NAME}`);
    await screen.findByRole('heading', { name: '登录品牌创作平台' });
    login.unmount();
    window.history.replaceState(null, '', '/change-password');
    render(<App />);
    expect(document.title).toBe(`修改密码 - ${COMPANY_NAME}`);
    await screen.findByRole('heading', { name: '设置新密码' });
  });
});

describe('网站身份定点展示', () => {
  it('标签页图标使用独立方形标识, 不依赖字体并带缓存版本', () => {
    const html = readFileSync(resolve('index.html'), 'utf8');
    const svg = readFileSync(resolve('public/favicon.svg'), 'utf8');
    const icon = new DOMParser().parseFromString(svg, 'image/svg+xml');
    expect(html).toContain('href="/favicon.svg?v=artinjecter-1"');
    expect(icon.querySelector('parsererror')).toBeNull();
    expect(icon.documentElement.getAttribute('viewBox')).toBe('0 0 32 32');
    expect(icon.querySelector('title')?.textContent).toBe('ArtInjecter');
    expect(icon.querySelector('path')?.getAttribute('d')).toBeTruthy();
    expect(icon.querySelector('text')).toBeNull();
  });

  it('登录页独立展示全称, 保留原说明与表单', () => {
    render(<Login />);
    expect(screen.getByText(COMPANY_NAME)).toBeTruthy();
    expect(screen.queryByText('Dirovo')).toBeNull();
    expect(screen.queryByText('蒂洛薇')).toBeNull();
    expect(screen.getByLabelText('手机号')).toBeTruthy();
    expect(screen.getByLabelText('密码')).toBeTruthy();
    expect(screen.getByRole('button', { name: '登录' })).toBeTruthy();
  });

  it('侧栏只展示指定 Logo, 移动顶部保留全称且导航可打开关闭', async () => {
    localStorage.setItem('access_token', 'test-only');
    render(<MemoryRouter><Layout /></MemoryRouter>);
    await waitFor(() => expect(screen.getByRole('link', { name: '工作台' })).toBeTruthy());
    expect(screen.getAllByText(COMPANY_NAME)).toHaveLength(1);
    const logo = screen.getByRole('img', { name: `${COMPANY_NAME} Logo` });
    expect(logo.getAttribute('src')).toContain('company-logo.png');
    expect(logo.getAttribute('width')).toBe('2000');
    expect(logo.getAttribute('height')).toBe('468');
    const sidebar = document.querySelector('aside')!;
    expect(sidebar.textContent).not.toContain(COMPANY_NAME);
    expect(sidebar.className).toContain('hidden');
    exportLayoutFixture('layout-desktop.html');
    fireEvent.click(screen.getByRole('button', { name: '打开导航' }));
    expect(sidebar.className).toContain('fixed');
    exportLayoutFixture('layout-drawer.html');
    fireEvent.click(screen.getByRole('button', { name: '关闭导航' }));
    expect(sidebar.className).toContain('hidden');
  });
});
