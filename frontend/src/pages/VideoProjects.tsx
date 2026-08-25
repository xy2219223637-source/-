import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowRight, Check, Clock3, Loader2, Play, Sparkles, Video } from 'lucide-react';
import { mediaUrl, productApi, videoApi } from '../api/client';
import type { Product, VideoProject } from '../types';

const platforms = ['小红书', '抖音', '视频号'];
const videoTypes = [
  { value: '博主种草', note: '亲切口播为主，穿插商品特写和试用体验' },
  { value: '专业测评', note: '重点讲成分、质地和使用方式，增加试用镜头' },
  { value: '高级广告', note: '商品特写、光影和品牌氛围为主，表达更克制' },
  { value: '日常分享', note: '生活化场景和真实体验为主，降低广告感' },
];

export default function VideoProjectsPage() {
  const navigate = useNavigate(); const [params] = useSearchParams();
  const [products, setProducts] = useState<Product[]>([]); const [projects, setProjects] = useState<VideoProject[]>([]);
  const [productId, setProductId] = useState(''); const [duration, setDuration] = useState(20);
  const [platform, setPlatform] = useState('小红书'); const [videoType, setVideoType] = useState('博主种草');
  const [voice, setVoice] = useState('自然分享女声');
  const [points, setPoints] = useState(''); const [extra, setExtra] = useState(''); const [saving, setSaving] = useState(false);
  useEffect(() => { Promise.all([productApi.list(), videoApi.list()]).then(([ps, vs]) => { setProducts(ps); setProjects(vs); const wanted=params.get('product'); setProductId(wanted && ps.some(p=>String(p.id)===wanted) ? wanted : String(ps[0]?.id || '')); }); }, []);
  const product = useMemo(() => products.find(p => String(p.id) === productId), [products, productId]);
  async function create() { if (!product) return; setSaving(true); try { const row = await videoApi.create({product_id:product.id,duration_seconds:duration,platform,video_type:videoType,voice,selling_points:points,extra_requirements:extra}); navigate(`/video-projects/${row.id}?new=1`); } finally { setSaving(false); } }
  return <div className="space-y-8 animate-fade-in max-w-7xl mx-auto">
    <header className="flex items-end justify-between gap-5"><div><div className="text-xs tracking-[.18em] text-[var(--muted)] mb-2">AI TALKING VIDEO</div><h1 className="font-display text-4xl">AI 口播视频</h1><p className="mt-2 text-sm text-[var(--muted)]">选择商品，填写简单需求，自动完成文案、分镜与竖屏视频编排。</p></div><div className="hidden md:flex gap-2 text-xs text-[var(--muted)]"><span className="px-3 py-2 border border-[var(--border)]"><Check size={13} className="inline mr-1"/>商品资料复用</span><span className="px-3 py-2 border border-[var(--border)]"><Video size={13} className="inline mr-1"/>9:16 竖屏</span></div></header>
    <section className="panel grid lg:grid-cols-[300px_1fr] overflow-hidden">
      <aside className="p-6 bg-[#f1f0ed] border-r border-[var(--border)]"><div className="text-xs font-medium text-[var(--muted)] mb-3">01 · 选择已有商品</div><select className="input-select" value={productId} onChange={e=>setProductId(e.target.value)}>{products.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select>{product && <div className="mt-5 bg-white border border-[var(--border)]"><div className="aspect-square grid place-items-center bg-white">{product.image_urls[0] ? <img src={mediaUrl(product.image_urls[0])} className="w-full h-full object-contain"/> : <Video className="text-[var(--muted)]"/>}</div><div className="p-4 border-t border-[var(--border)]"><div className="font-medium">{product.name}</div><div className="text-xs text-[var(--muted)] mt-1">{product.brand_name || '未设置品牌'} · {product.category || '未分类'}</div></div></div>}</aside>
      <div className="p-6 lg:p-8 space-y-6"><div className="text-xs font-medium text-[var(--muted)]">02 · 设置视频需求</div>
        <div className="grid sm:grid-cols-2 gap-5"><label className="field"><span>目标平台</span><select className="input-select" value={platform} onChange={e=>setPlatform(e.target.value)}>{platforms.map(x=><option key={x}>{x}</option>)}</select></label><label className="field"><span>视频时长</span><div className="grid grid-cols-3 gap-2">{[15,20,30].map(x=><button key={x} onClick={()=>setDuration(x)} className={`py-2.5 text-sm border ${duration===x?'bg-black text-white border-black':'bg-white border-[var(--border)]'}`}>{x} 秒</button>)}</div></label><label className="field"><span>口播声音</span><select className="input-select" value={voice} onChange={e=>setVoice(e.target.value)}><option>自然分享女声</option><option>温柔精致女声</option><option>专业测评女声</option><option>清爽自然男声</option></select></label></div>
        <div><div className="text-xs text-[var(--muted)] mb-2">视频类型</div><div className="grid sm:grid-cols-2 gap-2">{videoTypes.map(item=><button type="button" key={item.value} onClick={()=>setVideoType(item.value)} className={`text-left border p-3 transition-colors ${videoType===item.value?'border-black bg-black text-white':'border-[var(--border)] bg-white'}`}><span className="text-sm font-medium block">{item.value}</span><span className={`text-xs mt-1 block leading-5 ${videoType===item.value?'text-white/70':'text-[var(--muted)]'}`}>{item.note}</span></button>)}</div><p className="text-xs text-[var(--muted)] mt-2">系统会根据视频类型自动决定文案语气，以及人物口播、商品展示和产品试用的镜头比例。</p></div>
        <label className="field"><span>核心卖点（不填则由 AI 从商品资料提取）</span><textarea rows={3} value={points} onChange={e=>setPoints(e.target.value)} placeholder="例如：轻薄服帖、自然水光感、适合日常通勤"/></label><label className="field"><span>补充要求（可不填）</span><textarea rows={3} value={extra} onChange={e=>setExtra(e.target.value)} placeholder="例如：开场更抓人，整体不要太像硬广"/></label>
        <div className="bg-[#f6f5f2] border border-[var(--border)] p-4 text-xs text-[var(--muted)] leading-6">系统会自动读取当前商品的图片、事实资料、品牌视觉和知识库内容；不会修改这些原始资料。</div><button className="btn-primary w-full justify-center py-3" onClick={create} disabled={!product||saving}>{saving?<Loader2 size={16} className="animate-spin"/>:<Sparkles size={16}/>}AI 生成视频方案<ArrowRight size={16}/></button>
      </div>
    </section>
    <section><div className="flex justify-between mb-4"><div><h2 className="text-lg font-medium">历史视频</h2><p className="text-xs text-[var(--muted)] mt-1">视频项目独立保存，不影响原来的图片创作项目</p></div><span className="text-xs text-[var(--muted)]">{projects.length} 个项目</span></div><div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">{projects.map(p=><Link key={p.id} to={`/video-projects/${p.id}`} className="panel p-5 group hover:border-black transition-colors"><div className="flex gap-4"><div className="w-16 h-24 bg-[#eee] shrink-0 overflow-hidden">{p.product.image_urls[0]&&<img src={mediaUrl(p.product.image_urls[0])} className="w-full h-full object-cover"/>}</div><div className="min-w-0 flex-1"><div className="font-medium line-clamp-2">{p.name}</div><div className="text-xs text-[var(--muted)] mt-2">{p.platform} · {p.duration_seconds}秒 · {p.style}</div><div className="mt-4 flex items-center justify-between text-xs"><span className={p.status==='preview_ready'?'text-emerald-700':'text-amber-700'}>{p.status==='preview_ready'?'预览已就绪':'方案编辑中'}</span><Play size={15}/></div></div></div></Link>)}{!projects.length&&<div className="panel p-10 sm:col-span-2 xl:col-span-3 text-center text-sm text-[var(--muted)]"><Clock3 className="mx-auto mb-2"/>还没有口播视频项目</div>}</div></section>
  </div>;
}
