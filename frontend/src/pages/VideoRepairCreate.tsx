import {useEffect,useState} from 'react';
import {Loader2,Upload} from 'lucide-react';
import {Link,useNavigate} from 'react-router-dom';
import {ecommerceProductApi,videoRepairApi} from '../api/client';
import type {Product} from '../types';

function message(error:unknown){
  const value=error as {response?:{data?:{detail?:string|{message?:string}}};message?:string};
  const detail=value.response?.data?.detail;
  return typeof detail==='string'?detail:detail?.message||value.message||'创建失败';
}

export default function VideoRepairCreate(){
  const navigate=useNavigate();
  const [products,setProducts]=useState<Product[]>([]);
  const [productId,setProductId]=useState('');
  const [file,setFile]=useState<File|null>(null);
  const [previewUrl,setPreviewUrl]=useState('');
  const [uploadedId,setUploadedId]=useState<number|null>(null);
  const [reason,setReason]=useState('');
  const [suggestions,setSuggestions]=useState('');
  const [loading,setLoading]=useState(true);
  const [busy,setBusy]=useState(false);
  const [stage,setStage]=useState('');
  const [error,setError]=useState('');

  useEffect(()=>{ecommerceProductApi.list().then(setProducts).catch(()=>setError('电商商品加载失败')).finally(()=>setLoading(false))},[]);
  useEffect(()=>{
    if(!file){setPreviewUrl('');return}
    const url=URL.createObjectURL(file);
    setPreviewUrl(url);
    return ()=>URL.revokeObjectURL(url);
  },[file]);

  async function submit(event:React.FormEvent){
    event.preventDefault();
    if(!productId||!file||!reason.trim()||busy)return;
    setBusy(true);setError('');
    let phase:'upload'|'create'='upload';
    try{
      let sourceId=uploadedId;
      if(!sourceId){
        setStage('正在上传原视频…');
        const asset=await videoRepairApi.uploadSource(Number(productId),file);
        sourceId=asset.id;setUploadedId(asset.id);
      }
      phase='create';setStage('正在创建返修任务…');
      const task=await videoRepairApi.create({source_asset_id:sourceId,ecommerce_product_id:Number(productId),input:{
        raw_reason:reason.trim(),suggestions:suggestions.split('\n').map(row=>row.trim()).filter(Boolean).slice(0,20),
      }});
      navigate(`/video-repair/${task.id}`);
    }catch(value){setError(`${phase==='upload'?'原视频上传失败':'视频已上传，任务创建失败'}：${message(value)}`)}
    finally{setBusy(false);setStage('')}
  }

  return <div className="mx-auto max-w-4xl space-y-6 animate-fade-in"><header><div className="mb-2 text-xs tracking-[.18em] text-[var(--muted)]">NEW VIDEO REPAIR</div><h1 className="font-display text-4xl">新建审核失败修改</h1><p className="mt-2 text-sm text-[var(--muted)]">直接上传待修改的视频。确认修复预览后，新版本会进入所选商品的电商成品库。</p></header>
    <form className="panel space-y-5 p-6" onSubmit={submit}>
      <label className="field"><span>新成片所属电商商品 *</span><select className="input-select" value={productId} disabled={loading||busy} onChange={event=>{setProductId(event.target.value);setUploadedId(null)}}><option value="">请选择商品</option>{products.map(product=><option key={product.id} value={product.id}>{product.name}</option>)}</select><small>仅用于归档修复结果，无需从成品库选择原视频。</small></label>
      {!loading&&!products.length&&<p className="text-sm text-amber-800">还没有电商商品。请先<Link className="underline" to="/ecommerce-products/new">创建商品</Link>。</p>}
      <label className="field"><span>本地原视频 *</span><input className="input" type="file" accept=".mp4,.mov,.mkv,.webm,.m4v,video/*" disabled={busy} onChange={event=>{setFile(event.target.files?.[0]||null);setUploadedId(null)}}/>{file&&<small>{file.name}</small>}</label>
      {previewUrl&&<div className="rounded border border-[var(--border)] bg-slate-50 p-3"><p className="mb-2 text-sm font-medium">原视频预览</p><video aria-label="本地原视频预览" key={previewUrl} className="aspect-video w-full max-w-2xl bg-black object-contain" controls preload="metadata" src={previewUrl}/><p className="mt-2 text-xs text-[var(--muted)]">选择文件后即可在本地预览；点击“创建并分析”才会上传。</p></div>}
      <label className="field"><span>审核失败原因 *</span><textarea className="input min-h-28" maxLength={4000} value={reason} disabled={busy} onChange={event=>setReason(event.target.value)} placeholder="例如：6 到 10 秒商品效果展示不准确"/><small>{reason.length}/4000</small></label>
      <label className="field"><span>修改建议（可选，每行一条）</span><textarea className="input min-h-24" value={suggestions} disabled={busy} onChange={event=>setSuggestions(event.target.value)} placeholder="替换为真实商品展示画面"/></label>
      {error&&<p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
      <div className="flex items-center gap-3"><button className="primary" type="submit" disabled={busy||loading||!productId||!file||!reason.trim()}>{busy?<Loader2 size={15} className="animate-spin"/>:<Upload size={15}/>}创建并分析</button><Link className="btn-secondary" to="/video-repair">取消</Link>{stage&&<span role="status" className="text-sm text-[var(--muted)]">{stage}</span>}</div>
    </form>
  </div>;
}
