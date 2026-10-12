import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckSquare2, Download, Eye, Film, Loader2, Play, RefreshCw, RotateCcw, Square, Upload } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { ecommerceProductApi, ecommerceVideoLibraryApi, ecommerceVideoTaxonomyApi, mediaUrl } from '../api/client';
import EcommerceVideoUploadDialog from '../components/EcommerceVideoUploadDialog';
import LazyVideo from '../components/LazyVideo';
import { VideoTaxonomySummary } from '../components/EcommerceVideoTaxonomyEditor';
import { matchesVideoTaxonomy, VideoBatchTaxonomyBar, VideoTaxonomyFilters } from '../components/EcommerceVideoTaxonomyFilters';
import type { Product, VideoLibraryAsset, VideoReusableStatus, VideoTaxonomyCatalog, VideoTaxonomyFilterValue } from '../types';

type AssetKind = 'material' | 'final_output';
const emptyFilter: VideoTaxonomyFilterValue = { categoryId: null, publicTagIds: [], publicTagMatchMode: 'any', personalTagIds: [] };
const analysisStatus: Record<string, { label: string; className: string }> = {
  uploaded: { label: '未分析', className: 'bg-gray-700 text-white' },
  validating: { label: '校验中', className: 'bg-sky-600 text-white' },
  queued_for_analysis: { label: '等待分析', className: 'bg-amber-400 text-black' },
  analyzing: { label: '分析中', className: 'bg-blue-600 text-white' },
  analyzed: { label: '分析完成', className: 'bg-emerald-600 text-white' },
  analysis_failed: { label: '分析失败', className: 'bg-red-600 text-white' },
};

export default function EcommerceVideoLibrary({ assetKind }: { assetKind: AssetKind }) {
  const isFinal = assetKind === 'final_output';
  const title = isFinal ? '商品成品库' : '商品素材库';
  const basePath = isFinal ? '/ecommerce-final-videos' : '/ecommerce-video-assets';
  const navigate = useNavigate();
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useState('');
  const [assets, setAssets] = useState<VideoLibraryAsset[]>([]);
  const [catalog, setCatalog] = useState<VideoTaxonomyCatalog | null>(null);
  const [filter, setFilter] = useState<VideoTaxonomyFilterValue>(emptyFilter);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [status, setStatus] = useState('all');
  const [durationFilter, setDurationFilter] = useState('all');
  const [dateFilter, setDateFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [uploadOpen, setUploadOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reanalyzingId, setReanalyzingId] = useState<number | null>(null);
  const [error, setError] = useState('');

  const refreshCatalog = useCallback(async () => {
    setCatalog(await ecommerceVideoTaxonomyApi.catalog(isFinal ? 'final_output' : 'material'));
  }, [isFinal]);
  const refreshAssets = useCallback(async () => {
    if (!productId) { setAssets([]); return; }
    setAssets(await ecommerceVideoLibraryApi.list(Number(productId), 'finished', assetKind));
  }, [assetKind, productId]);

  useEffect(() => {
    ecommerceProductApi.list().then(rows => {
      setProducts(rows);
      setProductId(current => rows.some(row => String(row.id) === current) ? current : String(rows[0]?.id || ''));
    }).catch(() => setError('商品列表加载失败'));
    refreshCatalog().catch(() => setError('分类与标签加载失败'));
  }, [refreshCatalog]);

  useEffect(() => {
    setBusy(true);
    refreshAssets().catch(() => setError('视频列表加载失败')).finally(() => setBusy(false));
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') refreshAssets().catch(() => {}); }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshAssets]);

  const visible = useMemo(() => assets.filter(asset => {
    if (catalog && !matchesVideoTaxonomy(asset, catalog, filter)) return false;
    if (isFinal) {
      if (durationFilter !== 'all' && !(durationFilter === 'under-15' && asset.duration_seconds <= 15)
        && !(durationFilter === '15-30' && asset.duration_seconds > 15 && asset.duration_seconds <= 30)
        && !(durationFilter === '30-60' && asset.duration_seconds > 30 && asset.duration_seconds <= 60)
        && !(durationFilter === 'over-60' && asset.duration_seconds > 60)) return false;
      if (dateFilter !== 'all') {
        const created = new Date(asset.created_at);
        const cutoff = new Date();
        if (dateFilter === 'today') cutoff.setHours(0, 0, 0, 0);
        else cutoff.setDate(cutoff.getDate() - Number(dateFilter));
        if (created < cutoff) return false;
      }
    } else if (status !== 'all' && asset.status !== status && asset.reusable_status !== status) return false;
    if (search && !`${asset.filename} ${isFinal ? '' : `${asset.analysis?.summary || ''} ${asset.effective_tags.join(' ')}`}`.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  }), [assets, catalog, dateFilter, durationFilter, filter, isFinal, search, status]);

  const totalMinutes = useMemo(() => visible.reduce((sum, asset) => sum + asset.duration_seconds, 0) / 60, [visible]);
  const allVisibleSelected = visible.length > 0 && visible.every(asset => selectedIds.includes(asset.id));
  const resetFinalFilters = () => { setSearch(''); setDurationFilter('all'); setDateFilter('all'); setFilter(emptyFilter); };
  const formatDuration = (seconds: number) => { const total = Math.round(seconds); return `${Math.floor(total / 60).toString().padStart(2, '0')}:${(total % 60).toString().padStart(2, '0')}`; };

  async function updateStatus(asset: VideoLibraryAsset, next: VideoReusableStatus) {
    try { await ecommerceVideoLibraryApi.updateReusableStatus(asset.id, next); await refreshAssets(); }
    catch { setError('复用状态更新失败'); }
  }

  async function reanalyze(asset: VideoLibraryAsset) {
    if (['validating', 'queued_for_analysis', 'analyzing'].includes(asset.status) || reanalyzingId !== null) return;
    setReanalyzingId(asset.id); setError('');
    try { await ecommerceVideoLibraryApi.reanalyze(asset.id); await refreshAssets(); }
    catch (reason) {
      const detail = (reason as {response?: {data?: {detail?: unknown}}})?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : '重新分析失败');
    } finally { setReanalyzingId(null); }
  }

  if (isFinal) return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header><div className="mb-2 text-xs tracking-[.18em] text-[var(--muted)]">FINAL VIDEO LIBRARY</div><h1 className="font-display text-4xl">商品成品库</h1><p className="mt-2 text-sm text-[var(--muted)]">集中保存、查看和下载商品成品视频。</p></header>
    {error && <div className="border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">{error}</div>}
    <section className="panel flex flex-wrap items-end gap-3 p-4">
      <label className="field min-w-56 flex-1"><span>商品</span><select className="input-select" value={productId} onChange={event => { setProductId(event.target.value); setSelectedIds([]); }}><option value="">请选择商品</option>{products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label>
      <label className="field min-w-48"><span>搜索</span><input className="input" value={search} onChange={event => setSearch(event.target.value)} placeholder="文件名" /></label>
      <label className="field min-w-40"><span>时长</span><select className="input-select" value={durationFilter} onChange={event => setDurationFilter(event.target.value)}><option value="all">全部时长</option><option value="under-15">15 秒内</option><option value="15-30">15-30 秒</option><option value="30-60">30-60 秒</option><option value="over-60">60 秒以上</option></select></label>
      <label className="field min-w-40"><span>入库时间</span><select className="input-select" value={dateFilter} onChange={event => setDateFilter(event.target.value)}><option value="all">全部时间</option><option value="today">今天</option><option value="7">近 7 天</option><option value="30">近 30 天</option></select></label>
      <button className="btn-secondary h-[42px]" onClick={resetFinalFilters}><RotateCcw size={15} />重置</button>
      <button className="btn-primary h-[42px]" disabled={!productId || !catalog} onClick={() => setUploadOpen(true)}><Upload size={15} />上传视频</button>
    </section>
    {!products.length && <section className="panel p-8 text-center text-sm">请先创建电商商品。<Link className="ml-2 underline" to="/ecommerce-products/new">创建商品</Link></section>}
    {catalog && <VideoTaxonomyFilters objectType="final_output" catalog={catalog} value={filter} onChange={setFilter} onRefresh={refreshCatalog} showPresets={false} />}
    {catalog && <VideoBatchTaxonomyBar catalog={catalog} selectedIds={selectedIds} onClear={() => setSelectedIds([])} onDone={async () => { await refreshAssets(); setSelectedIds([]); }} />}
    <section className="grid grid-cols-2 gap-3">{[[visible.length, '成品视频'], [totalMinutes.toFixed(1), '总分钟']].map(([value, label]) => <div className="panel p-4" key={label}><div className="font-display text-2xl">{value}</div><div className="mt-1 text-xs text-[var(--muted)]">{label}</div></div>)}</section>
    <section><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-medium">完整成片</h2><p className="mt-1 text-xs text-[var(--muted)]">共 {visible.length} 条结果</p></div><div className="flex items-center gap-2"><button className="btn-secondary" disabled={!visible.length} onClick={() => setSelectedIds(allVisibleSelected ? [] : visible.map(asset => asset.id))}>{allVisibleSelected ? <CheckSquare2 size={15} /> : <Square size={15} />}选择当前结果</button><button className="btn-secondary" onClick={() => refreshAssets().catch(() => setError('刷新失败'))}><RefreshCw size={15} />刷新</button>{busy && <Loader2 size={16} className="animate-spin" />}</div></div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{visible.map(asset => <article key={asset.id} className={`panel overflow-hidden ${selectedIds.includes(asset.id) ? 'border-black ring-1 ring-black' : ''}`}>
        <div className="relative"><button className="group relative block aspect-[9/16] w-full overflow-hidden bg-black" onClick={() => navigate(`${basePath}/${asset.id}`)} aria-label={`查看 ${asset.filename}`}><LazyVideo className="pointer-events-none h-full w-full object-contain" muted playsInline preload="metadata" src={`${mediaUrl(asset.object_url)}#t=0.1`} /><span className="absolute inset-0 grid place-items-center bg-black/10 transition-colors group-hover:bg-black/25"><span className="grid h-12 w-12 place-items-center rounded-full bg-white/90 text-black shadow"><Play size={20} fill="currentColor" /></span></span><span className="absolute bottom-3 right-3 bg-black/75 px-2 py-1 text-xs text-white">{formatDuration(asset.duration_seconds)}</span></button>{asset.status === 'validating' && <span className="absolute left-3 top-3 bg-sky-600 px-2 py-1 text-xs text-white">校验中</span>}{asset.status === 'analysis_failed' && <span className="absolute left-3 top-3 bg-red-600 px-2 py-1 text-xs text-white">校验失败</span>}<label className="absolute right-3 top-3 grid h-9 w-9 cursor-pointer place-items-center bg-white shadow" aria-label={`选择 ${asset.filename}`}><input className="sr-only" type="checkbox" checked={selectedIds.includes(asset.id)} onChange={() => setSelectedIds(current => current.includes(asset.id) ? current.filter(id => id !== asset.id) : [...current, asset.id])} />{selectedIds.includes(asset.id) ? <CheckSquare2 size={19} /> : <Square size={19} />}</label></div>
        <div className="p-4"><h3 className="truncate font-medium">{products.find(product => product.id === asset.product_id)?.name || '未命名商品'}</h3><div className="mt-1 truncate text-xs text-[var(--muted)]" title={asset.filename}>{asset.filename}</div><div className="mt-3 text-xs text-[var(--muted)]">入库于 {new Date(asset.created_at).toLocaleString('zh-CN')}</div><div className="mt-3"><VideoTaxonomySummary asset={asset} compact showAiTags={false} /></div><div className="mt-4 flex gap-2"><button className="btn-primary flex-1 justify-center" onClick={() => navigate(`${basePath}/${asset.id}`)}><Play size={15} />查看详情</button><a className="btn-secondary flex-1 justify-center" href={mediaUrl(asset.object_url)} download><Download size={15} />下载</a></div></div>
      </article>)}{!visible.length && !busy && <div className="panel py-16 text-center text-sm text-[var(--muted)] md:col-span-2 xl:col-span-3"><Film className="mx-auto mb-3" />这个商品还没有成品视频</div>}</div>
    </section>
    {uploadOpen && catalog && productId && <EcommerceVideoUploadDialog libraryType="finished" assetKind="final_output" productId={Number(productId)} productName={products.find(product => String(product.id) === productId)?.name || ''} catalog={catalog} onClose={() => setUploadOpen(false)} onCatalogRefresh={refreshCatalog} onUploaded={async () => { await refreshAssets(); await refreshCatalog(); }} />}
  </div>;

  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header><h1 className="font-display text-4xl">{title}</h1><p className="mt-2 text-sm text-[var(--muted)]">{isFinal ? '独立保存、查看和下载电商成品视频。' : '独立管理电商成品素材、分析结果、分类标签和复用状态。'}</p></header>
    {error && <div className="border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">{error}</div>}
    <section className="panel flex flex-wrap items-end gap-3 p-4">
      <label className="field min-w-56 flex-1"><span>商品</span><select className="input-select" value={productId} onChange={event => { setProductId(event.target.value); setSelectedIds([]); }}><option value="">请选择商品</option>{products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label>
      <label className="field min-w-48"><span>搜索</span><input className="input" value={search} onChange={event => setSearch(event.target.value)} placeholder="文件名、摘要或标签" /></label>
      <label className="field min-w-40"><span>状态</span><select className="input-select" value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option><option value="uploaded">未分析</option><option value="validating">校验中</option><option value="queued_for_analysis">等待分析</option><option value="analyzing">分析中</option><option value="analyzed">分析完成</option><option value="analysis_failed">分析失败</option>{!isFinal && <><option value="review_required">待确认复用</option><option value="reusable">可复用</option><option value="excluded">已排除</option><option value="blocked">禁止复用</option></>}</select></label>
      <button className="btn-primary h-[42px]" disabled={!productId || !catalog} onClick={() => setUploadOpen(true)}><Upload size={15} />上传视频</button>
    </section>
    {!products.length && <section className="panel p-8 text-center text-sm">请先创建电商商品。<Link className="ml-2 underline" to="/ecommerce-products/new">创建商品</Link></section>}
    {catalog && <VideoTaxonomyFilters objectType={assetKind} catalog={catalog} value={filter} onChange={setFilter} onRefresh={refreshCatalog} showPresets={false} />}
    {catalog && <VideoBatchTaxonomyBar catalog={catalog} selectedIds={selectedIds} onClear={() => setSelectedIds([])} onDone={async () => { await refreshAssets(); setSelectedIds([]); }} />}
    <div className="flex items-center justify-between"><span className="text-sm text-[var(--muted)]">共 {visible.length} 条视频</span><button className="btn-secondary" onClick={() => refreshAssets().catch(() => setError('刷新失败'))}><RefreshCw size={14} />刷新</button></div>
    {busy ? <div className="grid h-32 place-items-center"><Loader2 className="animate-spin" /></div> : <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{visible.map(asset => <article key={asset.id} className="panel overflow-hidden">
      <div className="relative"><video className="aspect-video w-full bg-black object-contain" src={mediaUrl(asset.object_url)} controls preload="metadata" /><span role="status" aria-live="polite" className={`pointer-events-none absolute right-3 top-3 inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium shadow ${analysisStatus[asset.status]?.className || 'bg-black/75 text-white'}`}>{['validating', 'queued_for_analysis', 'analyzing'].includes(asset.status) && <Loader2 size={12} className="animate-spin" />}{analysisStatus[asset.status]?.label || asset.status}</span></div>
      <div className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-2"><div className="min-w-0"><h2 className="truncate font-medium" title={asset.filename}>{asset.filename}</h2><p className="mt-1 text-xs text-[var(--muted)]">{asset.duration_seconds.toFixed(1)} 秒 · {asset.width} × {asset.height}</p></div><input type="checkbox" aria-label={`选择 ${asset.filename}`} checked={selectedIds.includes(asset.id)} onChange={() => setSelectedIds(current => current.includes(asset.id) ? current.filter(id => id !== asset.id) : [...current, asset.id])} /></div>
        <VideoTaxonomySummary asset={asset} compact />
        <p className="min-h-10 text-sm">{asset.analysis?.summary || '等待 AI 分析'}</p>
        <div className="flex flex-wrap gap-2">{!isFinal && <select className="input-select flex-1" value={asset.reusable_status} onChange={event => updateStatus(asset, event.target.value as VideoReusableStatus)}><option value="review_required">待确认</option><option value="reusable">可复用</option><option value="excluded">排除</option><option value="blocked">禁止复用</option></select>}{(!isFinal || ['uploaded', 'analysis_failed'].includes(asset.status)) && <button className="btn-secondary" onClick={() => reanalyze(asset)} disabled={['validating', 'queued_for_analysis', 'analyzing'].includes(asset.status) || reanalyzingId !== null}><RefreshCw size={14} />{asset.status === 'uploaded' ? '开始分析' : asset.status === 'analyzing' ? '分析中' : asset.status === 'queued_for_analysis' ? '等待分析' : '重新分析'}</button>}</div>
        <div className="flex gap-2"><button className="btn-secondary flex-1" onClick={() => navigate(`${basePath}/${asset.id}`)}><Eye size={14} />查看详情</button><a className="btn-secondary flex-1" href={mediaUrl(asset.object_url)} download><Download size={14} />下载</a></div>
      </div>
    </article>)}</section>}
    {!busy && !visible.length && !!products.length && <div className="panel p-10 text-center text-sm text-[var(--muted)]">暂无视频</div>}
    {uploadOpen && catalog && productId && <EcommerceVideoUploadDialog libraryType="finished" assetKind={assetKind} productId={Number(productId)} productName={products.find(product => String(product.id) === productId)?.name || ''} catalog={catalog} onClose={() => setUploadOpen(false)} onCatalogRefresh={refreshCatalog} onUploaded={async () => { await refreshAssets(); await refreshCatalog(); }} />}
  </div>;
}
