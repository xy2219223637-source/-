import {useCallback,useEffect,useState} from 'react';
import {ArrowLeft,Download,Loader2} from 'lucide-react';
import {Link,useParams} from 'react-router-dom';
import {mediaUrl,productApi,videoLibraryApi,videoTaxonomyApi} from '../api/client';
import {VideoTaxonomyEditor,VideoTaxonomySummary} from '../components/VideoTaxonomyEditor';
import type {Product,VideoLibraryAsset,VideoTaxonomyCatalog} from '../types';

type LibraryType='semi_finished'|'finished';
type Props={libraryType:LibraryType;title:string};

function formatDuration(seconds:number){
  const total=Math.max(0,Math.round(seconds));
  return `${Math.floor(total/60).toString().padStart(2,'0')}:${(total%60).toString().padStart(2,'0')}`;
}

function formatDate(value:string){
  return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));
}

export default function VideoAssetDetail({libraryType,title}:Props){
  const {id}=useParams();
  const assetId=Number(id);
  const libraryPath=libraryType==='finished'?'/finished-video-assets':'/semi-finished-assets';
  const [asset,setAsset]=useState<VideoLibraryAsset|null>(null);
  const [product,setProduct]=useState<Product|null>(null);
  const [catalog,setCatalog]=useState<VideoTaxonomyCatalog|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [editing,setEditing]=useState(false);

  const refreshAsset=useCallback(async()=>{
    if(!Number.isInteger(assetId)||assetId<=0)throw new Error('not-found');
    const next=await videoLibraryApi.get(assetId);
    if(next.asset_kind!=='material'||next.library_type!==libraryType)throw new Error('not-found');
    setAsset(next);
  },[assetId,libraryType]);

  const refreshCatalog=useCallback(async()=>{
    setCatalog(await videoTaxonomyApi.catalog('material'));
  },[]);

  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');
    if(!Number.isInteger(assetId)||assetId<=0){
      setError('没有找到这条素材');
      setLoading(false);
      return()=>{active=false};
    }
    Promise.all([videoLibraryApi.get(assetId),videoTaxonomyApi.catalog('material')]).then(async([nextAsset,nextCatalog])=>{
      if(nextAsset.asset_kind!=='material'||nextAsset.library_type!==libraryType)throw new Error('not-found');
      const nextProduct=await productApi.get(nextAsset.product_id);
      if(active){setAsset(nextAsset);setCatalog(nextCatalog);setProduct(nextProduct)}
    }).catch(()=>{if(active)setError('没有找到这条素材')}).finally(()=>{if(active)setLoading(false)});
    return()=>{active=false};
  },[assetId,libraryType]);

  if(loading)return <div className="grid min-h-80 place-items-center text-sm text-[var(--muted)]"><div className="flex items-center gap-2"><Loader2 size={17} className="animate-spin"/>素材加载中</div></div>;
  if(error||!asset)return <div className="panel p-10 text-center"><p>{error||'没有找到这条素材'}</p><Link className="btn-secondary mt-5" to={libraryPath}>返回{title}</Link></div>;

  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><Link to={libraryPath} className="mb-4 inline-flex items-center gap-2 text-sm text-[var(--muted)] hover:text-black"><ArrowLeft size={15}/>返回{title}</Link><h1 className="font-display text-4xl">{product?.name||title}</h1><p className="mt-2 max-w-2xl truncate text-sm text-[var(--muted)]" title={asset.filename}>{asset.filename}</p></div><a className="btn-secondary" href={mediaUrl(asset.object_url)} download><Download size={15}/>下载视频</a></header>

    <section className="panel grid overflow-hidden lg:grid-cols-[minmax(360px,1.1fr)_minmax(340px,.9fr)]">
      <div className="grid min-h-[540px] place-items-center bg-black p-5"><video className="max-h-[70vh] max-w-full" controls playsInline src={mediaUrl(asset.object_url)}/></div>
      <div className="p-6 lg:p-8">
        <dl className="grid grid-cols-[88px_1fr] gap-x-4 gap-y-4 text-sm"><dt className="text-[var(--muted)]">商品</dt><dd>{product?.name||'未命名商品'}</dd><dt className="text-[var(--muted)]">文件名</dt><dd className="break-all">{asset.filename}</dd><dt className="text-[var(--muted)]">时长</dt><dd>{formatDuration(asset.duration_seconds)}</dd><dt className="text-[var(--muted)]">画面规格</dt><dd>{asset.width} x {asset.height}</dd><dt className="text-[var(--muted)]">上传时间</dt><dd>{formatDate(asset.created_at)}</dd></dl>
        <div className="mt-7 border-t border-[var(--border)] pt-6"><VideoTaxonomySummary asset={asset} onEdit={()=>setEditing(true)}/></div>
      </div>
    </section>

    {editing&&catalog&&<VideoTaxonomyEditor asset={asset} catalog={catalog} objectType="material" onClose={()=>setEditing(false)} onSaved={refreshAsset} onCatalogRefresh={refreshCatalog}/>}
  </div>;
}
