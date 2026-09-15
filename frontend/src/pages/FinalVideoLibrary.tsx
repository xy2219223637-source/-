import {useEffect,useMemo,useRef,useState} from 'react';
import {CheckSquare2,Download,Film,Loader2,Play,RotateCcw,Square} from 'lucide-react';
import {useNavigate} from 'react-router-dom';
import {mediaUrl,productApi,videoLibraryApi,videoTaxonomyApi} from '../api/client';
import LazyVideo from '../components/LazyVideo';
import {matchesVideoTaxonomy,VideoBatchTaxonomyBar,VideoTaxonomyFilters} from '../components/VideoTaxonomyFilters';
import type {Product,VideoLibraryAsset,VideoTaxonomyCatalog,VideoTaxonomyFilterValue} from '../types';

export default function FinalVideoLibrary(){
  const navigate=useNavigate();
  const [products,setProducts]=useState<Product[]>([]);
  const [productId,setProductId]=useState('');
  const [videos,setVideos]=useState<VideoLibraryAsset[]>([]);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [durationFilter,setDurationFilter]=useState('all');
  const [dateFilter,setDateFilter]=useState('all');
  const [sourceFilter,setSourceFilter]=useState('all');
  const [catalog,setCatalog]=useState<VideoTaxonomyCatalog|null>(null);
  const [taxonomyFilter,setTaxonomyFilter]=useState<VideoTaxonomyFilterValue>({categoryId:null,publicTagIds:[],publicTagMatchMode:'any',personalTagIds:[]});
  const [selectedIds,setSelectedIds]=useState<number[]>([]);
  const loadRequest=useRef(0);

  useEffect(()=>{
    productApi.list().then(rows=>{
      setProducts(rows);setProductId(String(rows[0]?.id||''));
    }).catch(()=>setError('商品列表加载失败'));
    void refreshCatalog();
  },[]);

  useEffect(()=>{
    setSelectedIds([]);
    if(!productId){setVideos([]);return;}
    void loadVideos();
    const refresh=()=>{if(document.visibilityState==='visible')void loadVideos(true)};
    const timer=window.setInterval(refresh,5000);
    document.addEventListener('visibilitychange',refresh);
    return()=>{loadRequest.current+=1;window.clearInterval(timer);document.removeEventListener('visibilitychange',refresh)};
  },[productId]);

  async function refreshCatalog(){
    try{setCatalog(await videoTaxonomyApi.catalog('final_output'))}catch{setError('分类与标签加载失败')}
  }

  async function loadVideos(silent=false){
    if(!productId)return;
    const request=++loadRequest.current;
    if(!silent)setLoading(true);
    try{
      const rows=await videoLibraryApi.list(Number(productId),'finished','final_output');
      if(request===loadRequest.current){setVideos(rows);setSelectedIds(current=>current.filter(id=>rows.some(video=>video.id===id)));setError('')}
    }catch{if(request===loadRequest.current&&!silent)setError('成片库加载失败')}
    finally{if(request===loadRequest.current&&!silent)setLoading(false)}
  }

  function applyPreset(filters:Record<string,unknown>){
    if(typeof filters.duration_filter==='string')setDurationFilter(filters.duration_filter);
    if(typeof filters.date_filter==='string')setDateFilter(filters.date_filter);
    if(typeof filters.source_filter==='string')setSourceFilter(filters.source_filter);
    if(typeof filters.product_id==='number'&&products.some(product=>product.id===filters.product_id))setProductId(String(filters.product_id));
  }

  const filteredVideos=useMemo(()=>videos.filter(video=>{
    if(catalog&&!matchesVideoTaxonomy(video,catalog,taxonomyFilter))return false;
    if(sourceFilter!=='all'&&video.source!==sourceFilter)return false;
    const durationMatches=durationFilter==='all'
      ||(durationFilter==='under-15'&&video.duration_seconds<=15)
      ||(durationFilter==='15-30'&&video.duration_seconds>15&&video.duration_seconds<=30)
      ||(durationFilter==='30-60'&&video.duration_seconds>30&&video.duration_seconds<=60)
      ||(durationFilter==='over-60'&&video.duration_seconds>60);
    if(!durationMatches)return false;
    if(dateFilter==='all')return true;
    const createdAt=new Date(video.created_at);if(Number.isNaN(createdAt.getTime()))return false;
    const cutoff=new Date();
    if(dateFilter==='today')cutoff.setHours(0,0,0,0);
    else cutoff.setDate(cutoff.getDate()-Number(dateFilter));
    return createdAt>=cutoff;
  }),[videos,catalog,taxonomyFilter,durationFilter,dateFilter,sourceFilter]);
  const totalMinutes=useMemo(()=>filteredVideos.reduce((sum,item)=>sum+item.duration_seconds,0)/60,[filteredVideos]);
  const filtersActive=durationFilter!=='all'||dateFilter!=='all'||sourceFilter!=='all'||taxonomyFilter.categoryId!==null||taxonomyFilter.publicTagIds.length>0||taxonomyFilter.personalTagIds.length>0;
  const resetFilters=()=>{setDurationFilter('all');setDateFilter('all');setSourceFilter('all');setTaxonomyFilter({categoryId:null,publicTagIds:[],publicTagMatchMode:'any',personalTagIds:[]})};
  const currentProduct=products.find(product=>String(product.id)===productId);
  const formatDuration=(seconds:number)=>{const total=Math.round(seconds);return`${Math.floor(total/60).toString().padStart(2,'0')}:${(total%60).toString().padStart(2,'0')}`};
  const formatCreatedAt=(value:string)=>new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
  const allVisibleSelected=filteredVideos.length>0&&filteredVideos.every(video=>selectedIds.includes(video.id));
  const toggleSelection=(id:number)=>setSelectedIds(current=>current.includes(id)?current.filter(item=>item!==id):[...current,id]);

  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header><div className="mb-2 text-xs tracking-[.18em] text-[var(--muted)]">FINAL VIDEO LIBRARY</div><h1 className="font-display text-4xl">成片库</h1><p className="mt-2 text-sm text-[var(--muted)]">集中保存智能成片和成品混剪输出.</p></header>
    {error&&<div className="border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">{error}</div>}
    <section className="panel flex flex-wrap items-end gap-3 p-4">
      <label className="field min-w-56 flex-1"><span>商品</span><select className="input-select" value={productId} onChange={event=>setProductId(event.target.value)}><option value="">请选择商品</option>{products.map(product=><option key={product.id} value={product.id}>{product.name}{product.brand_name?` - ${product.brand_name}`:''}</option>)}</select></label>
      <label className="field min-w-40"><span>生成来源</span><select className="input-select" value={sourceFilter} onChange={event=>setSourceFilter(event.target.value)}><option value="all">全部来源</option><option value="smart_compose_output">智能成片</option><option value="finished_remix_output">成品混剪</option></select></label>
      <label className="field min-w-40"><span>时长</span><select className="input-select" value={durationFilter} onChange={event=>setDurationFilter(event.target.value)}><option value="all">全部时长</option><option value="under-15">15 秒内</option><option value="15-30">15-30 秒</option><option value="30-60">30-60 秒</option><option value="over-60">60 秒以上</option></select></label>
      <label className="field min-w-40"><span>生成时间</span><select className="input-select" value={dateFilter} onChange={event=>setDateFilter(event.target.value)}><option value="all">全部时间</option><option value="today">今天</option><option value="7">近 7 天</option><option value="30">近 30 天</option></select></label>
      <button className="btn-secondary h-[42px]" onClick={resetFilters} disabled={!filtersActive}><RotateCcw size={15}/>重置</button>
    </section>
    {catalog&&<VideoTaxonomyFilters objectType="final_output" catalog={catalog} value={taxonomyFilter} onChange={setTaxonomyFilter} onRefresh={refreshCatalog} presetFilters={{product_id:Number(productId)||null,source_filter:sourceFilter,duration_filter:durationFilter,date_filter:dateFilter}} onApplyPreset={applyPreset}/>}
    {catalog&&<VideoBatchTaxonomyBar catalog={catalog} selectedIds={selectedIds} onClear={()=>setSelectedIds([])} onDone={async()=>{await Promise.all([loadVideos(true),refreshCatalog()])}}/>}
    <section className="grid grid-cols-3 gap-3">{[[filteredVideos.length,'成片'],[filteredVideos.filter(video=>video.source==='smart_compose_output').length,'智能成片'],[totalMinutes.toFixed(1),'总分钟']].map(([value,label])=><div className="panel p-4" key={label}><div className="font-display text-2xl">{value}</div><div className="mt-1 text-xs text-[var(--muted)]">{label}</div></div>)}</section>
    <section>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><h2 className="text-lg font-medium">完整成片</h2><p className="mt-1 text-xs text-[var(--muted)]">共 {filteredVideos.length} 条结果</p></div><div className="flex items-center gap-2"><button className="btn-secondary" disabled={!filteredVideos.length} onClick={()=>setSelectedIds(allVisibleSelected?[]:filteredVideos.map(video=>video.id))}>{allVisibleSelected?<CheckSquare2 size={15}/>:<Square size={15}/>}选择当前结果</button>{loading&&<Loader2 size={16} className="animate-spin"/>}</div></div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">{filteredVideos.map(video=>{
        const selected=selectedIds.includes(video.id);
        return <article className={`panel overflow-hidden ${selected?'border-black ring-1 ring-black':''}`} key={video.id}>
          <div className="relative"><button className="group relative block aspect-[9/16] w-full overflow-hidden bg-black" onClick={()=>navigate(`/final-videos/${video.id}`)} aria-label={`查看 ${video.filename}`}><LazyVideo className="h-full w-full object-contain pointer-events-none" muted playsInline preload="metadata" src={`${mediaUrl(video.object_url)}#t=0.1`}/><span className="absolute inset-0 grid place-items-center bg-black/10 transition-colors group-hover:bg-black/25"><span className="grid h-12 w-12 place-items-center rounded-full bg-white/90 text-black shadow"><Play size={20} fill="currentColor"/></span></span><span className="absolute bottom-3 right-3 bg-black/75 px-2 py-1 text-xs text-white">{formatDuration(video.duration_seconds)}</span></button><label className="absolute right-3 top-3 grid h-9 w-9 cursor-pointer place-items-center bg-white shadow" aria-label={`选择 ${video.filename}`}><input className="sr-only" type="checkbox" checked={selected} onChange={()=>toggleSelection(video.id)}/>{selected?<CheckSquare2 size={19}/>:<Square size={19}/>}</label></div>
          <div className="p-4"><h3 className="truncate font-medium">{currentProduct?.name||'未命名商品'}</h3><div className="mt-1 truncate text-xs text-[var(--muted)]" title={video.filename}>{video.filename}</div><div className="mt-3 text-xs text-[var(--muted)]">生成于 {formatCreatedAt(video.created_at)}</div>{video.category&&<div className="mt-3 text-xs"><span className="text-[var(--muted)]">分类 </span>{video.category.path.join(' / ')}</div>}{video.taxonomy_tags.length>0&&<div className="mt-3 flex flex-wrap gap-1.5">{video.taxonomy_tags.map(tag=><span key={tag.id} className={`border px-2 py-1 text-[11px] ${tag.scope==='personal'?'border-amber-200 bg-amber-50 text-amber-800':'border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{tag.name}{tag.source==='inherited'?' - 继承':''}</span>)}</div>}<div className="mt-4 flex gap-2"><button className="btn-primary flex-1 justify-center" onClick={()=>navigate(`/final-videos/${video.id}`)}><Play size={15}/>查看详情</button><a className="btn-secondary flex-1 justify-center" href={mediaUrl(video.object_url)} download><Download size={15}/>下载</a></div></div>
        </article>;
      })}{!filteredVideos.length&&!loading&&<div className="panel py-16 text-center text-sm text-[var(--muted)] md:col-span-2 xl:col-span-3"><Film className="mx-auto mb-3"/>{videos.length&&filtersActive?<><div>当前筛选条件下暂无成片</div><button className="btn-secondary mt-4" onClick={resetFilters}><RotateCcw size={15}/>重置筛选</button></>:<>这个商品还没有生成完整成片</>}</div>}</div>
    </section>
  </div>;
}
