import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import Layout from '../src/components/Layout';
import EcommerceVideoDetail from '../src/pages/EcommerceVideoDetail';
import EcommerceVideoLibrary from '../src/pages/EcommerceVideoLibrary';
import { authApi, ecommerceProductApi, ecommerceVideoLibraryApi, ecommerceVideoTaxonomyApi } from '../src/api/client';
import type { VideoLibraryAsset } from '../src/types';

vi.mock('../src/api/client', () => ({
  mediaUrl: (value: string) => value,
  authApi: { me: vi.fn() },
  ecommerceProductApi: { get: vi.fn().mockResolvedValue({ id: 1, name: '电商商品' }), list: vi.fn().mockResolvedValue([{ id: 1, name: '电商商品' }]) },
  ecommerceVideoLibraryApi: { get: vi.fn(), list: vi.fn(), reanalyze: vi.fn(), updateReusableStatus: vi.fn() },
  ecommerceVideoTaxonomyApi: { catalog: vi.fn().mockResolvedValue({ categories: [], tags: [], groups: [] }) },
}));
vi.mock('../src/components/EcommerceVideoTaxonomyFilters', () => ({ matchesVideoTaxonomy: () => true, VideoTaxonomyFilters: () => null, VideoBatchTaxonomyBar: () => null }));

const asset = {
  id: 101, product_id: 1, filename: '商品素材.mp4', asset_kind: 'material', library_type: 'finished',
  object_url: '/material.mp4', source: 'manual_upload', duration_seconds: 8, width: 720, height: 1280,
  fps: 30, has_audio: true, burned_subtitles: false, has_background_music: false,
  reusable_status: 'reusable', excluded: false, status: 'analyzed', analysis: null,
  highlights: [], effective_tags: [], ai_tags: [], manual_tags: [], removed_ai_tags: [],
  category_id: null, category: null, taxonomy_tags: [], public_tags: [], personal_tags: [],
} as VideoLibraryAsset;
const finalAsset = {
  ...asset, filename: '商品成片.mp4', asset_kind: 'final_output', object_url: '/final.mp4',
  reusable_status: 'blocked', status: 'uploaded',
} as VideoLibraryAsset;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.setItem('access_token', 'test-token');
  vi.mocked(ecommerceProductApi.get).mockResolvedValue({ id: 1, name: '电商商品' } as Awaited<ReturnType<typeof ecommerceProductApi.get>>);
  vi.mocked(ecommerceProductApi.list).mockResolvedValue([{ id: 1, name: '电商商品' } as Awaited<ReturnType<typeof ecommerceProductApi.get>>]);
  vi.mocked(ecommerceVideoTaxonomyApi.catalog).mockResolvedValue({ categories: [], tags: [], groups: [] } as Awaited<ReturnType<typeof ecommerceVideoTaxonomyApi.catalog>>);
  vi.mocked(ecommerceVideoLibraryApi.get).mockResolvedValue(finalAsset);
  vi.mocked(ecommerceVideoLibraryApi.list).mockResolvedValue([finalAsset]);
  vi.mocked(ecommerceVideoLibraryApi.reanalyze).mockResolvedValue({ ...asset, status: 'queued_for_analysis' });
  vi.mocked(ecommerceVideoLibraryApi.updateReusableStatus).mockResolvedValue(asset);
  vi.mocked(authApi.me).mockResolvedValue({ role: 'employee', is_platform_admin: false, permissions: ['ecommerce_assets.access'] } as Awaited<ReturnType<typeof authApi.me>>);
});
afterEach(() => { cleanup(); localStorage.clear(); });

function detail(kind: 'material' | 'final_output') {
  return render(<MemoryRouter initialEntries={['/videos/101']}><Routes><Route path="/videos/:id" element={<EcommerceVideoDetail assetKind={kind} />} /></Routes></MemoryRouter>);
}

describe('电商商品精修入口', () => {
  it('商品成片详情只保留查看下载, 精修入口统一放在精修页', async () => {
    detail('final_output');
    await screen.findByRole('heading', { name: finalAsset.filename });
    expect(screen.queryByRole('link', { name: '精修商品成片' })).toBeNull();
    expect(screen.getByRole('link', { name: '返回商品成片库' }).getAttribute('href')).toBe('/ecommerce-final-videos');
  });

  it('已排除的商品成片不显示精修入口', async () => {
    vi.mocked(ecommerceVideoLibraryApi.get).mockResolvedValue({ ...finalAsset, excluded: true });
    detail('final_output');
    await screen.findByRole('heading', { name: finalAsset.filename });
    expect(screen.queryByRole('link', { name: '精修商品成片' })).toBeNull();
  });

  it('商品成片详情不展示库内分析或 AI 标签, 保留分类和人工标签', async () => {
    vi.mocked(ecommerceVideoLibraryApi.get).mockResolvedValue({
      ...finalAsset, status: 'analysis_failed', effective_tags: ['旧 AI 内容标签'],
      analysis: { summary: '旧成片分析摘要', error_message: '旧成片分析错误' },
    } as VideoLibraryAsset);
    detail('final_output');
    await screen.findByRole('heading', { name: finalAsset.filename });
    expect(screen.queryByText(/分析|旧 AI 内容标签/)).toBeNull();
    expect(screen.getByRole('heading', { name: '分类与标签' })).toBeTruthy();
    expect(screen.getByText('公共标签')).toBeTruthy();
    expect(screen.getByText('个人标签')).toBeTruthy();
    expect(screen.getByRole('button', { name: '编辑分类和标签' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: '精修商品成片' })).toBeNull();
    expect(screen.getByRole('link', { name: '下载视频' })).toBeTruthy();
  });

  it.each(['validating', 'validation_failed'])('商品成片详情只保留文件校验提示 %s, 校验前不可精修', async status => {
    vi.mocked(ecommerceVideoLibraryApi.get).mockResolvedValue({ ...finalAsset, status });
    detail('final_output');
    await screen.findByRole('heading', { name: finalAsset.filename });
    expect(screen.getByText(status === 'validating' ? /正在校验视频文件/ : /文件校验失败/)).toBeTruthy();
    expect(screen.queryByRole('link', { name: '精修商品成片' })).toBeNull();
    expect(screen.queryByText(/分析/)).toBeNull();
  });

  it('商品素材详情不作为被精修的源视频入口', async () => {
    vi.mocked(ecommerceVideoLibraryApi.get).mockResolvedValue(asset);
    detail('material');
    expect((await screen.findByRole('link', { name: '返回商品素材库' })).getAttribute('href')).toBe('/ecommerce-video-assets');
    expect(screen.queryByRole('link', { name: /精修/ })).toBeNull();
  });

  it('商品成片列表不提供精修入口, 仍使用电商成片接口', async () => {
    render(<MemoryRouter><EcommerceVideoLibrary assetKind="final_output" /></MemoryRouter>);
    await screen.findByRole('heading', { name: finalAsset.filename });
    expect(screen.queryByRole('link', { name: '精修商品成片' })).toBeNull();
    expect(screen.getByRole('button', { name: '查看详情' })).toBeTruthy();
    expect(screen.getByRole('link', { name: '下载' })).toBeTruthy();
    expect(ecommerceVideoLibraryApi.list).toHaveBeenCalledWith(1, 'finished', 'final_output');
  });

  it.each(['uploaded', 'queued_for_analysis', 'analyzing', 'analyzed', 'analysis_failed'])('商品成片列表忽略历史分析状态 %s, 不显示分析和复用功能', async status => {
    vi.mocked(ecommerceVideoLibraryApi.list).mockResolvedValue([{
      ...finalAsset, status, effective_tags: ['旧 AI 内容标签'],
      analysis: { summary: '旧成片分析摘要', error_message: '旧成片分析错误' },
    } as VideoLibraryAsset]);
    render(<MemoryRouter><EcommerceVideoLibrary assetKind="final_output" /></MemoryRouter>);
    await screen.findByRole('heading', { name: finalAsset.filename });
    expect(screen.queryByRole('combobox', { name: '状态' })).toBeNull();
    expect(screen.queryByRole('combobox', { name: /复用状态/ })).toBeNull();
    expect(screen.queryByText(/分析|旧 AI 内容标签/)).toBeNull();
    expect(screen.getByPlaceholderText('文件名或标签')).toBeTruthy();
    expect(ecommerceVideoLibraryApi.reanalyze).not.toHaveBeenCalled();
  });

  it('商品成片列表搜索人工分类标签, 不依赖 AI 摘要', async () => {
    vi.mocked(ecommerceVideoLibraryApi.list).mockResolvedValue([{
      ...finalAsset, taxonomy_tags: [{ id: 1, name: '卖点展示', scope: 'public', status: 'active', source: 'manual' }],
      analysis: { summary: '旧成片分析摘要' },
    } as VideoLibraryAsset]);
    render(<MemoryRouter><EcommerceVideoLibrary assetKind="final_output" /></MemoryRouter>);
    await screen.findByRole('heading', { name: finalAsset.filename });
    fireEvent.change(screen.getByPlaceholderText('文件名或标签'), { target: { value: '卖点展示' } });
    expect(screen.getByRole('heading', { name: finalAsset.filename })).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText('文件名或标签'), { target: { value: '旧成片分析摘要' } });
    expect(screen.queryByRole('heading', { name: finalAsset.filename })).toBeNull();
    expect(screen.getByText('暂无视频')).toBeTruthy();
  });

  it.each(['validating', 'validation_failed'])('商品成片列表区分文件校验状态 %s, 不提供分析按钮', async status => {
    vi.mocked(ecommerceVideoLibraryApi.list).mockResolvedValue([{ ...finalAsset, status }]);
    render(<MemoryRouter><EcommerceVideoLibrary assetKind="final_output" /></MemoryRouter>);
    await screen.findByRole('heading', { name: finalAsset.filename });
    expect(screen.getByRole('status').textContent).toBe(status === 'validating' ? '校验中' : '校验失败');
    expect(screen.queryByRole('link', { name: '精修商品成片' })).toBeNull();
    expect(screen.queryByText(/分析/)).toBeNull();
  });

  it('商品素材列表只作为替换和补充素材库, 不提供源视频精修入口', async () => {
    vi.mocked(ecommerceVideoLibraryApi.list).mockResolvedValue([asset]);
    render(<MemoryRouter><EcommerceVideoLibrary assetKind="material" /></MemoryRouter>);
    await screen.findByRole('heading', { name: asset.filename });
    expect(screen.queryByRole('link', { name: /精修/ })).toBeNull();
    expect(ecommerceVideoLibraryApi.list).toHaveBeenCalledWith(1, 'finished', 'material');
  });

  it('商品素材保留分析状态和复用操作', async () => {
    vi.mocked(ecommerceVideoLibraryApi.list).mockResolvedValue([asset]);
    render(<MemoryRouter><EcommerceVideoLibrary assetKind="material" /></MemoryRouter>);
    await screen.findByRole('heading', { name: asset.filename });
    expect(screen.getByRole('combobox', { name: '状态' })).toBeTruthy();
    expect(screen.getByRole('status').textContent).toBe('分析完成');
    fireEvent.change(screen.getByRole('combobox', { name: `复用状态 ${asset.filename}` }), { target: { value: 'blocked' } });
    await waitFor(() => expect(ecommerceVideoLibraryApi.updateReusableStatus).toHaveBeenCalledWith(asset.id, 'blocked'));
    fireEvent.click(screen.getByRole('button', { name: '重新分析' }));
    await waitFor(() => expect(ecommerceVideoLibraryApi.reanalyze).toHaveBeenCalledWith(asset.id));
  });

  it('商品素材详情保留摘要, AI 标签和片段分析', async () => {
    vi.mocked(ecommerceVideoLibraryApi.get).mockResolvedValue({ ...asset, analysis: { summary: '素材分析摘要' } } as VideoLibraryAsset);
    detail('material');
    expect(await screen.findByText('素材分析摘要')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'AI 标签修正' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '视频片段分析' })).toBeTruthy();
    expect(screen.getByText('AI 内容标签')).toBeTruthy();
  });

  it('仅有电商素材权限也可以访问精修, 不要求旧成片权限', async () => {
    render(<MemoryRouter initialEntries={['/video-refinements']}><Layout /></MemoryRouter>);
    expect((await screen.findByRole('link', { name: '视频精修' })).getAttribute('href')).toBe('/video-refinements');
    expect(screen.getByRole('link', { name: '商品成片库' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: '成片库' })).toBeNull();
  });

  it('只有旧成片库权限时不显示商品精修入口', async () => {
    vi.mocked(authApi.me).mockResolvedValue({ role: 'employee', is_platform_admin: false, permissions: ['final_videos.access'] } as Awaited<ReturnType<typeof authApi.me>>);
    render(<MemoryRouter><Layout /></MemoryRouter>);
    await screen.findByRole('link', { name: '成片库' });
    expect(screen.queryByRole('link', { name: '视频精修' })).toBeNull();
  });
});
