import {useCallback,useEffect,useMemo,useState} from 'react';
import {CheckCircle2,Loader2,RotateCcw} from 'lucide-react';
import type {
  QianchuanAccount,QianchuanAwemeOption,QianchuanProductOption,QianchuanUpload,
} from '../api/client';

type DemoStage='ready'|'uploading'|'material_ready'|'creating'|'reviewing'|'approved'|'enabling'|'enabled';
type DemoEvent={stage:DemoStage;label:string;at:string};

export type QianchuanDemoPlan={
  id:string; name:string; advertiser_id:string; advertiser_name:string; material_id:string;
  status:'enabled'; budget:string; roi_goal:string; created_at:string;
};

const STEPS=['上传素材','创建计划','平台审核','启用投放','投放中'];
const PROGRESS:Record<DemoStage,number>={
  ready:0,uploading:0,material_ready:1,creating:1,reviewing:2,approved:3,enabling:3,enabled:5,
};
const ACTIVE_STEP:Record<DemoStage,number>={
  ready:-1,uploading:0,material_ready:1,creating:1,reviewing:2,approved:3,enabling:3,enabled:4,
};
const STATUS:Record<DemoStage,string>={
  ready:'待开始',uploading:'素材上传中',material_ready:'素材已就绪',creating:'正在创建计划',
  reviewing:'平台审核中',approved:'审核通过',enabling:'正在启用投放',enabled:'投放中',
};
const NEXT_STAGE:Partial<Record<DemoStage,{next:DemoStage;label:string;delay:number}>>={
  uploading:{next:'material_ready',label:'Mock 素材上传成功',delay:700},
  material_ready:{next:'creating',label:'开始创建 Mock 商品全域计划',delay:450},
  creating:{next:'reviewing',label:'Mock 计划创建成功，进入平台审核',delay:700},
  reviewing:{next:'approved',label:'Mock 平台审核通过',delay:1200},
  approved:{next:'enabling',label:'开始启用 Mock 计划',delay:500},
  enabling:{next:'enabled',label:'Mock 计划已启用并进入投放中',delay:700},
};

function now(){return new Date().toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'})}

export default function QianchuanPlanDemo({account,aweme,product,upload,catalogMock=false,onCompleted}: {
  account:QianchuanAccount; aweme:QianchuanAwemeOption; product:QianchuanProductOption;
  upload:QianchuanUpload; catalogMock?:boolean; onCompleted?:(plan:QianchuanDemoPlan)=>void;
}){
  const [stage,setStage]=useState<DemoStage>('ready');
  const [name,setName]=useState(`${product.name||'商品'}｜成片全域推广`);
  const [budget,setBudget]=useState('500');
  const [roiGoal,setRoiGoal]=useState('1.50');
  const [planId,setPlanId]=useState('');
  const [events,setEvents]=useState<DemoEvent[]>([]);
  const metrics=useMemo(()=>[
    ['消耗','¥428.60'],['展现','128,430'],['点击率','4.72%'],
    ['成交订单','86'],['成交金额','¥6,972.00'],['实际 ROI','16.27'],
  ],[]);

  const advance=useCallback((next:DemoStage,label:string)=>{
    setStage(next);setEvents(current=>[...current,{stage:next,label,at:now()}]);
  },[]);

  useEffect(()=>{
    const transition=NEXT_STAGE[stage];
    if(!transition)return;
    const timer=window.setTimeout(()=>{
      if(stage==='creating')setPlanId(`MOCK-${Date.now().toString(36).toUpperCase()}`);
      if(transition.next==='enabled')onCompleted?.({
        id:planId,name,advertiser_id:account.advertiser_id,
        advertiser_name:account.advertiser_name||'千川账户',material_id:upload.material_id||'',
        status:'enabled',budget,roi_goal:roiGoal,created_at:new Date().toISOString(),
      });
      advance(transition.next,transition.label);
    },transition.delay);
    return()=>window.clearTimeout(timer);
  },[account.advertiser_id,account.advertiser_name,advance,budget,name,onCompleted,planId,roiGoal,stage,upload.material_id]);

  function start(){
    setPlanId('');setEvents([]);
    advance('uploading','开始 Mock 素材上传流程');
  }
  function reset(){setStage('ready');setPlanId('');setEvents([])}
  const running=stage!=='ready'&&stage!=='enabled';

  return <section aria-label="千川全域计划 Mock 演示" className="mt-5 rounded-lg border-2 border-dashed border-violet-300 bg-violet-50/40 p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div><h3 className="font-medium text-violet-950">单视频创建并投流 · Mock 演示</h3>
        <p className="mt-1 text-xs text-violet-800">一个视频依次完成素材上传、创建商品全域计划、平台审核和启用投放；全流程仅在浏览器模拟，不会重复上传或写入千川。</p></div>
      <span className="rounded-full bg-violet-100 px-3 py-1 text-xs font-medium text-violet-800">DEMO / 非真实投放</span>
    </div>

    <ol aria-label="Mock 投流进度" className="mt-5 grid gap-2 sm:grid-cols-5">
      {STEPS.map((label,index)=>{
        const complete=PROGRESS[stage]>index;
        const active=ACTIVE_STEP[stage]===index;
        return <li key={label} aria-current={active?'step':undefined}
          className={`rounded border px-3 py-2 text-xs ${complete?'border-emerald-300 bg-emerald-50 text-emerald-900':active?'border-violet-400 bg-white text-violet-950':'border-[var(--border)] bg-white/60 text-[var(--muted)]'}`}>
          <span className="mr-2 font-medium">{complete?'✓':index+1}</span>{label}
        </li>;
      })}
    </ol>

    <div className="mt-5 grid gap-4 lg:grid-cols-2">
      <div className="rounded border border-[var(--border)] bg-white p-4 text-sm">
        <h4 className="font-medium">本次投流对象</h4>
        <dl className="mt-3 grid grid-cols-[5rem_1fr] gap-x-3 gap-y-2 text-xs">
          <dt className="text-[var(--muted)]">广告账号</dt><dd>{account.advertiser_name||'千川账户'} · {account.advertiser_id}</dd>
          <dt className="text-[var(--muted)]">抖音号</dt><dd>{aweme.name||'未命名'} · {aweme.id}</dd>
          <dt className="text-[var(--muted)]">商品</dt><dd>{product.name||'未命名'} · {product.id}</dd>
          <dt className="text-[var(--muted)]">视频</dt><dd className="break-all">{upload.filename} · {upload.material_id}</dd>
        </dl>
        {catalogMock&&<p className="mt-3 rounded bg-amber-50 p-2 text-xs text-amber-900">抖音号和商品为 Mock 目录数据，仅用于平台目录不可用时的产品演示。</p>}
      </div>
      <div className="rounded border border-[var(--border)] bg-white p-4">
        <div className="flex items-center justify-between gap-3"><h4 className="text-sm font-medium">最小计划参数</h4>
          <span role="status" className="rounded bg-violet-100 px-2 py-1 text-xs text-violet-800">{STATUS[stage]}</span></div>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-xs sm:col-span-2">计划名称<input aria-label="Mock 计划名称" className="mt-1 w-full rounded border border-[var(--border)] p-2" maxLength={80} value={name} onChange={event=>setName(event.target.value)} disabled={stage!=='ready'}/></label>
          <label className="text-xs">日预算（演示元）<input aria-label="Mock 日预算" className="mt-1 w-full rounded border border-[var(--border)] p-2" type="number" min="1" value={budget} onChange={event=>setBudget(event.target.value)} disabled={stage!=='ready'}/></label>
          <label className="text-xs">目标 ROI<input aria-label="Mock 目标 ROI" className="mt-1 w-full rounded border border-[var(--border)] p-2" type="number" min="0.01" step="0.01" value={roiGoal} onChange={event=>setRoiGoal(event.target.value)} disabled={stage!=='ready'}/></label>
        </div>
        {planId&&<p className="mt-3 break-all text-xs text-[var(--muted)]">Mock 计划 ID：{planId}</p>}
      </div>
    </div>

    <div className="mt-4 flex flex-wrap items-center gap-2">
      {stage==='ready'&&<button className="primary" onClick={start} disabled={!name.trim()||Number(budget)<=0||Number(roiGoal)<=0}><CheckCircle2 size={15}/>创建并投流</button>}
      {running&&<button className="primary" disabled><Loader2 size={15} className="animate-spin"/>正在自动执行</button>}
      {stage!=='ready'&&<button className="btn-secondary" onClick={reset}><RotateCcw size={15}/>{stage==='enabled'?'重新演示':'取消并重置'}</button>}
      <p className="text-xs text-[var(--muted)]">演示会自动推进，无需逐步点击。</p>
    </div>

    {stage==='enabled'&&<section aria-label="Mock 计划数据" className="mt-5 rounded border border-emerald-200 bg-emerald-50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-medium text-emerald-950">计划投放数据</h4><span className="text-xs text-emerald-800">固定演示数据 · 非千川返回</span></div>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">{metrics.map(([label,value])=><div key={label} className="rounded bg-white p-3"><p className="text-xs text-[var(--muted)]">{label}</p><p className="mt-1 font-medium">{value}</p></div>)}</div>
    </section>}

    {!!events.length&&<div className="mt-5 rounded border border-[var(--border)] bg-white p-4">
      <h4 className="text-sm font-medium">Mock 执行记录</h4>
      <ol className="mt-3 space-y-2 text-xs">{[...events].reverse().map((event,index)=><li key={`${event.at}-${index}`} className="flex justify-between gap-3"><span>{event.label}</span><time className="shrink-0 text-[var(--muted)]">{event.at}</time></li>)}</ol>
    </div>}
  </section>;
}
