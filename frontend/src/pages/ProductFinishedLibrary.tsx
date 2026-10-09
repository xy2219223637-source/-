import {useEffect,useState} from 'react';
import {Link,useNavigate,useParams} from 'react-router-dom';
import {Loader2} from 'lucide-react';
import {materialLibraryApi} from '../api/materialLibrary';
import type {LibraryFinishedVideo,LibraryProduct} from '../api/materialLibrary';
import {mediaUrl} from '../api/client';

export default function ProductFinishedLibrary(){
  const {productId}=useParams();
  const id=Number(productId);
  const navigate=useNavigate();
  const [products,setProducts]=useState<LibraryProduct[]>([]);
  const [videos,setVideos]=useState<LibraryFinishedVideo[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');

  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');
    materialLibraryApi.products().then(rows=>{if(active)setProducts(rows)})
      .catch(()=>{if(active)setError('商品列表加载失败')}).finally(()=>{if(active&&!productId)setLoading(false)});
    return()=>{active=false};
  },[productId]);

  useEffect(()=>{
    if(!productId){setVideos([]);return;}
    let active=true;
    setLoading(true);setError('');
    materialLibraryApi.finishedVideos(id).then(rows=>{if(active)setVideos(rows)})
      .catch(()=>{if(active)setError('商品成品加载失败')}).finally(()=>{if(active)setLoading(false)});
    return()=>{active=false};
  },[productId,id]);

  return <div className="mx-auto max-w-6xl space-y-6 animate-fade-in">
    <header><h1 className="font-display text-4xl">商品成品库</h1><p className="mt-2 text-sm text-[var(--muted)]">按商品查看已生成并保存的成片。</p></header>
    <div className="flex flex-wrap items-end gap-3"><label className="field min-w-64"><span>选择商品</span><select className="input-select" value={productId||''} onChange={event=>navigate(event.target.value?`/product-finished-library/${event.target.value}`:'/product-finished-library')}><option value="">请选择商品</option>{products.map(row=><option key={row.id} value={row.id}>{row.introduction.slice(0,60)}</option>)}</select></label><Link className="btn-secondary" to="/ecommerce-products">管理商品</Link>{productId&&<Link className="btn-secondary" to={`/material-library/${productId}`}>查看该商品素材</Link>}</div>
    {error&&<p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
    {loading?<div className="panel flex items-center gap-2 p-6"><Loader2 size={18} className="animate-spin"/>加载中…</div>:!productId?<div className="panel p-10 text-center text-sm text-[var(--muted)]">{products.length?'请选择商品查看成品。':'还没有商品，请先在电商商品管理中创建。'}</div>:!videos.length?<div className="panel p-10 text-center text-sm text-[var(--muted)]">该商品暂无成品视频。后续生成完成的成片会在这里展示。</div>:<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{videos.map(video=><article key={video.id} className="panel overflow-hidden"><video controls preload="metadata" src={mediaUrl(video.object_url)} className="aspect-[9/16] max-h-96 w-full bg-black object-contain"/><div className="space-y-2 p-4"><p className="truncate text-sm font-medium" title={video.filename}>{video.filename}</p><p className="text-xs text-[var(--muted)]">{video.duration_seconds.toFixed(1)} 秒 · {new Date(video.created_at).toLocaleString('zh-CN')}</p><a className="btn-secondary" href={mediaUrl(video.object_url)} target="_blank" rel="noreferrer">查看原文件</a></div></article>)}</div>}
  </div>;
}
