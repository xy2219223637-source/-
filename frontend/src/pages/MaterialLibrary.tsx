import {useEffect, useRef, useState} from 'react';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {Loader2, RefreshCw, Trash2, Upload} from 'lucide-react';
import {materialLibraryApi} from '../api/materialLibrary';
import type {LibraryAsset, LibraryProduct} from '../api/materialLibrary';

function errorMessage(error:unknown){
  const value = error as {response?:{data?:{detail?:string}}; message?:string};
  return value.response?.data?.detail || value.message || '操作失败';
}

export default function MaterialLibrary(){
  const {productId} = useParams();
  const id = Number(productId);
  const navigate = useNavigate();
  const [products,setProducts] = useState<LibraryProduct[]>([]);
  const [product,setProduct] = useState<LibraryProduct|null>(null);
  const [assets,setAssets] = useState<LibraryAsset[]>([]);
  const [files,setFiles] = useState<File[]>([]);
  const [loading,setLoading] = useState(true);
  const [busy,setBusy] = useState(false);
  const [progress,setProgress] = useState('');
  const [error,setError] = useState('');
  const [editingSegment,setEditingSegment] = useState('');
  const [segmentDraft,setSegmentDraft] = useState({start:'',end:'',description:''});
  const videoRefs=useRef<Record<number,HTMLVideoElement|null>>({});

  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');setFiles([]);setProduct(null);setAssets([]);
    if(productId){
      Promise.all([materialLibraryApi.product(id),materialLibraryApi.assets(id),materialLibraryApi.products()])
        .then(([nextProduct,nextAssets,nextProducts])=>{if(active){setProduct(nextProduct);setAssets(nextAssets);setProducts(nextProducts)}})
        .catch(value=>{if(active)setError(errorMessage(value))}).finally(()=>{if(active)setLoading(false)});
    }else{
      materialLibraryApi.products().then(rows=>{if(active)setProducts(rows)})
        .catch(value=>{if(active)setError(errorMessage(value))}).finally(()=>{if(active)setLoading(false)});
    }
    return ()=>{active=false};
  },[productId,id]);

  useEffect(()=>{
    if(!productId||busy||!assets.some(row=>row.kind==='video'&&['pending','validating','queued_for_analysis','analyzing'].includes(row.analysis_status||'')))return;
    const timer=window.setInterval(()=>materialLibraryApi.assets(id).then(setAssets).catch(()=>{}),3000);
    return()=>window.clearInterval(timer);
  },[productId,id,busy,assets]);

  async function reanalyze(asset:LibraryAsset){
    if(!product||busy)return;
    setBusy(true);setError('');
    try{const row=await materialLibraryApi.reanalyze(product.id,asset.id);setAssets(current=>current.map(item=>item.id===row.id?row:item))}
    catch(value){setError(errorMessage(value))}finally{setBusy(false)}
  }

  async function saveSegment(asset:LibraryAsset,highlightId:string){
    if(!product||busy)return;
    setBusy(true);setError('');
    try{
      const row=await materialLibraryApi.correctSegment(product.id,asset.id,highlightId,{
        start_ms:Math.round(Number(segmentDraft.start)*1000),end_ms:Math.round(Number(segmentDraft.end)*1000),description:segmentDraft.description.trim(),
      });
      setAssets(current=>current.map(item=>item.id===row.id?row:item));setEditingSegment('');
    }catch(value){setError(errorMessage(value))}finally{setBusy(false)}
  }

  async function uploadFiles(){
    if(!product || !files.length || busy)return;
    setBusy(true);setError('');
    let uploaded=0;
    try{
      for(const file of files){
        setProgress(`上传 ${uploaded+1}/${files.length}：${file.name}`);
        const row=await materialLibraryApi.upload(product.id,file,percent=>setProgress(`上传 ${uploaded+1}/${files.length}：${file.name} · ${percent}%`));
        setAssets(current=>[row,...current.filter(item=>item.id!==row.id)]);uploaded++;
      }
      setFiles([]);
    }catch(value){setError(`已成功上传 ${uploaded} 个文件；${errorMessage(value)}`)}finally{setBusy(false);setProgress('')}
  }

  async function removeAsset(asset:LibraryAsset){
    if(!product || busy || !window.confirm(`移除素材“${asset.filename}”？`))return;
    setBusy(true);setError('');
    try{await materialLibraryApi.remove(product.id,asset.id);setAssets(current=>current.filter(row=>row.id!==asset.id))}
    catch(value){setError(errorMessage(value))}finally{setBusy(false)}
  }

  return <div className="mx-auto max-w-6xl space-y-6 animate-fade-in">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="font-display text-4xl mt-1">商品素材库</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">选择商品，查看和上传该商品的图片及视频素材。</p>
      </div>
    </header>
    <div className="flex flex-wrap items-end gap-3"><label className="field min-w-64"><span>选择商品</span><select className="input-select" value={productId||''} onChange={event=>navigate(event.target.value?`/material-library/${event.target.value}`:'/material-library')}><option value="">请选择商品</option>{products.map(row=><option key={row.id} value={row.id}>{row.introduction.slice(0,60)}</option>)}</select></label><Link className="btn-secondary" to="/ecommerce-products">管理商品</Link>{product&&<Link className="btn-secondary" to={`/product-finished-library/${product.id}`}>查看该商品成品</Link>}</div>
    {error&&<p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
    {loading?<div className="panel flex items-center gap-2 p-6"><Loader2 className="animate-spin" size={18}/>加载中…</div>:productId?product&&<>
      <section className="panel p-5">
        <div className="flex flex-col sm:flex-row gap-5">
          <img src={product.image_url} alt="产品图片" className="h-44 w-44 rounded object-cover border border-[var(--border)]"/>
          <div className="flex-1 min-w-0"><h2 className="font-medium">产品介绍</h2><p className="mt-2 whitespace-pre-wrap break-words text-sm">{product.introduction}</p></div>
        </div>
      </section>
      <section className="panel p-5 space-y-4">
        <div><h2 className="font-medium">上传素材</h2><p className="text-xs text-[var(--muted)] mt-1">支持 JPG、PNG、WebP、GIF、MP4、MOV、WebM 和 MKV；视频上传后在后台分析可用片段。</p></div>
        <input aria-label="选择素材文件" type="file" accept=".jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.webm,.mkv,image/*,video/*" multiple disabled={busy} onChange={event=>setFiles(Array.from(event.target.files||[]))}/>
        <div className="flex items-center gap-3"><button type="button" className="primary" disabled={busy||!files.length} onClick={uploadFiles}><Upload size={16}/>上传 {files.length||''} 个素材</button>{progress&&<span role="status" className="text-sm text-[var(--muted)]">{progress}</span>}</div>
      </section>
      <section className="space-y-3"><h2 className="font-medium">素材（{assets.length}）</h2>
        {!assets.length?<div className="panel p-8 text-center text-sm text-[var(--muted)]">还没有素材，选择文件后上传。</div>:<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{assets.map(asset=><article key={asset.id} className="panel overflow-hidden">
          {asset.kind==='image'?<img src={asset.object_url} alt={asset.filename} className="w-full h-48 object-contain bg-black/5"/>:<video ref={element=>{videoRefs.current[asset.id]=element}} src={asset.object_url} controls preload="metadata" className="w-full h-48 bg-black"/>}
          <div className="p-4"><p className="truncate text-sm font-medium" title={asset.filename}>{asset.filename}</p><p className="mt-1 text-xs text-[var(--muted)]">{asset.kind==='image'?'图片':'视频'} · {(asset.size_bytes/1024/1024).toFixed(1)} MB</p>
            {asset.kind==='video'&&<div className="mt-3 space-y-2 text-xs"><p className="font-medium">{asset.analysis_status==='analyzed'?'分析完成':asset.analysis_status==='analysis_failed'?'分析失败':'等待或正在分析'}</p>{asset.analysis_summary&&<p>{asset.analysis_summary}</p>}{asset.analysis_error&&<p className="text-rose-700">{asset.analysis_error}</p>}{asset.analysis_status==='analysis_failed'&&<button type="button" className="btn-secondary" disabled={busy} onClick={()=>reanalyze(asset)}><RefreshCw size={13}/>重试分析</button>}{asset.segments?.map(segment=>{
              const key=`${asset.id}:${segment.highlight_id}`;
              return <div key={key} className="rounded border border-[var(--border)] p-2"><button type="button" className="text-left font-medium underline" onClick={()=>{const video=videoRefs.current[asset.id];if(video){video.currentTime=segment.start;void video.play()}}}>{segment.start.toFixed(1)}–{segment.end.toFixed(1)} 秒 · {segment.description}</button><p className="mt-1 text-[var(--muted)]">商品：{{clear:'清晰',partial:'部分可见',none:'未出现',uncertain:'无法确认'}[segment.product_visibility||'uncertain']} · 声音：{{mute_ok:'可静音',keep_original:'保留原声',needs_review:'需人工确认'}[segment.audio_mode||'needs_review']}</p>{!!segment.on_screen_text?.length&&<p>画面文字：{segment.on_screen_text.join('、')}</p>}{editingSegment===key?<div className="mt-2 space-y-2"><div className="flex gap-2"><input className="input w-24" aria-label="片段开始秒数" type="number" step="0.1" min="0" value={segmentDraft.start} onChange={event=>setSegmentDraft({...segmentDraft,start:event.target.value})}/><input className="input w-24" aria-label="片段结束秒数" type="number" step="0.1" min="0" value={segmentDraft.end} onChange={event=>setSegmentDraft({...segmentDraft,end:event.target.value})}/></div><input className="input w-full" aria-label="片段描述" maxLength={500} value={segmentDraft.description} onChange={event=>setSegmentDraft({...segmentDraft,description:event.target.value})}/><div className="flex gap-2"><button className="btn-secondary" type="button" disabled={busy||!segmentDraft.description.trim()} onClick={()=>saveSegment(asset,segment.highlight_id)}>保存</button><button className="btn-secondary" type="button" onClick={()=>setEditingSegment('')}>取消</button></div></div>:<button type="button" className="mt-1 text-[var(--muted)] underline" onClick={()=>{setEditingSegment(key);setSegmentDraft({start:String(segment.start),end:String(segment.end),description:segment.description})}}>修正片段</button>}</div>;
            })}</div>}
            <div className="mt-3 flex gap-2"><a className="btn-secondary" href={asset.object_url} target="_blank" rel="noreferrer">查看原文件</a><button type="button" className="btn-secondary" disabled={busy} onClick={()=>removeAsset(asset)}><Trash2 size={15}/>移除</button></div>
          </div></article>)}</div>}
      </section>
    </>:<>
      <div className="panel p-10 text-center text-sm text-[var(--muted)]">{products.length?'请选择商品查看素材。':'还没有商品，请先在电商商品管理中创建。'}</div>
    </>}
  </div>;
}
