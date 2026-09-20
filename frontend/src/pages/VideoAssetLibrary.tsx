import {useEffect,useMemo,useRef,useState} from 'react';
import {
  Ban,
  CheckCircle2,
  CheckSquare2,
  Eye,
  FileVideo,
  Loader2,
  Pencil,
  RefreshCw,
  Square,
  Tag,
  Upload,
  X,
} from 'lucide-react';
import {useLocation,useNavigate,useSearchParams} from 'react-router-dom';
import {mediaUrl,productApi,videoLibraryApi,videoTaxonomyApi} from '../api/client';
import {matchesVideoTaxonomy,VideoBatchTaxonomyBar,VideoTaxonomyFilters} from '../components/VideoTaxonomyFilters';
import {VideoTaxonomySummary} from '../components/VideoTaxonomyEditor';
import VideoUploadDialog from '../components/VideoUploadDialog';
import type {
  Product,
  VideoLibraryAsset,
  VideoReusableStatus,
  VideoTaxonomyCatalog,
  VideoTaxonomyFilterValue,
} from '../types';

const statusText:Record<string,string>={
  validating:'正在校验视频',queued_for_analysis:'待分析',analyzing:'分析中',analyzed:'分析完成',analysis_failed:'分析失败',
};
const businessStatusText:Record<string,string>={
  processing:'处理中',review:'待确认',reusable:'可混剪',excluded:'已排除',blocked:'禁止复用',failed:'分析失败',
};
type LibraryType='semi_finished'|'finished';
type Props={libraryType:LibraryType;title:string;description:string};

function businessStatus(asset:VideoLibraryAsset){
  if(asset.status==='analysis_failed')return'failed';
  if(['validating','queued_for_analysis','analyzing'].includes(asset.status))return'processing';
  if(asset.reusable_status==='blocked')return'blocked';
  if(asset.excluded||asset.reusable_status==='excluded')return'excluded';
  if(asset.reusable_status==='reusable')return'reusable';
  return'review';
}

function positiveNumber(value:string|null){
  const parsed=Number(value);
  return Number.isInteger(parsed)&&parsed>0?parsed:null;
}

function idList(value:string|null){
  return Array.from(new Set((value||'').split(',').map(item=>positiveNumber(item)).filter((item):item is number=>item!==null)));
}

export default function VideoAssetLibrary({libraryType,title,description}:Props){
  const navigate=useNavigate();
  const location=useLocation();
  const [searchParams,setSearchParams]=useSearchParams();
  const isFinished=libraryType==='finished';
  const [products,setProducts]=useState<Product[]>([]);
  const [productId,setProductId]=useState(()=>searchParams.get('product')||'');
  const [assets,setAssets]=useState<VideoLibraryAsset[]>([]);
  const [busy,setBusy]=useState('');
  const [error,setError]=useState('');
  const [tagFilter,setTagFilter]=useState(()=>searchParams.get('ai_tag')||'全部');
  const [statusFilter,setStatusFilter]=useState(()=>searchParams.get('status')||'all');
  const [durationFilter,setDurationFilter]=useState(()=>searchParams.get('duration')||'all');
  const [uploadOpen,setUploadOpen]=useState(false);
  const [editingId,setEditingId]=useState<number|null>(null);
  const [tagDraft,setTagDraft]=useState('');
  const [catalog,setCatalog]=useState<VideoTaxonomyCatalog|null>(null);
  const [taxonomyFilter,setTaxonomyFilter]=useState<VideoTaxonomyFilterValue>(()=>({
    categoryId:positiveNumber(searchParams.get('category')),
    publicTagIds:idList(searchParams.get('public_tags')),
    publicTagMatchMode:searchParams.get('tag_match')==='all'?'all':'any',
    personalTagIds:idList(searchParams.get('personal_tags')),
  }));
  const [selectedIds,setSelectedIds]=useState<number[]>(()=>idList(searchParams.get('selected')));
  const loadRequest=useRef(0);
  const previousProductId=useRef(productId);

  useEffect(()=>{
    productApi.list().then(rows=>{
      setProducts(rows);
      setProductId(current=>rows.some(product=>String(product.id)===current)?current:String(rows[0]?.id||''));
    }).catch(()=>setError('商品列表加载失败'));
    void refreshCatalog();
  },[]);

  useEffect(()=>{
    setSearchParams(current=>{
      const next=new URLSearchParams(current);
      const setOrDelete=(key:string,value:string,defaultValue='')=>value&&value!==defaultValue?next.set(key,value):next.delete(key);
      setOrDelete('product',productId);
      setOrDelete('status',statusFilter,'all');
      setOrDelete('duration',durationFilter,'all');
      setOrDelete('ai_tag',tagFilter,'全部');
      setOrDelete('category',taxonomyFilter.categoryId?String(taxonomyFilter.categoryId):'');
      setOrDelete('public_tags',taxonomyFilter.publicTagIds.join(','));
      setOrDelete('tag_match',taxonomyFilter.publicTagMatchMode,'any');
      setOrDelete('personal_tags',taxonomyFilter.personalTagIds.join(','));
      setOrDelete('selected',selectedIds.join(','));
      return next;
    },{replace:true});
  },[productId,statusFilter,durationFilter,tagFilter,taxonomyFilter,selectedIds,setSearchParams]);

  useEffect(()=>{
    if(previousProductId.current&&previousProductId.current!==productId)setSelectedIds([]);
    previousProductId.current=productId;
    if(!productId){setAssets([]);return;}
    void load();
    const refresh=()=>{if(document.visibilityState==='visible')void load(true)};
    const timer=window.setInterval(refresh,5000);
    document.addEventListener('visibilitychange',refresh);
    return()=>{loadRequest.current+=1;window.clearInterval(timer);document.removeEventListener('visibilitychange',refresh)};
  },[productId,libraryType]);

  async function refreshCatalog(){
    try{setCatalog(await videoTaxonomyApi.catalog('material'))}catch{setError('分类与标签加载失败')}
  }

  async function load(silent=false){
    if(!productId)return;
    const request=++loadRequest.current;
    if(!silent)setBusy('load');
    try{
      const rows=await videoLibraryApi.list(Number(productId),libraryType);
      if(request!==loadRequest.current)return;
      setAssets(rows);
      setSelectedIds(current=>current.filter(id=>rows.some(asset=>asset.id===id)));
      setError('');
    }catch{if(request===loadRequest.current&&!silent)setError('素材库加载失败')}
    finally{if(request===loadRequest.current&&!silent)setBusy('')}
  }

  async function exclude(asset:VideoLibraryAsset){
    setBusy(String(asset.id));
    try{await videoLibraryApi.exclude(asset.id,!asset.excluded);await load(true)}finally{setBusy('')}
  }

  async function setReusable(asset:VideoLibraryAsset,reusable_status:VideoReusableStatus){
    setBusy(`reuse-${asset.id}`);
    try{await videoLibraryApi.updateReusableStatus(asset.id,reusable_status);await load(true)}
    catch{setError('复用状态更新失败')}
    finally{setBusy('')}
  }

  async function reanalyze(asset:VideoLibraryAsset){
    setBusy(String(asset.id));
    try{await videoLibraryApi.reanalyze(asset.id);await load(true)}finally{setBusy('')}
  }

  async function saveLegacyTags(asset:VideoLibraryAsset,manualTags:string[],removedAiTags:string[]){
    setBusy(`tags-${asset.id}`);
    try{
      const updated=await videoLibraryApi.updateTags(asset.id,{manual_tags:manualTags,removed_ai_tags:removedAiTags});
      setAssets(current=>current.map(item=>item.id===asset.id?updated:item));
    }catch{setError('AI 标签修正保存失败')}
    finally{setBusy('')}
  }

  function removeLegacyTag(asset:VideoLibraryAsset,tag:string){
    if(asset.manual_tags.includes(tag))void saveLegacyTags(asset,asset.manual_tags.filter(value=>value!==tag),asset.removed_ai_tags);
    else void saveLegacyTags(asset,asset.manual_tags,[...asset.removed_ai_tags,tag]);
  }

  function restoreLegacyTag(asset:VideoLibraryAsset,tag:string){
    void saveLegacyTags(asset,asset.manual_tags,asset.removed_ai_tags.filter(value=>value!==tag));
  }

  function addManualTags(asset:VideoLibraryAsset){
    const additions=tagDraft.split(/[,，\n]/).map(value=>value.trim()).filter(Boolean);
    if(!additions.length)return;
    void saveLegacyTags(asset,Array.from(new Set([...asset.manual_tags,...additions])),asset.removed_ai_tags);
    setTagDraft('');
  }

  function applyPreset(filters:Record<string,unknown>){
    if(typeof filters.status_filter==='string')setStatusFilter(filters.status_filter);
    if(typeof filters.duration_filter==='string')setDurationFilter(filters.duration_filter);
    if(typeof filters.ai_tag_filter==='string')setTagFilter(filters.ai_tag_filter);
    if(typeof filters.product_id==='number'&&products.some(product=>product.id===filters.product_id))setProductId(String(filters.product_id));
  }

  const allTags=useMemo(()=>Array.from(new Set(assets.flatMap(asset=>asset.effective_tags||[]))).sort(),[assets]);
  const stats=useMemo(()=>({
    total:assets.length,
    analyzed:assets.filter(asset=>asset.status==='analyzed').length,
    usable:assets.filter(asset=>asset.status==='analyzed'&&!asset.excluded&&(!isFinished||asset.reusable_status==='reusable')).length,
    review:assets.filter(asset=>businessStatus(asset)==='review').length,
    processing:assets.filter(asset=>businessStatus(asset)==='processing').length,
    excluded:assets.filter(asset=>businessStatus(asset)==='excluded').length,
    failed:assets.filter(asset=>asset.status==='analysis_failed').length,
    duration:assets.reduce((total,asset)=>total+asset.duration_seconds,0),
  }),[assets,isFinished]);
  const analysisStats=useMemo(()=>{
    const active=assets.filter(asset=>!asset.excluded);
    const completed=active.filter(asset=>asset.status==='analyzed').length;
    const failed=active.filter(asset=>asset.status==='analysis_failed').length;
    const analyzing=active.filter(asset=>asset.status==='analyzing').length;
    const queued=active.filter(asset=>asset.status==='queued_for_analysis').length;
    return{total:active.length,completed,failed,analyzing,queued,percent:active.length?Math.round((completed+failed)/active.length*100):0};
  },[assets]);
  const visibleAssets=assets.filter(asset=>{
    if(catalog&&!matchesVideoTaxonomy(asset,catalog,taxonomyFilter))return false;
    if(tagFilter!=='全部'&&!asset.effective_tags.includes(tagFilter))return false;
    if(isFinished&&statusFilter!=='all'&&businessStatus(asset)!==statusFilter)return false;
    if(durationFilter==='under-15'&&asset.duration_seconds>15)return false;
    if(durationFilter==='15-30'&&(asset.duration_seconds<=15||asset.duration_seconds>30))return false;
    if(durationFilter==='30-60'&&(asset.duration_seconds<=30||asset.duration_seconds>60))return false;
    if(durationFilter==='over-60'&&asset.duration_seconds<=60)return false;
    return true;
  });
  const allVisibleSelected=visibleAssets.length>0&&visibleAssets.every(asset=>selectedIds.includes(asset.id));
  const toggleSelection=(assetId:number)=>setSelectedIds(current=>current.includes(assetId)?current.filter(id=>id!==assetId):[...current,assetId]);
  const openDetail=(assetId:number,suffix='')=>navigate(`/${libraryType==='finished'?'finished-video-assets':'semi-finished-assets'}/${assetId}${suffix}`,{
    state:{libraryReturn:{url:location.pathname+location.search,scroll:window.scrollY}},
  });

  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header><div className="mb-2 text-xs tracking-[.18em] text-[var(--muted)]">{isFinished?'FINISHED VIDEO LIBRARY':'SEMI-FINISHED MATERIAL LIBRARY'}</div><h1 className="font-display text-4xl">{title}</h1><p className="mt-2 text-sm text-[var(--muted)]">{description}</p></header>
    {error&&<div className="border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">{error}</div>}

    <section className="panel p-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="field min-w-56 flex-1"><span>商品</span><select className="input-select" value={productId} onChange={event=>setProductId(event.target.value)}><option value="">请选择商品</option>{products.map(product=><option key={product.id} value={product.id}>{product.name}{product.brand_name?` - ${product.brand_name}`:''}</option>)}</select></label>
        {isFinished&&<label className="field min-w-36"><span>状态</span><select className="input-select" value={statusFilter} onChange={event=>setStatusFilter(event.target.value)}><option value="all">全部状态</option><option value="processing">处理中</option><option value="review">待确认</option><option value="reusable">可混剪</option><option value="excluded">已排除</option><option value="blocked">禁止复用</option><option value="failed">分析失败</option></select></label>}
        <label className="field min-w-36"><span>时长</span><select className="input-select" value={durationFilter} onChange={event=>setDurationFilter(event.target.value)}><option value="all">全部时长</option><option value="under-15">15 秒内</option><option value="15-30">15-30 秒</option><option value="30-60">30-60 秒</option><option value="over-60">60 秒以上</option></select></label>
        <label className="field min-w-36"><span>AI 分析标签</span><select className="input-select" value={tagFilter} onChange={event=>setTagFilter(event.target.value)}><option value="全部">全部标签</option>{allTags.map(tag=><option key={tag} value={tag}>{tag}</option>)}</select></label>
        <button className="btn-primary h-[42px]" disabled={!productId||!catalog} onClick={()=>setUploadOpen(true)}><Upload size={15}/>上传视频</button>
      </div>
    </section>

    {uploadOpen&&catalog&&productId&&<VideoUploadDialog libraryType={libraryType} productId={Number(productId)} productName={products.find(product=>product.id===Number(productId))?.name||'未命名商品'} catalog={catalog} onClose={()=>setUploadOpen(false)} onCatalogRefresh={refreshCatalog} onUploaded={async()=>{await Promise.all([load(true),refreshCatalog()])}}/>}

    {catalog&&<VideoTaxonomyFilters objectType="material" catalog={catalog} value={taxonomyFilter} onChange={setTaxonomyFilter} onRefresh={refreshCatalog} presetFilters={{product_id:Number(productId)||null,library_type:libraryType,status_filter:statusFilter,duration_filter:durationFilter,ai_tag_filter:tagFilter}} onApplyPreset={applyPreset}/>}
    {catalog&&<VideoBatchTaxonomyBar catalog={catalog} selectedIds={selectedIds} onClear={()=>setSelectedIds([])} onDone={()=>load(true)}/>}

    <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">{(isFinished?[
      [stats.total,'全部素材','all'],[stats.processing,'处理中','processing'],[stats.review,'待确认','review'],[stats.usable,'可混剪','reusable'],[stats.excluded,'已排除','excluded'],
    ]:[
      [stats.total,'素材','all'],[stats.analyzed,'分析完成','all'],[stats.usable,'可用于成片','all'],[stats.failed,'失败','all'],[(stats.duration/60).toFixed(1),'总分钟','all'],
    ]).map(([value,label,status])=>isFinished?<button className={`panel p-4 text-left transition-colors hover:border-black ${statusFilter===status?'border-black ring-1 ring-black':''}`} key={label} onClick={()=>setStatusFilter(String(status))}><div className="font-display text-2xl">{value}</div><div className="mt-1 text-xs text-[var(--muted)]">{label}</div></button>:<div className="panel p-4" key={label}><div className="font-display text-2xl">{value}</div><div className="mt-1 text-xs text-[var(--muted)]">{label}</div></div>)}</section>

    {analysisStats.total>0&&analysisStats.completed+analysisStats.failed<analysisStats.total&&<section className="panel border-blue-200 bg-blue-50 p-5"><div className="flex flex-wrap justify-between gap-2"><div className="flex items-center gap-2 font-medium"><Loader2 size={16} className="animate-spin text-blue-600"/>AI 正在分析素材</div><b className="text-blue-700">{analysisStats.completed+analysisStats.failed} / {analysisStats.total} 条 - {analysisStats.percent}%</b></div><div className="mt-4 h-2.5 overflow-hidden rounded-full bg-blue-100"><div className="h-full bg-blue-600 transition-[width]" style={{width:`${analysisStats.percent}%`}}/></div><div className="mt-3 flex flex-wrap gap-4 text-xs"><span>模型分析中 {analysisStats.analyzing}</span><span>排队等待 {analysisStats.queued}</span><span>成功 {analysisStats.completed}</span>{analysisStats.failed>0&&<span className="text-red-600">失败 {analysisStats.failed}</span>}</div></section>}

    <section>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-medium">素材列表</h2><p className="mt-1 text-xs text-[var(--muted)]">共 {visibleAssets.length} 条结果</p></div><div className="flex items-center gap-2"><button className="btn-secondary" disabled={!visibleAssets.length} onClick={()=>setSelectedIds(allVisibleSelected?[]:visibleAssets.map(asset=>asset.id))}>{allVisibleSelected?<CheckSquare2 size={15}/>:<Square size={15}/>}选择当前结果</button>{busy==='load'&&<Loader2 size={16} className="animate-spin"/>}</div></div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{visibleAssets.map(asset=>{
        const state=businessStatus(asset);
        const usableSeconds=asset.highlights.reduce((total,item)=>total+Math.max(0,item.end-item.start),0);
        const selected=selectedIds.includes(asset.id);
        return <article key={asset.id} className={`panel overflow-hidden ${selected?'border-black ring-1 ring-black':''} ${state==='excluded'||state==='blocked'?'opacity-60':''}`}>
          <div className="relative"><video className="aspect-video w-full bg-black object-cover" src={mediaUrl(asset.object_url)} controls preload="metadata"/><label className="absolute right-3 top-3 grid h-9 w-9 cursor-pointer place-items-center bg-white shadow" aria-label={`选择 ${asset.filename}`}><input className="sr-only" type="checkbox" checked={selected} onChange={()=>toggleSelection(asset.id)}/>{selected?<CheckSquare2 size={19}/>:<Square size={19}/>}</label>{isFinished&&<span className={`absolute left-3 top-3 px-2 py-1 text-[11px] font-medium shadow ${state==='reusable'?'bg-emerald-600 text-white':state==='review'?'bg-amber-400 text-black':state==='failed'||state==='blocked'?'bg-red-600 text-white':state==='processing'?'bg-blue-600 text-white':'bg-black/75 text-white'}`}>{businessStatusText[state]}</span>}</div>
          <div className="p-4"><div className="flex justify-between gap-2"><b className="truncate" title={asset.filename}>{asset.filename}</b>{!isFinished&&<span className={`shrink-0 text-xs ${asset.status==='analyzed'?'text-emerald-700':asset.status==='analysis_failed'?'text-red-600':'text-blue-600'}`}>{statusText[asset.status]||asset.status}</span>}</div><div className="mt-2 text-xs text-[var(--muted)]">{asset.duration_seconds.toFixed(1)} 秒 - {asset.width} x {asset.height}{asset.burned_subtitles?' - 烧录字幕':''}{asset.has_background_music?' - 原 BGM':''}</div>
            <button className="mt-3 text-xs underline underline-offset-4" onClick={()=>openDetail(asset.id,'?tab=used')}>引用次数 {asset.reference_count??0}</button>
            <div className="mt-3"><VideoTaxonomySummary asset={asset} compact/></div>
            {(asset.status==='queued_for_analysis'||asset.status==='analyzing')&&<div className="mt-3 border border-blue-100 bg-blue-50 p-3"><div className="flex items-center gap-2 text-xs text-blue-800"><Loader2 size={13} className="animate-spin"/>{asset.status==='queued_for_analysis'?'等待分析':'正在分析视频内容'}</div></div>}
            <p className="mt-3 min-h-10 text-sm leading-5">{asset.analysis?.summary||'等待 AI 生成内容摘要'}</p>
            {isFinished&&asset.status==='analyzed'&&<div className="mt-3 bg-[#f6f5f2] px-3 py-2 text-xs"><b>{asset.highlights.length} 个可用片段</b><span className="ml-2 text-[var(--muted)]">合计 {usableSeconds.toFixed(1)} 秒</span></div>}
            <button className="btn-secondary mt-4 w-full justify-center" onClick={()=>openDetail(asset.id)}><Eye size={14}/>查看详情与标签</button>
            {editingId===asset.id&&<div className="mt-4 border border-blue-200 bg-blue-50 p-3"><div className="text-xs font-medium">修正 AI 分析标签</div><div className="mt-2 flex flex-wrap gap-1.5">{asset.effective_tags.map(tag=><button key={tag} onClick={()=>removeLegacyTag(asset,tag)} disabled={!!busy} className="inline-flex items-center gap-1 border border-blue-200 bg-white px-2 py-1 text-[11px]">{tag}<X size={11}/></button>)}</div><div className="mt-3 flex gap-2"><input className="min-w-0 flex-1 border border-blue-200 px-2 py-1.5 text-xs" value={tagDraft} onChange={event=>setTagDraft(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();addManualTags(asset)}}} placeholder="输入标签, 使用逗号分隔"/><button className="btn-secondary" onClick={()=>addManualTags(asset)} disabled={!tagDraft.trim()||!!busy}><Tag size={13}/>添加</button></div>{asset.removed_ai_tags.length>0&&<div className="mt-3 text-xs"><span className="text-[var(--muted)]">已隐藏 </span>{asset.removed_ai_tags.map(tag=><button className="ml-2 underline" key={tag} onClick={()=>restoreLegacyTag(asset,tag)}>{tag}</button>)}</div>}</div>}
            {isFinished?<div className="mt-4 flex flex-wrap gap-2">{state==='review'&&asset.generation_depth<=1&&<><button className="btn-primary" disabled={!!busy} onClick={()=>setReusable(asset,'reusable')}><CheckCircle2 size={14}/>确认可混剪</button><button className="btn-secondary" disabled={!!busy} onClick={()=>setReusable(asset,'excluded')}><Ban size={14}/>排除</button></>}{state==='reusable'&&<button className="btn-secondary" disabled={!!busy} onClick={()=>setReusable(asset,'excluded')}><Ban size={14}/>排除</button>}{state==='excluded'&&asset.generation_depth<=1&&<button className="btn-secondary" disabled={!!busy} onClick={()=>setReusable(asset,'review_required')}><CheckCircle2 size={14}/>恢复待确认</button>}{state==='failed'&&<button className="btn-secondary" onClick={()=>reanalyze(asset)} disabled={!!busy}><RefreshCw size={14}/>重新分析</button>}{asset.status==='analyzed'&&<button className="btn-secondary" onClick={()=>{setEditingId(editingId===asset.id?null:asset.id);setTagDraft('')}}><Pencil size={14}/>AI 标签</button>}</div>:<div className="mt-4 flex justify-between"><div className="flex flex-wrap gap-2"><button className="btn-secondary" onClick={()=>exclude(asset)} disabled={!!busy}>{asset.excluded?<><CheckCircle2 size={14}/>恢复</>:<><Ban size={14}/>排除</>}</button><button className="btn-secondary" onClick={()=>{setEditingId(editingId===asset.id?null:asset.id);setTagDraft('')}}><Pencil size={14}/>AI 标签</button></div>{asset.status==='analysis_failed'&&<button className="btn-secondary" onClick={()=>reanalyze(asset)} disabled={!!busy}><RefreshCw size={14}/>重新分析</button>}</div>}
          </div>
        </article>;
      })}{!visibleAssets.length&&<div className="panel py-16 text-center text-sm text-[var(--muted)] md:col-span-2 xl:col-span-3"><FileVideo className="mx-auto mb-3"/>{assets.length?'当前筛选条件下没有素材':productId?'这个商品还没有视频素材':'请先选择商品'}</div>}</div>
    </section>
  </div>;
}
