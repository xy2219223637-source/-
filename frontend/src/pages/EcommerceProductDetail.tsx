import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Loader2,
  Trash2,
  Image as ImageIcon,
  Upload,
  FileText,
  Pencil,
  Save,
  X,
} from 'lucide-react';
import { mediaUrl, ecommerceProductApi } from '../api/client';
import type { Product, ProductAsset, ProductCreate } from '../types';

export default function ProductDetail() {
  const { id } = useParams();
  const productId = Number(id);
  const navigate = useNavigate();

  const [product, setProduct] = useState<Product | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [assets, setAssets] = useState<ProductAsset[]>([]);
  const [assetType, setAssetType] = useState('product_image');
  const [assetDescription, setAssetDescription] = useState('');
  const [uploadingAsset, setUploadingAsset] = useState(false);
  const [readiness, setReadiness] = useState<{score:number;status:string;items:Array<{key:string;label:string;complete:boolean;required:boolean}>;missing_required:string[]} | null>(null);
  const [facts,setFacts]=useState<Array<{id:number;fact_key:string;label:string;value:string;source_type:string;source_ref:string;status:string;conflict_values:string[]}>>([]);
  const [admission,setAdmission]=useState<{status:string;missing_roles:string[];images:Array<{label:string;inspection:{status:string;score:number;issues:string[]}}> }|null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<Partial<ProductCreate>>({});

  async function load() {
    const [p, a, ready, factRows, admissionRow] = await Promise.all([
      ecommerceProductApi.get(productId),
      ecommerceProductApi.listAssets(productId),
      ecommerceProductApi.readiness(productId),
      ecommerceProductApi.facts(productId),
      ecommerceProductApi.imageAdmission(productId),
    ]);
    setProduct(p);
    setAssets(a);
    setReadiness(ready);
    setFacts(factRows);
    setAdmission(admissionRow);
  }

  useEffect(() => {
    if (!productId) return;
    (async () => {
      try {
        await load();
      } catch (e) {
        console.error(e);
        setError('加载失败');
      } finally {
        setLoading(false);
      }
    })();
  }, [productId]);

  async function handleDelete() {
    if (!confirm('确定删除该电商商品及其素材？')) return;
    await ecommerceProductApi.remove(productId);
    navigate('/ecommerce-products');
  }

  function beginEdit() {
    setDraft({
      name: product.name, category: product.category, price: product.price,
      description: product.description, target_users: product.target_users,
      brand_name: product.brand_name, ingredients: product.ingredients,
      usage_method: product.usage_method, specifications: product.specifications,
    });
    setEditing(true);
    setError('');
    setSuccess('');
    requestAnimationFrame(() => document.getElementById('basic-info-editor')?.scrollIntoView({behavior:'smooth', block:'start'}));
  }

  async function saveBasicInfo() {
    if (!String(draft.name || '').trim()) { setError('商品名称不能为空'); return; }
    setSaving(true); setError('');
    try {
      await ecommerceProductApi.update(productId, {
        name:String(draft.name).trim(), category:String(draft.category||''), price:Number(draft.price)||0,
        description:String(draft.description||''), target_users:String(draft.target_users||''),
        brand_name:String(draft.brand_name||''), ingredients:String(draft.ingredients||''),
        usage_method:String(draft.usage_method||''), specifications:String(draft.specifications||''),
      });
      await load(); setEditing(false); setSuccess('商品基本资料已保存');
    } catch (e) {
      console.error(e); setError('商品资料保存失败');
    } finally { setSaving(false); }
  }

  async function uploadAssets(files: FileList | null) {
    if (!files?.length) return;
    setUploadingAsset(true);
    setError('');
    setSuccess('');
    try {
      await ecommerceProductApi.uploadAssets(productId, Array.from(files), {
        asset_type: assetType,
        description: assetDescription,
      });
      await load();
      setAssetDescription('');
      setSuccess(`已新增 ${files.length} 个素材`);
    } catch (e) {
      console.error(e);
      setError('素材上传失败');
    } finally {
      setUploadingAsset(false);
    }
  }

  async function removeAsset(asset: ProductAsset) {
    if (asset.product_id !== productId) { setError('企业共享素材请在其所属位置管理'); return; }
    if (!confirm(`确定删除素材“${asset.name}”？删除后无法恢复。`)) return;
    setError(''); setSuccess('');
    try {
      await ecommerceProductApi.removeAsset(productId, asset.id);
      await load();
      setSuccess('素材已删除');
    } catch (e) {
      console.error(e); setError('素材删除失败，请稍后重试');
    }
  }

  async function updateAsset(asset: ProductAsset, values: Partial<ProductAsset>) {
    const next = { ...asset, ...values };
    setAssets((current) => current.map((item) => item.id === asset.id ? next : item));
    try {
      const saved = await ecommerceProductApi.updateAsset(productId, asset.id, { description: next.description, tags: next.tags, material_role: next.material_role, priority: next.priority, locked: next.locked, excluded: next.excluded, benchmark_role: next.benchmark_role, protection:next.protection||{} });
      setAssets((current) => current.map((item) => item.id === saved.id ? saved : item));
    } catch (e) {
      console.error(e); setError(`${asset.name}设置保存失败`); setAssets((current) => current.map((item) => item.id === asset.id ? asset : item));
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-[var(--muted)]">
        <Loader2 className="animate-spin mr-2" size={20} /> 加载中…
      </div>
    );
  }

  if (!product) {
    return <div className="text-[var(--muted)]">商品不存在</div>;
  }

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex items-center gap-3 text-sm text-[var(--muted)]">
        <Link to="/ecommerce-products" className="inline-flex items-center gap-1 hover:text-[var(--text)]">
          <ArrowLeft size={16} /> 返回电商商品管理
        </Link>
      </div>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4 min-w-0">
          {product.image_urls?.[0] ? <img src={mediaUrl(product.image_urls[0])} alt="商品封面" className="w-20 h-20 rounded-xl object-cover border border-[var(--border)] bg-white shrink-0"/> : <div className="w-20 h-20 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border)] grid place-items-center text-[var(--muted)] shrink-0"><ImageIcon size={22}/></div>}
          <div className="min-w-0">
            <div className="text-[10px] text-[var(--muted)] mb-1">{product.image_urls?.[0] ? '当前商品封面' : '暂未设置商品封面'}</div>
            <h1 className="font-display text-3xl tracking-tight truncate">{product.name}</h1>
            <p className="mt-1 text-sm text-[var(--muted)]">{product.category || '未分类'} · ¥{product.price} · 目标用户：{product.target_users || '未指定'}</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={beginEdit}><Pencil size={15} />编辑基本资料</button>
          <button className="btn-danger inline-flex items-center gap-2" onClick={handleDelete}>
            <Trash2 size={16} /> 删除
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 text-red-700 px-4 py-3 text-sm">
          {error}
        </div>
      )}
      {success && <div className="rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-700 px-4 py-3 text-sm">{success}</div>}
      {editing && <section id="basic-info-editor" className="panel p-5 space-y-4 scroll-mt-6">
        <div className="flex items-center justify-between"><div><h2 className="font-medium">编辑基本资料</h2><p className="text-xs text-[var(--muted)] mt-1">保存后会更新当前企业的云端商品资料。</p></div><button className="btn-secondary" onClick={()=>setEditing(false)} disabled={saving}><X size={14}/>取消</button></div>
        <div className="grid sm:grid-cols-2 gap-4">
          <label className="field"><span>商品名称 *</span><input value={String(draft.name||'')} onChange={e=>setDraft({...draft,name:e.target.value})}/></label>
          <label className="field"><span>商品类别</span><input value={String(draft.category||'')} onChange={e=>setDraft({...draft,category:e.target.value})}/></label>
          <label className="field"><span>品牌名称</span><input value={String(draft.brand_name||'')} onChange={e=>setDraft({...draft,brand_name:e.target.value})}/></label>
          <label className="field"><span>价格（元）</span><input type="number" min="0" step="0.01" value={Number(draft.price||0)} onChange={e=>setDraft({...draft,price:Number(e.target.value)})}/></label>
          <label className="field"><span>目标用户</span><input value={String(draft.target_users||'')} onChange={e=>setDraft({...draft,target_users:e.target.value})}/></label>
        </div>
        <label className="field"><span>商品描述</span><textarea rows={4} value={String(draft.description||'')} onChange={e=>setDraft({...draft,description:e.target.value})}/></label>
        <div className="grid sm:grid-cols-2 gap-4">
          <label className="field"><span>核心成分 / 配方</span><textarea rows={3} value={String(draft.ingredients||'')} onChange={e=>setDraft({...draft,ingredients:e.target.value})}/></label>
          <label className="field"><span>使用方法</span><textarea rows={3} value={String(draft.usage_method||'')} onChange={e=>setDraft({...draft,usage_method:e.target.value})}/></label>
        </div>
        <label className="field"><span>规格信息</span><textarea rows={3} value={String(draft.specifications||'')} onChange={e=>setDraft({...draft,specifications:e.target.value})}/></label>
        <div className="flex justify-end"><button className="btn-primary" onClick={saveBasicInfo} disabled={saving}>{saving?<Loader2 size={15} className="animate-spin"/>:<Save size={15}/>}保存修改</button></div>
      </section>}

      {readiness && <section className={`panel p-4 ${readiness.status === 'ready' ? 'border-emerald-200' : 'border-amber-300'}`}><div className="flex items-center justify-between"><div><div className="font-medium">商品资料完整度</div><div className="text-xs text-[var(--muted)] mt-1">必填资料完成 {readiness.score}%</div></div><span className={`text-sm font-medium ${readiness.status === 'ready' ? 'text-emerald-700' : 'text-amber-700'}`}>{readiness.status === 'ready' ? '资料齐全' : '建议补充'}</span></div><div className="flex flex-wrap gap-2 mt-3">{readiness.items.map(item=><span key={item.key} className={`rounded-full px-2.5 py-1 text-xs ${item.complete?'bg-emerald-50 text-emerald-800':'bg-amber-50 text-amber-800'}`}>{item.complete?'✓':'!'} {item.label}{item.required?' · 必填':''}</span>)}</div></section>}
      {admission&&<section className="panel p-4"><div className="font-medium">商品原图准入检查</div><div className="grid md:grid-cols-3 gap-2 mt-3">{admission.images.map((x,i)=><div className={`rounded-lg p-3 text-xs ${x.inspection.status==='passed'?'bg-emerald-50':'bg-amber-50'}`} key={i}><b>{x.label} · {x.inspection.score}分</b><div className="mt-1">{x.inspection.issues.join('、')||'清晰度和画幅通过'}</div></div>)}</div>{admission.missing_roles.length>0&&<div className="text-xs text-amber-800 mt-3">建议补充基准图：{admission.missing_roles.join('、')}</div>}</section>}
      <section className="panel p-5"><div className="flex justify-between"><div><h2 className="font-medium">商品事实中心</h2><p className="text-xs text-[var(--muted)] mt-1">请确认商品事实；冲突内容需人工选择。</p></div></div><div className="grid md:grid-cols-2 gap-3 mt-4">{facts.map(f=><div key={f.id} className={`rounded-xl border p-4 ${f.status==='confirmed'?'border-emerald-200':f.status==='conflict'?'border-red-300 bg-red-50/30':'border-amber-200'}`}><div className="flex justify-between"><b className="text-sm">{f.label}</b><span className="text-xs">{f.status==='confirmed'?'已确认':f.status==='conflict'?'存在冲突':'待确认'}</span></div><p className="text-sm mt-2 whitespace-pre-wrap">{f.value||'尚未填写'}</p><div className="text-[10px] text-[var(--muted)] mt-2">来源：{f.source_type} {f.source_ref}</div>{f.conflict_values?.length>0&&<div className="mt-2 text-xs text-red-700">冲突值：{f.conflict_values.join(' / ')}</div>}<div className="flex gap-2 mt-3"><button className="btn-secondary text-xs" onClick={async()=>{const value=prompt(`修改${f.label}`,f.value);if(value===null)return;await ecommerceProductApi.saveFact(productId,{fact_key:f.fact_key,label:f.label,value,source_type:'manual',source_ref:'人工编辑'});setFacts(await ecommerceProductApi.facts(productId))}}>编辑</button>{f.status!=='confirmed'&&<button className="btn-primary text-xs" onClick={async()=>{await ecommerceProductApi.confirmFact(productId,f.id,f.value);setFacts(await ecommerceProductApi.facts(productId))}}>确认作为正式事实</button>}</div></div>)}</div></section>

      <div>
        <div className="panel p-6 space-y-4">
          <div className="flex items-center justify-between"><h2 className="font-medium">商品描述</h2><button className="btn-secondary text-xs" onClick={beginEdit}><Pencil size={13}/>编辑商品资料</button></div>
          <p className="text-sm leading-relaxed whitespace-pre-wrap text-[var(--text)]/90">
            {product.description || '暂无描述'}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            <InfoBlock label="品牌" value={product.brand_name} />
            <InfoBlock label="规格" value={product.specifications} />
            <InfoBlock label="核心成分" value={product.ingredients} />
            <InfoBlock label="使用方法" value={product.usage_method} />
          </div>

          <div id="material-library" className="border-t border-[var(--border)] pt-4 space-y-3 scroll-mt-6">
            <h2 className="font-medium flex items-center gap-2"><FileText size={16} /> 商品 / 品牌素材库</h2>
            <div className="grid grid-cols-1 sm:grid-cols-[180px_1fr_auto] gap-2">
              <select className="input-select" value={assetType} onChange={(e) => setAssetType(e.target.value)}>
                <option value="product_image">产品图片</option>
                <option value="brand_asset">品牌素材</option>
                <option value="certificate">证书</option>
                <option value="test_report">检测报告</option>
                <option value="design_skill">设计方法论 / Skill</option>
              </select>
              <input className="input" value={assetDescription} onChange={(e) => setAssetDescription(e.target.value)} placeholder="素材说明，填写素材说明" />
              <label className="btn-primary inline-flex items-center justify-center gap-2 cursor-pointer">
                {uploadingAsset ? <Loader2 className="animate-spin" size={14} /> : <Upload size={14} />}
                上传
                <input type="file" multiple className="hidden" onChange={(e) => {uploadAssets(e.target.files);e.currentTarget.value='';}} />
              </label>
            </div>
            <p className="text-xs text-[var(--muted)]">可设置素材角色、优先级、锁定及排除状态。</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {assets.length === 0 && <div className="sm:col-span-2 rounded-xl border border-dashed border-[var(--border)] p-8 text-center text-sm text-[var(--muted)]">暂未上传素材；后续测试时可在这里直接上传并设置角色。</div>}
              {assets.map((asset) => (
                <div key={asset.id} className={`rounded-xl border p-3 ${asset.excluded ? 'border-red-200 bg-red-50/50 opacity-70' : asset.locked ? 'border-emerald-300 bg-emerald-50/40' : 'border-[var(--border)]'}`}>
                  <div className="flex gap-3">{asset.mime_type.startsWith('image/') ? <a href={mediaUrl(asset.file_url)} target="_blank" rel="noreferrer"><img src={mediaUrl(asset.file_url)} className="w-20 h-20 rounded-lg object-cover border border-[var(--border)]" /></a> : <div className="w-20 h-20 rounded-lg bg-[#f1efe9] grid place-items-center"><FileText size={20} /></div>}<div className="min-w-0 flex-1"><div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="text-sm font-medium truncate">{asset.name}</div>{asset.file_url===product.image_urls?.[0]&&<span className="inline-block mt-1 rounded-full bg-[var(--accent-soft)] text-[var(--accent)] px-2 py-0.5 text-[10px]">当前封面</span>}</div>{asset.product_id===productId?<button className="text-red-700 hover:text-red-900 shrink-0" title="删除素材" onClick={()=>removeAsset(asset)}><Trash2 size={14}/></button>:<span className="text-[10px] text-[var(--muted)] shrink-0">企业共享</span>}</div><div className="text-[10px] text-[var(--muted)] mt-1">{asset.description || '无说明'}</div><select className="input-select w-full mt-2 text-xs" value={asset.material_role || 'auto'} onChange={(event) => updateAsset(asset, { material_role: event.target.value })}><option value="auto">AI 自动识别</option><option value="product">商品正面图</option><option value="package">套装/礼盒图</option><option value="detail">侧面/细节图</option><option value="texture">质地微距</option><option value="scenario">使用场景</option><option value="ingredient">成分素材</option><option value="logo">品牌 Logo</option><option value="brand">品牌视觉素材</option><option value="reference">设计参考图</option></select></div></div>
                  <div className="flex items-center justify-between mt-3 text-xs"><label className="flex items-center gap-1.5"><input type="checkbox" checked={asset.locked} disabled={asset.excluded} onChange={(event) => updateAsset(asset, { locked: event.target.checked })} />锁定使用</label><label className="flex items-center gap-1.5 text-red-700"><input type="checkbox" checked={asset.excluded} onChange={(event) => updateAsset(asset, { excluded: event.target.checked, locked: event.target.checked ? false : asset.locked })} />排除素材</label><label className="flex items-center gap-1">优先级<input type="number" min="0" max="9" className="w-12 rounded border border-[var(--border)] px-1 py-0.5" value={asset.priority} onChange={(event) => updateAsset(asset, { priority: Number(event.target.value) })} /></label></div>
                  {asset.mime_type.startsWith('image/') && <label className="block text-xs mt-3">质检基准用途<select className="input-select w-full mt-1" value={asset.benchmark_role || 'none'} onChange={(event)=>updateAsset(asset,{benchmark_role:event.target.value})}><option value="none">不是基准图</option><option value="product_front">商品正面标准图</option><option value="package">包装标准图</option><option value="set_composition">套装组成标准图</option><option value="color">包装颜色标准图</option></select></label>}
                </div>
              ))}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-[var(--bg-elevated)] p-3">
      <div className="text-xs text-[var(--muted)] mb-1">{label}</div>
      <div className="whitespace-pre-wrap">{value || '未填写'}</div>
    </div>
  );
}
