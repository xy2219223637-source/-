import {useEffect,useState} from 'react';
import {Link,useNavigate} from 'react-router-dom';
import {ArrowRight,Clapperboard,Clock3,Loader2,Play,Sparkles} from 'lucide-react';
import {autoEditApi,productApi,videoLibraryApi} from '../api/client';
import type {AutoEditProject,Product,VideoLibraryAsset} from '../types';

type EditMode='smart_compose'|'finished_remix';
type Props={editMode:EditMode;title:string;description:string};

export default function PerformanceVideoProjectsPage({editMode,title,description}:Props){
  const navigate=useNavigate(); const [products,setProducts]=useState<Product[]>([]); const [projects,setProjects]=useState<AutoEditProject[]>([]);
  const [libraryAssets,setLibraryAssets]=useState<VideoLibraryAsset[]>([]);
  const [productId,setProductId]=useState(''); const [duration,setDuration]=useState(90); const [count,setCount]=useState(3);
  const [objective,setObjective]=useState('live_stream'); const [constraints,setConstraints]=useState(''); const [saving,setSaving]=useState(false);
  const [productsLoading,setProductsLoading]=useState(true); const [productError,setProductError]=useState(''); const [projectError,setProjectError]=useState('');
  const remix=editMode==='finished_remix';
  const listPath=remix?'/finished-video-remix':'/smart-video-compose';
  const libraryPath=remix?'/finished-video-assets':'/semi-finished-assets';
  const libraryType=remix?'finished' as const:'semi_finished' as const;
  useEffect(()=>{
    productApi.list().then(ps=>{setProducts(ps);setProductId(current=>current||String(ps[0]?.id||''));setProductError('')}).catch(()=>setProductError('商品列表加载失败，请刷新页面重试')).finally(()=>setProductsLoading(false));
    autoEditApi.list(editMode).then(rows=>{setProjects(rows);setProjectError('')}).catch(()=>setProjectError('项目暂时无法加载，请确认后端已重启并完成数据迁移'));
  },[editMode]);
  useEffect(()=>{if(productId)videoLibraryApi.list(Number(productId),libraryType).then(setLibraryAssets).catch(()=>setLibraryAssets([]));else setLibraryAssets([])},[productId,libraryType]);
  async function create(){if(!productId)return;setSaving(true);try{const row=await autoEditApi.create({product_id:Number(productId),target_duration_seconds:duration,variant_count:count,aspect_ratio:'9:16',objective,edit_mode:editMode,constraints:constraints.split('\n').map(x=>x.trim()).filter(Boolean)});navigate(`${listPath}/${row.id}`);}finally{setSaving(false)}}
  const usable=libraryAssets.filter(x=>x.status==='analyzed'&&!x.excluded&&(!remix||(x.reusable_status==='reusable'&&x.generation_depth<=1)));
  const steps=remix?['成品分析','完整语义段切分','营销框架','全局选段与衔接评分','音频统一','渲染多版本']:['上传半成品','原声与画面分析','生成成片方案','校验时间线','渲染并入库成品'];
  return <div className="space-y-8 animate-fade-in max-w-7xl mx-auto">
    <header><div className="text-xs tracking-[.18em] text-[var(--muted)] mb-2">{remix?'FINISHED VIDEO REMIX':'SMART VIDEO COMPOSE'}</div><h1 className="font-display text-4xl">{title}</h1><p className="mt-2 text-sm text-[var(--muted)]">{description}</p></header>
    <section className="panel p-4"><div className="grid sm:grid-cols-5 gap-2">{steps.map((x,i)=><div key={x} className="border border-[var(--border)] bg-[#f6f5f2] p-3"><span className="text-[10px] text-blue-600">{String(i+1).padStart(2,'0')}</span><div className="text-xs font-medium mt-2">{x}</div></div>)}</div></section>
    <section className="panel overflow-hidden"><div className="px-6 py-4 bg-black text-white flex gap-3"><Clapperboard/><div><div className="font-medium">{remix?'新建成品混剪项目':'新建智能成片项目'}</div><div className="text-xs text-white/60 mt-1">{remix?'只读取已审核可复用、代次不超过 1 的成品素材':'只读取半成品素材库中已分析的原声和辅助画面'}</div></div></div><div className="p-6 lg:p-8 space-y-5">
      <div className="grid sm:grid-cols-3 gap-4"><label className="field"><span>商品</span><select className="input-select" value={productId} disabled={productsLoading||!products.length} onChange={e=>setProductId(e.target.value)}><option value="">{productsLoading?'正在加载商品…':products.length?'请选择商品':'商品管理中暂无商品'}</option>{products.map(p=><option key={p.id} value={p.id}>{p.name}{p.brand_name?` · ${p.brand_name}`:''}</option>)}</select>{productError&&<small className="text-red-600 mt-1">{productError}</small>}{!productsLoading&&!productError&&!products.length&&<small className="text-[var(--muted)] mt-1">请先到商品管理创建商品</small>}</label><label className="field"><span>目标时长</span><select className="input-select" value={duration} onChange={e=>setDuration(Number(e.target.value))}>{[60,90,120].map(x=><option key={x} value={x}>{x} 秒</option>)}</select><small className="text-[var(--muted)] mt-1">素材不足时会合理缩短，不靠 TTS 凑时长</small></label><label className="field"><span>成片版本</span><select className="input-select" value={count} onChange={e=>setCount(Number(e.target.value))}>{[1,3,4,5].map(x=><option key={x} value={x}>{x} 个版本</option>)}</select></label></div>
      {productId&&<div className="bg-blue-50 border border-blue-200 p-3 text-sm text-blue-900">{remix?'成品素材库':'半成品素材库'}已找到 {libraryAssets.length} 条视频，其中 {usable.length} 条{remix?'可混剪':'可用于成片'}。{!libraryAssets.length&&<Link className="underline ml-1" to={libraryPath}>前往上传</Link>}</div>}
      <div className="field"><span>成片目标</span><div className="grid sm:grid-cols-3 gap-3">{[{value:'live_stream',title:'直播间引流（默认）',note:'固定结构推进，结尾引导进入直播间，不使用小黄车话术'},{value:'seeding',title:'内容种草',note:'重视真实体验与信任建立，柔和带出卖点'},{value:'cart_conversion',title:'小黄车成交',note:'强化利益点和购买行动，仅此模式使用直接下单话术'}].map(item=><button type="button" key={item.value} onClick={()=>setObjective(item.value)} className={`text-left border p-4 ${objective===item.value?'border-blue-600 bg-blue-50':'border-[var(--border)] bg-white'}`}><b className="text-sm">{item.title}</b><div className="text-xs text-[var(--muted)] mt-1 leading-5">{item.note}</div></button>)}</div></div><label className="field"><span>额外约束（每行一条）</span><textarea rows={3} value={constraints} onChange={e=>setConstraints(e.target.value)} placeholder="例如：商品必须在前 5 秒出现"/></label>
      <button className="btn-primary" onClick={create} disabled={!productId||saving}>{saving?<Loader2 size={16} className="animate-spin"/>:<Sparkles size={16}/>}{remix?'创建成品混剪':'创建智能成片'}<ArrowRight size={16}/></button>
    </div></section>
    <section><div className="flex justify-between mb-4"><h2 className="text-lg font-medium">{remix?'成品混剪项目':'智能成片项目'}</h2><span className="text-xs text-[var(--muted)]">{projects.length} 个项目</span></div>{projectError&&<div className="border border-amber-200 bg-amber-50 text-amber-800 px-4 py-3 text-sm mb-4">{projectError}</div>}<div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">{projects.map(p=><Link key={p.id} to={`${listPath}/${p.id}`} className="panel p-5 hover:border-black"><b>{p.name}</b><div className="text-xs text-[var(--muted)] mt-2">{p.assets.length} 条素材 · {p.variant_count} 个版本 · {p.target_duration_seconds} 秒</div><div className="mt-4 text-xs flex items-center gap-2"><Play size={14}/>{p.status}</div></Link>)}{!projects.length&&!projectError&&<div className="panel p-10 sm:col-span-2 xl:col-span-3 text-center text-sm text-[var(--muted)]"><Clock3 className="mx-auto mb-2"/>{remix?'还没有成品混剪项目':'还没有智能成片项目'}</div>}</div></section>
  </div>
}
