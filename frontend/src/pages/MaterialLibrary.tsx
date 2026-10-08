import {useEffect, useState} from 'react';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {ImagePlus, Loader2, Plus, Trash2, Upload} from 'lucide-react';
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
  const [introduction,setIntroduction] = useState('');
  const [image,setImage] = useState<File|null>(null);
  const [files,setFiles] = useState<File[]>([]);
  const [editing,setEditing] = useState(false);
  const [creating,setCreating] = useState(false);
  const [loading,setLoading] = useState(true);
  const [busy,setBusy] = useState(false);
  const [progress,setProgress] = useState('');
  const [error,setError] = useState('');

  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');setEditing(false);setCreating(false);setFiles([]);setImage(null);
    if(productId){
      Promise.all([materialLibraryApi.product(id),materialLibraryApi.assets(id)])
        .then(([nextProduct,nextAssets])=>{if(active){setProduct(nextProduct);setIntroduction(nextProduct.introduction);setAssets(nextAssets)}})
        .catch(value=>{if(active)setError(errorMessage(value))}).finally(()=>{if(active)setLoading(false)});
    }else{
      materialLibraryApi.products().then(rows=>{if(active)setProducts(rows)})
        .catch(value=>{if(active)setError(errorMessage(value))}).finally(()=>{if(active)setLoading(false)});
    }
    return ()=>{active=false};
  },[productId,id]);

  async function saveProduct(event:React.FormEvent){
    event.preventDefault();
    if(busy || !introduction.trim() || (creating && !image))return;
    setBusy(true);setError('');
    try{
      if(creating && image){
        const row=await materialLibraryApi.create(image,introduction.trim());
        navigate(`/material-library/${row.id}`);
      }else if(product){
        const row=await materialLibraryApi.update(product.id,introduction.trim(),image||undefined);
        setProduct(row);setEditing(false);setImage(null);
      }
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
        setAssets(current=>[row,...current]);uploaded++;
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
        {productId&&<Link to="/material-library" className="text-sm text-[var(--muted)]">← 返回商品素材库</Link>}
        <h1 className="font-display text-4xl mt-1">{productId?'商品素材':'商品素材库'}</h1>
        <p className="mt-2 text-sm text-[var(--muted)]">按商品保存用户上传的图片和视频素材。</p>
      </div>
      {!productId&&!creating&&<button type="button" className="primary" onClick={()=>{setCreating(true);setIntroduction('');setImage(null)}}><Plus size={16}/>新建商品</button>}
    </header>
    {error&&<p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
    {loading?<div className="panel flex items-center gap-2 p-6"><Loader2 className="animate-spin" size={18}/>加载中…</div>:productId?product&&<>
      <section className="panel p-5">
        {editing?<form onSubmit={saveProduct} className="space-y-4">
          <h2 className="font-medium">编辑商品</h2>
          <label className="field"><span>产品图片（不更换可留空）</span><input type="file" accept=".jpg,.jpeg,.png,.webp,.gif,image/*" onChange={event=>setImage(event.target.files?.[0]||null)}/></label>
          <label className="field"><span>产品介绍 *</span><textarea className="input min-h-32" maxLength={10000} value={introduction} onChange={event=>setIntroduction(event.target.value)}/></label>
          <div className="flex gap-2"><button className="primary" disabled={busy||!introduction.trim()}>保存</button><button type="button" className="btn-secondary" onClick={()=>{setEditing(false);setIntroduction(product.introduction);setImage(null)}}>取消</button></div>
        </form>:<div className="flex flex-col sm:flex-row gap-5">
          <img src={product.image_url} alt="产品图片" className="h-44 w-44 rounded object-cover border border-[var(--border)]"/>
          <div className="flex-1 min-w-0"><h2 className="font-medium">产品介绍</h2><p className="mt-2 whitespace-pre-wrap break-words text-sm">{product.introduction}</p><button type="button" className="btn-secondary mt-4" onClick={()=>setEditing(true)}>编辑商品</button></div>
        </div>}
      </section>
      <section className="panel p-5 space-y-4">
        <div><h2 className="font-medium">上传素材</h2><p className="text-xs text-[var(--muted)] mt-1">支持 JPG、PNG、WebP、GIF、MP4、MOV、WebM 和 MKV；上传后直接保存，不做内容分析。</p></div>
        <input aria-label="选择素材文件" type="file" accept=".jpg,.jpeg,.png,.webp,.gif,.mp4,.mov,.webm,.mkv,image/*,video/*" multiple disabled={busy} onChange={event=>setFiles(Array.from(event.target.files||[]))}/>
        <div className="flex items-center gap-3"><button type="button" className="primary" disabled={busy||!files.length} onClick={uploadFiles}><Upload size={16}/>上传 {files.length||''} 个素材</button>{progress&&<span role="status" className="text-sm text-[var(--muted)]">{progress}</span>}</div>
      </section>
      <section className="space-y-3"><h2 className="font-medium">素材（{assets.length}）</h2>
        {!assets.length?<div className="panel p-8 text-center text-sm text-[var(--muted)]">还没有素材，选择文件后上传。</div>:<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{assets.map(asset=><article key={asset.id} className="panel overflow-hidden">
          {asset.kind==='image'?<img src={asset.object_url} alt={asset.filename} className="w-full h-48 object-contain bg-black/5"/>:<video src={asset.object_url} controls preload="metadata" className="w-full h-48 bg-black"/>}
          <div className="p-4"><p className="truncate text-sm font-medium" title={asset.filename}>{asset.filename}</p><p className="mt-1 text-xs text-[var(--muted)]">{asset.kind==='image'?'图片':'视频'} · {(asset.size_bytes/1024/1024).toFixed(1)} MB</p>
            <div className="mt-3 flex gap-2"><a className="btn-secondary" href={asset.object_url} target="_blank" rel="noreferrer">查看原文件</a><button type="button" className="btn-secondary" disabled={busy} onClick={()=>removeAsset(asset)}><Trash2 size={15}/>移除</button></div>
          </div></article>)}</div>}
      </section>
    </>:<>
      {creating&&<form className="panel p-6 space-y-4" onSubmit={saveProduct}><h2 className="font-medium">新建商品</h2>
        <label className="field"><span>产品图片 *</span><input type="file" accept=".jpg,.jpeg,.png,.webp,.gif,image/*" required onChange={event=>setImage(event.target.files?.[0]||null)}/></label>
        <label className="field"><span>产品介绍 *</span><textarea className="input min-h-32" maxLength={10000} required value={introduction} onChange={event=>setIntroduction(event.target.value)} placeholder="填写产品介绍"/></label>
        <div className="flex gap-2"><button className="primary" disabled={busy||!image||!introduction.trim()}>{busy?<Loader2 size={16} className="animate-spin"/>:<ImagePlus size={16}/>}创建商品</button><button className="btn-secondary" type="button" onClick={()=>setCreating(false)}>取消</button></div>
      </form>}
      {!products.length&&!creating?<div className="panel p-10 text-center text-sm text-[var(--muted)]">暂无商品，点击“新建商品”开始建立素材库。</div>:<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{products.map(row=><Link key={row.id} to={`/material-library/${row.id}`} className="panel overflow-hidden hover:shadow-md transition-shadow"><img src={row.image_url} alt="产品图片" className="w-full h-48 object-cover"/><div className="p-4"><p className="line-clamp-2 whitespace-pre-wrap break-words text-sm">{row.introduction}</p><span className="text-xs text-[var(--muted)] mt-2 block">查看商品素材 →</span></div></Link>)}</div>}
    </>}
  </div>;
}
