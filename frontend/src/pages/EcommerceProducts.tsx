import {useEffect, useState} from 'react';
import {Link, useNavigate, useParams} from 'react-router-dom';
import {ImagePlus, Loader2, Plus} from 'lucide-react';
import {materialLibraryApi} from '../api/materialLibrary';
import type {LibraryProduct} from '../api/materialLibrary';

function message(error:unknown){
  const value=error as {response?:{data?:{detail?:string}};message?:string};
  return value.response?.data?.detail||value.message||'操作失败';
}

export default function EcommerceProducts(){
  const {productId}=useParams();
  const id=Number(productId);
  const navigate=useNavigate();
  const [products,setProducts]=useState<LibraryProduct[]>([]);
  const [product,setProduct]=useState<LibraryProduct|null>(null);
  const [introduction,setIntroduction]=useState('');
  const [image,setImage]=useState<File|null>(null);
  const [editing,setEditing]=useState(false);
  const [creating,setCreating]=useState(false);
  const [loading,setLoading]=useState(true);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');

  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');setEditing(false);setCreating(false);setImage(null);setProduct(null);
    if(productId){
      materialLibraryApi.product(id).then(row=>{if(active){setProduct(row);setIntroduction(row.introduction)}})
        .catch(value=>{if(active)setError(message(value))}).finally(()=>{if(active)setLoading(false)});
    }else{
      materialLibraryApi.products().then(rows=>{if(active)setProducts(rows)})
        .catch(value=>{if(active)setError(message(value))}).finally(()=>{if(active)setLoading(false)});
    }
    return()=>{active=false};
  },[productId,id]);

  async function save(event:React.FormEvent){
    event.preventDefault();
    if(busy||!introduction.trim()||(creating&&!image))return;
    setBusy(true);setError('');
    try{
      if(creating&&image){
        const row=await materialLibraryApi.create(image,introduction.trim());
        navigate(`/ecommerce-products/${row.id}`);
      }else if(product){
        const row=await materialLibraryApi.update(product.id,introduction.trim(),image||undefined);
        setProduct(row);setEditing(false);setImage(null);
      }
    }catch(value){setError(message(value))}finally{setBusy(false)}
  }

  return <div className="mx-auto max-w-6xl space-y-6 animate-fade-in">
    <header className="flex flex-wrap items-start justify-between gap-3"><div>
      {productId&&<Link to="/ecommerce-products" className="text-sm text-[var(--muted)]">← 返回电商商品管理</Link>}
      <h1 className="font-display text-4xl mt-1">电商商品管理</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">维护商品图片和介绍，素材与成品按商品分别查看。</p>
    </div>{!productId&&!creating&&<button type="button" className="primary" onClick={()=>{setCreating(true);setIntroduction('');setImage(null)}}><Plus size={16}/>新建商品</button>}</header>
    {error&&<p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
    {loading?<div className="panel flex items-center gap-2 p-6"><Loader2 className="animate-spin" size={18}/>加载中…</div>:productId?product&&<>
      <section className="panel p-5">{editing?<form onSubmit={save} className="space-y-4">
        <h2 className="font-medium">编辑商品</h2>
        <label className="field"><span>产品图片（不更换可留空）</span><input type="file" accept=".jpg,.jpeg,.png,.webp,.gif,image/*" onChange={event=>setImage(event.target.files?.[0]||null)}/></label>
        <label className="field"><span>产品介绍 *</span><textarea className="input min-h-32" maxLength={10000} value={introduction} onChange={event=>setIntroduction(event.target.value)}/></label>
        <div className="flex gap-2"><button className="primary" disabled={busy||!introduction.trim()}>保存</button><button type="button" className="btn-secondary" onClick={()=>{setEditing(false);setIntroduction(product.introduction);setImage(null)}}>取消</button></div>
      </form>:<div className="flex flex-col gap-5 sm:flex-row"><img src={product.image_url} alt="产品图片" className="h-44 w-44 rounded object-cover border border-[var(--border)]"/><div className="min-w-0 flex-1"><h2 className="font-medium">产品介绍</h2><p className="mt-2 whitespace-pre-wrap break-words text-sm">{product.introduction}</p><button type="button" className="btn-secondary mt-4" onClick={()=>setEditing(true)}>编辑商品</button></div></div>}</section>
      <div className="flex flex-wrap gap-3"><Link className="btn-secondary" to={`/material-library/${product.id}`}>查看商品素材</Link><Link className="btn-secondary" to={`/product-finished-library/${product.id}`}>查看商品成品</Link></div>
    </>:<>
      {creating&&<form className="panel space-y-4 p-6" onSubmit={save}><h2 className="font-medium">新建商品</h2>
        <label className="field"><span>产品图片 *</span><input type="file" accept=".jpg,.jpeg,.png,.webp,.gif,image/*" required onChange={event=>setImage(event.target.files?.[0]||null)}/></label>
        <label className="field"><span>产品介绍 *</span><textarea className="input min-h-32" maxLength={10000} required value={introduction} onChange={event=>setIntroduction(event.target.value)} placeholder="填写产品介绍"/></label>
        <div className="flex gap-2"><button className="primary" disabled={busy||!image||!introduction.trim()}>{busy?<Loader2 size={16} className="animate-spin"/>:<ImagePlus size={16}/>}创建商品</button><button className="btn-secondary" type="button" onClick={()=>setCreating(false)}>取消</button></div>
      </form>}
      {!products.length&&!creating?<div className="panel p-10 text-center text-sm text-[var(--muted)]">暂无商品，点击“新建商品”开始建立档案。</div>:<div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{products.map(row=><Link key={row.id} to={`/ecommerce-products/${row.id}`} className="panel overflow-hidden transition-shadow hover:shadow-md"><img src={row.image_url} alt="产品图片" className="h-48 w-full object-cover"/><div className="p-4"><p className="line-clamp-2 whitespace-pre-wrap break-words text-sm">{row.introduction}</p><span className="mt-2 block text-xs text-[var(--muted)]">管理商品 →</span></div></Link>)}</div>}
    </>}
  </div>;
}
