import {useEffect,useMemo,useState} from 'react';
import {Download,Film,Loader2} from 'lucide-react';
import {mediaUrl,productApi,videoLibraryApi} from '../api/client';
import type {Product,VideoLibraryAsset} from '../types';

export default function FinalVideoLibrary(){
  const [products,setProducts]=useState<Product[]>([]);
  const [productId,setProductId]=useState('');
  const [videos,setVideos]=useState<VideoLibraryAsset[]>([]);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');

  useEffect(()=>{productApi.list().then(rows=>{setProducts(rows);setProductId(String(rows[0]?.id||''))}).catch(()=>setError('商品列表加载失败'))},[]);
  useEffect(()=>{
    if(!productId){setVideos([]);return}
    let active=true;
    const load=async()=>{setLoading(true);try{const rows=await videoLibraryApi.list(Number(productId),'finished','final_output');if(active){setVideos(rows);setError('')}}catch{if(active)setError('成片库加载失败')}finally{if(active)setLoading(false)}};
    load(); const timer=window.setInterval(load,5000);
    return()=>{active=false;window.clearInterval(timer)};
  },[productId]);

  const totalMinutes=useMemo(()=>videos.reduce((sum,item)=>sum+item.duration_seconds,0)/60,[videos]);
  return <div className="space-y-6 max-w-7xl mx-auto animate-fade-in">
    <header><div className="text-xs tracking-[.18em] text-[var(--muted)] mb-2">FINAL VIDEO LIBRARY</div><h1 className="font-display text-4xl">成片库</h1><p className="text-sm text-[var(--muted)] mt-2">集中保存智能成片和成品混剪输出。这里的完整视频用于预览、审核和下载，不会自动进入成品混剪素材池。</p></header>
    {error&&<div className="border border-red-200 bg-red-50 text-red-700 p-3 text-sm">{error}</div>}
    <section className="panel p-5"><label className="field"><span>当前商品</span><select className="input-select" value={productId} onChange={event=>setProductId(event.target.value)}><option value="">请选择商品</option>{products.map(product=><option key={product.id} value={product.id}>{product.name}{product.brand_name?` · ${product.brand_name}`:''}</option>)}</select></label></section>
    <section className="grid grid-cols-3 gap-3">{[[videos.length,'成片'],[videos.filter(x=>x.source==='smart_compose_output').length,'智能成片'],[totalMinutes.toFixed(1),'总分钟']].map(([value,label])=><div className="panel p-4" key={label}><div className="font-display text-2xl">{value}</div><div className="text-xs text-[var(--muted)] mt-1">{label}</div></div>)}</section>
    <section><div className="flex justify-between items-center mb-3"><div><h2 className="text-lg font-medium">完整成片</h2><p className="text-xs text-[var(--muted)] mt-1">混剪输出保留生成代次和来源记录，但与可混剪片段严格隔离。</p></div>{loading&&<Loader2 size={16} className="animate-spin"/>}</div>
      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-4">{videos.map(video=><article className="panel overflow-hidden" key={video.id}><video className="w-full bg-black aspect-[9/16] object-contain" controls preload="metadata" src={mediaUrl(video.object_url)}/><div className="p-4"><b className="block truncate">{video.filename}</b><div className="text-xs text-[var(--muted)] mt-2">{video.duration_seconds.toFixed(1)} 秒 · 第 {video.generation_depth} 代 · {video.source==='finished_remix_output'?'成品混剪':'智能成片'}</div><a className="btn-secondary mt-4 justify-center" href={mediaUrl(video.object_url)}><Download size={15}/>下载 MP4</a></div></article>)}{!videos.length&&!loading&&<div className="panel py-16 md:col-span-2 xl:col-span-3 text-center text-sm text-[var(--muted)]"><Film className="mx-auto mb-3"/>这个商品还没有生成完整成片</div>}</div>
    </section>
  </div>;
}
