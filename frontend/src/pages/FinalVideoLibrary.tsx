import {useEffect,useMemo,useState} from 'react';
import {Download,Film,Loader2,Play,RotateCcw} from 'lucide-react';
import {useNavigate} from 'react-router-dom';
import {mediaUrl,productApi,videoLibraryApi} from '../api/client';
import LazyVideo from '../components/LazyVideo';
import type {Product,VideoLibraryAsset} from '../types';

export default function FinalVideoLibrary(){
  const navigate=useNavigate();
  const [products,setProducts]=useState<Product[]>([]);
  const [productId,setProductId]=useState('');
  const [videos,setVideos]=useState<VideoLibraryAsset[]>([]);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [durationFilter,setDurationFilter]=useState('all');
  const [dateFilter,setDateFilter]=useState('all');

  useEffect(()=>{productApi.list().then(rows=>{setProducts(rows);setProductId(String(rows[0]?.id||''))}).catch(()=>setError('商品列表加载失败'))},[]);
  useEffect(()=>{
    if(!productId){setVideos([]);return}
    let active=true;
    const load=async()=>{setLoading(true);try{const rows=await videoLibraryApi.list(Number(productId),'finished','final_output');if(active){setVideos(rows);setError('')}}catch{if(active)setError('成片库加载失败')}finally{if(active)setLoading(false)}};
    load();
    const refresh=()=>{if(document.visibilityState==='visible')void load()};
    const timer=window.setInterval(refresh,5000);
    document.addEventListener('visibilitychange',refresh);
    return()=>{active=false;window.clearInterval(timer);document.removeEventListener('visibilitychange',refresh)};
  },[productId]);

  const filteredVideos=useMemo(()=>videos.filter(video=>{
    const durationMatches=durationFilter==='all'
      || (durationFilter==='under-15'&&video.duration_seconds<=15)
      || (durationFilter==='15-30'&&video.duration_seconds>15&&video.duration_seconds<=30)
      || (durationFilter==='30-60'&&video.duration_seconds>30&&video.duration_seconds<=60);
    if(!durationMatches)return false;
    if(dateFilter==='all')return true;
    const createdAt=new Date(video.created_at);
    if(Number.isNaN(createdAt.getTime()))return false;
    const cutoff=new Date();
    if(dateFilter==='today')cutoff.setHours(0,0,0,0);
    else cutoff.setDate(cutoff.getDate()-Number(dateFilter));
    return createdAt>=cutoff;
  }),[videos,durationFilter,dateFilter]);
  const totalMinutes=useMemo(()=>filteredVideos.reduce((sum,item)=>sum+item.duration_seconds,0)/60,[filteredVideos]);
  const filtersActive=durationFilter!=='all'||dateFilter!=='all';
  const resetFilters=()=>{setDurationFilter('all');setDateFilter('all')};
  const currentProduct=products.find(product=>String(product.id)===productId);
  const formatDuration=(seconds:number)=>{const total=Math.round(seconds);return `${Math.floor(total/60).toString().padStart(2,'0')}:${(total%60).toString().padStart(2,'0')}`};
  const formatCreatedAt=(value:string)=>new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
  return <div className="space-y-6 max-w-7xl mx-auto animate-fade-in">
    <header><div className="text-xs tracking-[.18em] text-[var(--muted)] mb-2">FINAL VIDEO LIBRARY</div><h1 className="font-display text-4xl">成片库</h1><p className="text-sm text-[var(--muted)] mt-2">集中保存智能成片和成品混剪输出。这里的完整视频用于预览、审核和下载，不会自动进入成品混剪素材池。</p></header>
    {error&&<div className="border border-red-200 bg-red-50 text-red-700 p-3 text-sm">{error}</div>}
    <section className="panel p-4 flex flex-wrap items-end gap-3">
      <label className="field min-w-56 flex-1"><span>商品</span><select className="input-select" value={productId} onChange={event=>setProductId(event.target.value)}><option value="">请选择商品</option>{products.map(product=><option key={product.id} value={product.id}>{product.name}{product.brand_name?` · ${product.brand_name}`:''}</option>)}</select></label>
      <label className="field min-w-40"><span>时长</span><select className="input-select" value={durationFilter} onChange={event=>setDurationFilter(event.target.value)}><option value="all">全部时长</option><option value="under-15">15 秒内</option><option value="15-30">15–30 秒</option><option value="30-60">30–60 秒</option></select></label>
      <label className="field min-w-40"><span>生成时间</span><select className="input-select" value={dateFilter} onChange={event=>setDateFilter(event.target.value)}><option value="all">全部时间</option><option value="today">今天</option><option value="7">近 7 天</option><option value="30">近 30 天</option></select></label>
      <button className="btn-secondary h-[42px]" onClick={resetFilters} disabled={!filtersActive}><RotateCcw size={15}/>重置</button>
    </section>
    <section className="grid grid-cols-3 gap-3">{[[filteredVideos.length,'成片'],[filteredVideos.filter(x=>x.source==='smart_compose_output').length,'智能成片'],[totalMinutes.toFixed(1),'总分钟']].map(([value,label])=><div className="panel p-4" key={label}><div className="font-display text-2xl">{value}</div><div className="text-xs text-[var(--muted)] mt-1">{label}</div></div>)}</section>
    <section><div className="flex justify-between items-center mb-3"><div><h2 className="text-lg font-medium">完整成片</h2><p className="text-xs text-[var(--muted)] mt-1">混剪输出保留生成代次和来源记录，但与可混剪片段严格隔离。</p></div>{loading&&<Loader2 size={16} className="animate-spin"/>}</div>
      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">{filteredVideos.map(video=><article className="panel overflow-hidden" key={video.id}>
        <button className="group relative block w-full bg-black aspect-[9/16] overflow-hidden" onClick={()=>navigate(`/final-videos/${video.id}`)} aria-label={`查看 ${video.filename}`}>
          <LazyVideo className="h-full w-full object-contain pointer-events-none" muted playsInline preload="metadata" src={`${mediaUrl(video.object_url)}#t=0.1`}/>
          <span className="absolute inset-0 grid place-items-center bg-black/10 transition-colors group-hover:bg-black/25"><span className="grid h-12 w-12 place-items-center rounded-full bg-white/90 text-black shadow"><Play size={20} fill="currentColor"/></span></span>
          <span className="absolute bottom-3 right-3 rounded bg-black/75 px-2 py-1 text-xs text-white">{formatDuration(video.duration_seconds)}</span>
        </button>
        <div className="p-4">
          <h3 className="font-medium truncate">{currentProduct?.name||'未命名商品'}</h3>
          <div className="mt-1 truncate text-xs text-[var(--muted)]" title={video.filename}>{video.filename}</div>
          <div className="mt-3 text-xs text-[var(--muted)]">生成于 {formatCreatedAt(video.created_at)}</div>
          <div className="mt-4 flex gap-2"><button className="btn-primary flex-1 justify-center" onClick={()=>navigate(`/final-videos/${video.id}`)}><Play size={15}/>查看详情</button><a className="btn-secondary flex-1 justify-center" href={mediaUrl(video.object_url)} download><Download size={15}/>下载</a></div>
        </div>
      </article>)}{!filteredVideos.length&&!loading&&<div className="panel py-16 md:col-span-2 xl:col-span-3 text-center text-sm text-[var(--muted)]"><Film className="mx-auto mb-3"/>{videos.length&&filtersActive?<><div>当前筛选条件下暂无成片</div><button className="btn-secondary mt-4" onClick={resetFilters}><RotateCcw size={15}/>重置筛选</button></>:<>这个商品还没有生成完整成片</>}</div>}</div>
    </section>
  </div>;
}
