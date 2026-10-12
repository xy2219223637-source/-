import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Download, Loader2, Pencil, Plus, RefreshCw, X } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { ecommerceProductApi, ecommerceVideoLibraryApi, ecommerceVideoTaxonomyApi, mediaUrl } from '../api/client';
import { VideoTaxonomyEditor, VideoTaxonomySummary } from '../components/EcommerceVideoTaxonomyEditor';
import type { Product, VideoLibraryAsset, VideoTaxonomyCatalog } from '../types';

export default function EcommerceVideoDetail({ assetKind }: { assetKind: 'material' | 'final_output' }) {
  const id = Number(useParams().id);
  const isFinal = assetKind === 'final_output';
  const basePath = isFinal ? '/ecommerce-final-videos' : '/ecommerce-video-assets';
  const [asset, setAsset] = useState<VideoLibraryAsset | null>(null);
  const [product, setProduct] = useState<Product | null>(null);
  const [catalog, setCatalog] = useState<VideoTaxonomyCatalog | null>(null);
  const [editing, setEditing] = useState(false);
  const [tagInput, setTagInput] = useState('');
  const [tagBusy, setTagBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');

  const refreshAsset = useCallback(async () => {
    const next = await ecommerceVideoLibraryApi.get(id);
    if (next.asset_kind !== assetKind || next.library_type !== 'finished') throw new Error('not-found');
    setAsset(next);
    return next;
  }, [assetKind, id]);
  const refreshCatalog = useCallback(async () => {
    setCatalog(await ecommerceVideoTaxonomyApi.catalog(assetKind));
  }, [assetKind]);

  async function saveTags(manualTags: string[], removedAiTags: string[]) {
    if (!asset) return;
    setTagBusy(true); setActionError('');
    try { setAsset(await ecommerceVideoLibraryApi.updateTags(asset.id, { manual_tags: manualTags, removed_ai_tags: removedAiTags })); }
    catch { setActionError('标签保存失败'); }
    finally { setTagBusy(false); }
  }

  useEffect(() => {
    if (!Number.isInteger(id) || id <= 0) { setError('视频不存在'); setLoading(false); return; }
    Promise.all([refreshAsset(), refreshCatalog()]).then(async ([next]) => {
      setProduct(await ecommerceProductApi.get(next.product_id));
    }).catch(() => setError('视频加载失败')).finally(() => setLoading(false));
  }, [id, refreshAsset, refreshCatalog]);

  if (loading) return <div className="grid h-64 place-items-center"><Loader2 className="animate-spin" /></div>;
  if (!asset || error) return <div className="panel p-10 text-center"><p>{error || '视频不存在'}</p><Link className="btn-secondary mt-4" to={basePath}>返回列表</Link></div>;

  return <div className="mx-auto max-w-5xl space-y-5 animate-fade-in">
    {actionError && <div className="border border-red-200 bg-red-50 p-3 text-sm text-red-700">{actionError}</div>}
    <Link className="inline-flex items-center gap-2 text-sm text-[var(--muted)]" to={basePath}><ArrowLeft size={16} />返回{isFinal ? '商品成品库' : '商品素材库'}</Link>
    <header className="flex flex-wrap items-start justify-between gap-4"><div><h1 className="font-display text-3xl">{asset.filename}</h1><p className="mt-2 text-sm text-[var(--muted)]">{product?.name || '商品'} · {asset.duration_seconds.toFixed(1)} 秒 · {asset.width} × {asset.height}</p></div><a className="btn-primary" href={mediaUrl(asset.object_url)} download><Download size={15} />下载视频</a></header>
    <section className="panel overflow-hidden"><video className="max-h-[70vh] w-full bg-black object-contain" src={mediaUrl(asset.object_url)} controls preload="metadata" /></section>
    <section className="panel space-y-4 p-5"><div className="flex justify-between"><div><h2 className="font-medium">视频资料</h2><p className="mt-1 text-xs text-[var(--muted)]">{isFinal ? `入库于 ${new Date(asset.created_at).toLocaleString('zh-CN')} · 来源：${asset.source}` : `状态：${asset.status} · 来源：${asset.source}`}</p></div><button className="btn-secondary" onClick={() => { refreshAsset().catch(() => setError('刷新失败')); refreshCatalog().catch(() => setError('标签加载失败')); }}><RefreshCw size={14} />刷新</button></div>
      {!isFinal && <><p className="text-sm">{asset.analysis?.summary || '暂无分析摘要'}</p>{asset.analysis?.error_message && <p className="text-sm text-red-700">{asset.analysis.error_message}</p>}</>}
      {isFinal && asset.status === 'validating' && <p className="text-sm text-sky-700">视频信息校验中，完成后会自动更新。</p>}
      {isFinal && asset.status === 'analysis_failed' && <p className="text-sm text-red-700">视频校验失败：{asset.analysis?.error_message || '请检查视频文件'}</p>}
      <div className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4"><div>原声：{asset.has_audio ? '有' : '无'}</div>{!isFinal && <><div>烧录字幕：{asset.burned_subtitles ? '有' : '无'}</div><div>背景音乐：{asset.has_background_music ? '有' : '无'}</div></>}<div>帧率：{asset.fps}</div></div>
    </section>
    <section className="panel p-5"><VideoTaxonomySummary asset={asset} showAiTags={!isFinal} onEdit={() => setEditing(true)} />
      {!isFinal && <>
      <div className="mt-5 border-t border-[var(--border)] pt-4"><h3 className="text-sm font-medium">AI 标签修正</h3><div className="mt-3 flex flex-wrap gap-2">{asset.effective_tags.map(tag => <button className="inline-flex items-center gap-1 rounded bg-[var(--bg-elevated)] px-2 py-1 text-xs" key={tag} disabled={tagBusy} onClick={() => saveTags(asset.manual_tags.filter(item => item !== tag), asset.ai_tags.includes(tag) ? [...asset.removed_ai_tags, tag] : asset.removed_ai_tags)}>{tag}<X size={11} /></button>)}</div>
        {!!asset.removed_ai_tags.length && <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--muted)]">已排除：{asset.removed_ai_tags.map(tag => <button className="underline" key={tag} disabled={tagBusy} onClick={() => saveTags(asset.manual_tags, asset.removed_ai_tags.filter(item => item !== tag))}>{tag}（恢复）</button>)}</div>}
        <div className="mt-3 flex gap-2"><input className="input flex-1" value={tagInput} onChange={event => setTagInput(event.target.value)} placeholder="输入标签，多个用逗号分隔" /><button className="btn-secondary" disabled={tagBusy || !tagInput.trim()} onClick={async () => { const tags = tagInput.split(/[,，\n]/).map(item => item.trim()).filter(Boolean); await saveTags([...asset.manual_tags, ...tags], asset.removed_ai_tags); setTagInput(''); }}><Plus size={14} />添加</button></div>
      </div>
      </>}
    </section>
    {!isFinal && <section className="panel p-5"><h2 className="font-medium">视频片段分析</h2><div className="mt-4 space-y-3">{asset.highlights.length ? asset.highlights.map(item => <div key={item.highlight_id} className="border border-[var(--border)] p-3"><b className="text-sm">{item.start.toFixed(1)}–{item.end.toFixed(1)} 秒 · {item.content_role}</b><p className="mt-1 text-sm">{item.description}</p><p className="mt-1 text-xs text-[var(--muted)]">{item.speech_text}</p></div>) : <p className="text-sm text-[var(--muted)]">暂无片段分析</p>}</div></section>}
    {catalog && <button className="btn-secondary" onClick={() => setEditing(true)}><Pencil size={14} />编辑分类和标签</button>}
    {editing && catalog && <VideoTaxonomyEditor asset={asset} catalog={catalog} objectType={assetKind} onClose={() => setEditing(false)} onSaved={async () => { await refreshAsset(); }} onCatalogRefresh={refreshCatalog} />}
  </div>;
}
