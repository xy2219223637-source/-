import {useEffect,useState} from 'react';
import {Loader2,Upload} from 'lucide-react';
import {Link,useNavigate,useSearchParams} from 'react-router-dom';
import {videoRepairApi} from '../api/client';
import {materialLibraryApi} from '../api/materialLibrary';
import type {LibraryProduct} from '../api/materialLibrary';

function message(error:unknown){
  const value=error as {response?:{data?:{detail?:string|{message?:string}|Array<{loc?:Array<string|number>;msg?:string}>}};message?:string};
  const detail=value.response?.data?.detail;
  if(typeof detail==='string')return detail;
  if(Array.isArray(detail)){
    if(detail.some(row=>row.loc?.includes('product_id')))return '后端仍在使用旧版上传接口，请更新或重启后端后重试';
    return detail.map(row=>row.msg||'请求参数无效').join('；');
  }
  return detail?.message||value.message||'创建失败';
}

export default function VideoRepairCreate(){
  const navigate=useNavigate();const [params]=useSearchParams();
  const [products,setProducts]=useState<LibraryProduct[]>([]);
  const [productId,setProductId]=useState(params.get('material_library_product_id')||'');
  const [file,setFile]=useState<File|null>(null);
  const [uploadedId,setUploadedId]=useState<number|null>(null);
  const [reason,setReason]=useState('');const [suggestions,setSuggestions]=useState('');
  const [loading,setLoading]=useState(true);const [busy,setBusy]=useState(false);
  const [stage,setStage]=useState('');const [error,setError]=useState('');

  useEffect(()=>{materialLibraryApi.products().then(rows=>{setProducts(rows);setProductId(current=>rows.some(row=>String(row.id)===current)?current:'')}).catch(()=>setError('素材库商品加载失败')).finally(()=>setLoading(false))},[]);

  async function submit(event:React.FormEvent){
    event.preventDefault();
    if(!productId||!file||!reason.trim()||busy)return;
    setBusy(true);setError('');
    let phase:'upload'|'analysis'='upload';
    try{
      let sourceId=uploadedId;
      if(!sourceId){
        setStage('正在上传原视频到 OSS…');
        const asset=await videoRepairApi.uploadLibrarySource(Number(productId),file);
        sourceId=asset.id;setUploadedId(asset.id);
      }
      phase='analysis';
      setStage('OSS 上传完成，正在创建并分析…');
      const task=await videoRepairApi.create({source_asset_id:sourceId,material_library_product_id:Number(productId),input:{
        raw_reason:reason.trim(),
        suggestions:suggestions.split('\n').map(item=>item.trim()).filter(Boolean).slice(0,20),
      }});
      navigate(`/video-repair/${task.id}`);
    }catch(value){setError(`${phase==='upload'?'视频尚未上传成功':'视频已上传，任务创建失败'}：${message(value)}`)}finally{setBusy(false);setStage('')}
  }

  return <div className="mx-auto max-w-4xl space-y-6 animate-fade-in"><header><div className="mb-2 text-xs tracking-[.18em] text-[var(--muted)]">NEW VIDEO REPAIR</div><h1 className="font-display text-4xl">新建审核失败修改</h1><p className="mt-2 text-sm text-[var(--muted)]">选择素材库商品并上传本地原视频，描述审核问题后由系统分析修改范围。</p></header>
    <form className="panel space-y-5 p-6" onSubmit={submit}>
      <label className="field"><span>素材库商品 *</span><select className="input-select" value={productId} disabled={loading||busy} onChange={event=>{setProductId(event.target.value);setUploadedId(null)}}><option value="">请选择素材库商品</option>{products.map(product=><option key={product.id} value={product.id}>{product.introduction.slice(0,60)}</option>)}</select><small>先在商品素材库上传可用视频，返修时可选择并裁剪。</small></label>
      <label className="field"><span>本地原视频 *</span><input className="input" type="file" accept=".mp4,.mov,.mkv,.webm,video/*" disabled={busy} onChange={event=>{setFile(event.target.files?.[0]||null);setUploadedId(null)}}/>{file&&<small>{file.name}</small>}</label>
      <label className="field"><span>问题描述 *</span><textarea aria-label="问题描述" className="input min-h-28" maxLength={4000} value={reason} disabled={busy} onChange={event=>setReason(event.target.value)} placeholder="例如：6 到 10 秒商品效果展示不准确"/><small>{reason.length}/4000</small></label>
      <label className="field"><span>建议（可选，每行一条）</span><textarea className="input min-h-24" value={suggestions} disabled={busy} onChange={event=>setSuggestions(event.target.value)} placeholder="替换为真实商品展示画面"/></label>
      {error&&<p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
      <div className="flex items-center gap-3"><button className="primary" type="submit" disabled={busy||loading||!productId||!file||!reason.trim()}>{busy?<Loader2 size={15} className="animate-spin"/>:<Upload size={15}/>}创建并分析</button><Link className="btn-secondary" to="/video-repair">取消</Link>{stage&&<span role="status" className="text-sm text-[var(--muted)]">{stage}</span>}</div>
    </form>
  </div>;
}
