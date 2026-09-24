import {useState} from 'react';
import type {QianchuanDemoPlan} from './QianchuanPlanDemo';

const METRICS=[
  ['消耗','¥428.60'],['展现','128,430'],['点击率','4.72%'],
  ['成交订单','86'],['成交金额','¥6,972.00'],['实际 ROI','16.27'],
];

export default function QianchuanPlanDataPanel({plans,onCreate}:{plans:QianchuanDemoPlan[];onCreate:()=>void}){
  const [selectedId,setSelectedId]=useState('');
  const selected=plans.find(plan=>plan.id===selectedId)||plans[0];

  return <section aria-label="投流计划数据" className="rounded border border-[var(--border)] p-5">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><h3 className="font-medium">投流计划数据</h3>
        <p className="mt-1 text-xs text-[var(--muted)]">创建入口在弹窗中；这里常驻计划筛选、状态和计划口径数据。</p></div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="block min-w-64 text-sm">投流计划
          <select aria-label="投流计划" className="input-select mt-2 w-full" value={selected?.id||''}
            onChange={event=>setSelectedId(event.target.value)} disabled={!plans.length}>
            {!plans.length&&<option value="">暂无计划</option>}
            {plans.map(plan=><option key={plan.id} value={plan.id}>{plan.name} · 投放中</option>)}
          </select>
        </label>
        <button className="btn-secondary" onClick={onCreate}>创建新计划</button>
      </div>
    </div>
    {!selected?<div className="mt-4 rounded bg-[var(--bg)] p-5 text-sm text-[var(--muted)]">当前视频还没有本页创建的 Mock 计划。点击“创建新计划”或页面顶部“推送投流”开始。</div>:<>
      <div className="mt-4 grid gap-3 rounded bg-[var(--bg)] p-4 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <p><span className="text-[var(--muted)]">计划 ID</span><span className="mt-1 block break-all">{selected.id}</span></p>
        <p><span className="text-[var(--muted)]">广告账号</span><span className="mt-1 block">{selected.advertiser_name}</span></p>
        <p><span className="text-[var(--muted)]">计划状态</span><span className="mt-1 block text-emerald-700">投放中</span></p>
        <p><span className="text-[var(--muted)]">日预算 / 目标 ROI</span><span className="mt-1 block">¥{selected.budget} / {selected.roi_goal}</span></p>
      </div>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2"><h4 className="text-sm font-medium">计划投放数据</h4><span className="text-xs text-amber-700">Mock 计划筛选与固定数据 · 非千川返回</span></div>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">{METRICS.map(([label,value])=><div key={label} className="rounded bg-[var(--bg)] p-3"><p className="text-xs text-[var(--muted)]">{label}</p><p className="mt-1 font-medium">{value}</p></div>)}</div>
    </>}
  </section>;
}
