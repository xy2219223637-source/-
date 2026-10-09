import {useCallback,useEffect,useState} from 'react';
import {ArrowRight,Loader2,Wrench} from 'lucide-react';
import {Link} from 'react-router-dom';
import {mediaUrl,videoRepairApi} from '../api/client';
import type {VideoRepairTask} from '../api/client';
import {VIDEO_REPAIR_STATUS_LABELS} from '../components/videoRepairStatus';

const FILTERS=[['','全部'],['AWAITING_MANUAL_DECISION','待人工决策'],['AWAITING_MATERIAL','待补素材'],['CONFIGURING','配置中'],['PREVIEW_READY','预览待确认'],['COMPLETED','已完成'],['FAILED','失败']];

export default function VideoRepairProjects(){
  const [status,setStatus]=useState('');
  const [tasks,setTasks]=useState<VideoRepairTask[]>([]);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const load=useCallback(async()=>{setLoading(true);setError('');try{setTasks((await videoRepairApi.list(status?{status}:{})).items)}catch{setError('修复任务加载失败')}finally{setLoading(false)}},[status]);
  useEffect(()=>{void load()},[load]);
  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><div className="mb-2 text-xs tracking-[.18em] text-[var(--muted)]">VIDEO REPAIR</div><h1 className="font-display text-4xl">审核失败修改</h1><p className="mt-2 text-sm text-[var(--muted)]">查看已有修改任务。</p></div></header>
    <div className="flex flex-wrap gap-2">{FILTERS.map(([value,label])=><button key={value||'all'} type="button" onClick={()=>setStatus(value)} className={status===value?'primary':'btn-secondary'}>{label}</button>)}</div>
    {error&&<p role="alert" className="rounded border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
    {loading?<div className="grid min-h-56 place-items-center"><Loader2 className="animate-spin"/></div>:tasks.length?<div className="grid gap-4 lg:grid-cols-2">{tasks.map(task=><Link key={task.id} to={`/video-repair/${task.id}`} className="panel group grid grid-cols-[8rem_1fr] gap-4 p-4 transition hover:border-violet-300">
      <video className="aspect-video w-full rounded bg-black object-cover" src={mediaUrl(task.source_asset.object_url)} preload="metadata"/>
      <div className="min-w-0"><div className="flex items-center justify-between gap-2"><span className="rounded-full bg-slate-100 px-2 py-1 text-xs">{VIDEO_REPAIR_STATUS_LABELS[task.status]||task.status}</span><ArrowRight size={16} className="text-[var(--muted)] group-hover:text-violet-600"/></div><h2 className="mt-2 truncate font-medium">{task.source_asset.filename}</h2><p className="mt-1 line-clamp-2 text-sm text-[var(--muted)]">{task.input.raw_reason}</p><p className="mt-2 text-xs text-[var(--muted)]">{new Date(task.created_at).toLocaleString('zh-CN')} · {task.repair_plan.primary_action||'待分析'}</p>{task.output_asset&&<p className="mt-1 truncate text-xs text-emerald-700">V2：{task.output_asset.filename}</p>}</div>
    </Link>)}</div>:<section className="panel grid min-h-64 place-items-center p-8 text-center"><div><Wrench className="mx-auto text-[var(--muted)]"/><h2 className="mt-3 font-medium">还没有修改任务</h2><p className="mt-2 text-sm text-[var(--muted)]">从成片库选择一条成片，填写审核或检查中发现的问题。</p><Link className="btn-secondary mt-4" to="/final-videos">前往成片库</Link></div></section>}
  </div>;
}
