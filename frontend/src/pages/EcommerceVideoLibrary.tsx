import { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, Eye, Loader2, RefreshCw, Upload } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { ecommerceProductApi, ecommerceVideoLibraryApi, ecommerceVideoTaxonomyApi, mediaUrl } from '../api/client';
import EcommerceVideoUploadDialog from '../components/EcommerceVideoUploadDialog';
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
const finalUploadStatus: Record<string, { label: string; className: string }> = {
  validating: { label: '校验中', className: 'bg-sky-600 text-white' },
  validation_failed: { label: '校验失败', className: 'bg-red-600 text-white' },
};

export default function EcommerceVideoLibrary({ assetKind }: { assetKind: AssetKind }) {
  const isFinal = assetKind === 'final_output';
  const title = isFinal ? '商品成片库' : '商品素材库';
  const basePath = isFinal ? '/ecommerce-final-videos' : '/ecommerce-video-assets';
  const navigate = useNavigate();
  const [products, setProducts] = useState<Product[]>([]);
  const [productId, setProductId] = useState('');
  const [assets, setAssets] = useState<VideoLibraryAsset[]>([]);
  const [catalog, setCatalog] = useState<VideoTaxonomyCatalog | null>(null);
  const [filter, setFilter] = useState<VideoTaxonomyFilterValue>(emptyFilter);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [status, setStatus] = useState('all');
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
    if (!isFinal && status !== 'all' && asset.status !== status && asset.reusable_status !== status) return false;
    const searchContent = isFinal
      ? `${asset.filename} ${asset.taxonomy_tags.map(tag => tag.name).join(' ')}`
      : `${asset.filename} ${asset.analysis?.summary || ''} ${asset.effective_tags.join(' ')}`;
    if (search && !searchContent.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  }), [assets, catalog, filter, isFinal, search, status]);

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

  const assetStatus = isFinal ? finalUploadStatus : analysisStatus;

  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header><h1 className="font-display text-4xl">{title}</h1><p className="mt-2 text-sm text-[var(--muted)]">{isFinal ? '保存, 查看和下载电商商品成片, 精修结果归档于此.' : '管理电商商品视频素材, 分析结果, 分类标签和复用状态, 为成片精修提供替换和补充素材.'}</p></header>
    {error && <div className="border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">{error}</div>}
    <section className="panel flex flex-wrap items-end gap-3 p-4">
      <label className="field min-w-56 flex-1"><span>商品</span><select className="input-select" value={productId} onChange={event => { setProductId(event.target.value); setSelectedIds([]); }}><option value="">请选择商品</option>{products.map(product => <option key={product.id} value={product.id}>{product.name}</option>)}</select></label>
      <label className="field min-w-48"><span>搜索</span><input className="input" value={search} onChange={event => setSearch(event.target.value)} placeholder={isFinal ? '文件名或标签' : '文件名、摘要或标签'} /></label>
      {!isFinal && <label className="field min-w-40"><span>状态</span><select className="input-select" value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option><option value="uploaded">未分析</option><option value="validating">校验中</option><option value="queued_for_analysis">等待分析</option><option value="analyzing">分析中</option><option value="analyzed">分析完成</option><option value="analysis_failed">分析失败</option><option value="review_required">待确认复用</option><option value="reusable">可复用</option><option value="excluded">已排除</option><option value="blocked">禁止复用</option></select></label>}
      <button className="btn-primary h-[42px]" disabled={!productId || !catalog} onClick={() => setUploadOpen(true)}><Upload size={15} />上传视频</button>
    </section>
    {!products.length && <section className="panel p-8 text-center text-sm">请先创建电商商品。<Link className="ml-2 underline" to="/ecommerce-products/new">创建商品</Link></section>}
    {catalog && <VideoTaxonomyFilters objectType={assetKind} catalog={catalog} value={filter} onChange={setFilter} onRefresh={refreshCatalog} showPresets={false} />}
    {catalog && <VideoBatchTaxonomyBar catalog={catalog} selectedIds={selectedIds} onClear={() => setSelectedIds([])} onDone={async () => { await refreshAssets(); setSelectedIds([]); }} />}
    <div className="flex items-center justify-between"><span className="text-sm text-[var(--muted)]">共 {visible.length} 条视频</span><button className="btn-secondary" onClick={() => refreshAssets().catch(() => setError('刷新失败'))}><RefreshCw size={14} />刷新</button></div>
    {busy ? <div className="grid h-32 place-items-center"><Loader2 className="animate-spin" /></div> : <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{visible.map(asset => <article key={asset.id} className="panel overflow-hidden">
      <div className="relative"><video className="aspect-video w-full bg-black object-contain" src={mediaUrl(asset.object_url)} controls preload="metadata" />{(!isFinal || finalUploadStatus[asset.status]) && <span role="status" aria-live="polite" className={`pointer-events-none absolute right-3 top-3 inline-flex items-center gap-1 px-2 py-1 text-[11px] font-medium shadow ${assetStatus[asset.status]?.className || 'bg-black/75 text-white'}`}>{['validating', 'queued_for_analysis', 'analyzing'].includes(asset.status) && <Loader2 size={12} className="animate-spin" />}{assetStatus[asset.status]?.label || asset.status}</span>}</div>
      <div className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-2"><div className="min-w-0"><h2 className="truncate font-medium" title={asset.filename}>{asset.filename}</h2><p className="mt-1 text-xs text-[var(--muted)]">{asset.duration_seconds.toFixed(1)} 秒 · {asset.width} × {asset.height}</p></div><input type="checkbox" aria-label={`选择 ${asset.filename}`} checked={selectedIds.includes(asset.id)} onChange={() => setSelectedIds(current => current.includes(asset.id) ? current.filter(id => id !== asset.id) : [...current, asset.id])} /></div>
        <VideoTaxonomySummary asset={asset} compact showAiTags={!isFinal} />
        {!isFinal && <><p className="min-h-10 text-sm">{asset.analysis?.summary || '等待 AI 分析'}</p>
        <div className="flex flex-wrap gap-2"><select className="input-select flex-1" aria-label={`复用状态 ${asset.filename}`} value={asset.reusable_status} onChange={event => updateStatus(asset, event.target.value as VideoReusableStatus)}><option value="review_required">待确认</option><option value="reusable">可复用</option><option value="excluded">排除</option><option value="blocked">禁止复用</option></select><button className="btn-secondary" onClick={() => reanalyze(asset)} disabled={['validating', 'queued_for_analysis', 'analyzing'].includes(asset.status) || reanalyzingId !== null}><RefreshCw size={14} />{asset.status === 'uploaded' ? '开始分析' : asset.status === 'analyzing' ? '分析中' : asset.status === 'queued_for_analysis' ? '等待分析' : '重新分析'}</button></div></>}
        {isFinal && asset.status === 'validation_failed' && <p className="text-sm text-red-700">文件校验失败, 请重新上传有效视频.</p>}
        <div className="flex gap-2"><button className="btn-secondary flex-1" onClick={() => navigate(`${basePath}/${asset.id}`)}><Eye size={14} />查看详情</button><a className="btn-secondary flex-1" href={mediaUrl(asset.object_url)} download><Download size={14} />下载</a></div>
      </div>
    </article>)}</section>}
    {!busy && !visible.length && !!products.length && <div className="panel p-10 text-center text-sm text-[var(--muted)]">暂无视频</div>}
    {uploadOpen && catalog && productId && <EcommerceVideoUploadDialog libraryType="finished" assetKind={assetKind} productId={Number(productId)} productName={products.find(product => String(product.id) === productId)?.name || ''} catalog={catalog} onClose={() => setUploadOpen(false)} onCatalogRefresh={refreshCatalog} onUploaded={async () => { await refreshAssets(); await refreshCatalog(); }} />}
  </div>;
}
