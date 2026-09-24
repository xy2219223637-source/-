import {useState} from 'react';
import type {QianchuanUpload} from '../api/client';
import QianchuanReportPanel from './QianchuanReportPanel';
import QianchuanUploadHistory,{uploadStatus} from './QianchuanUploadHistory';
import QianchuanPlanDataPanel from './QianchuanPlanDataPanel';
import type {QianchuanDemoPlan} from './QianchuanPlanDemo';

export default function QianchuanAnalysis({uploads,error,reload,onManage,onCreatePlan,plans=[]}: {
  uploads:QianchuanUpload[]; error:string; reload:()=>void; onManage:()=>void;
  onCreatePlan?:()=>void; plans?:QianchuanDemoPlan[];
}) {
  const [selectedAdvertiserId,setSelectedAdvertiserId]=useState('');
  const orderedUploads=[...uploads].sort((left,right)=>(Date.parse(right.created_at)||0)-(Date.parse(left.created_at)||0));
  const accounts=[...new Map([...orderedUploads].reverse().map(row=>[row.advertiser_id,row])).values()].reverse();
  const advertiserId=accounts.some(row=>row.advertiser_id===selectedAdvertiserId)
    ? selectedAdvertiserId : accounts[0]?.advertiser_id||'';
  const accountUploads=orderedUploads.filter(row=>row.advertiser_id===advertiserId);
  const latestUpload=accountUploads[0];
  const reportUpload=accountUploads.find(row=>row.status==='succeeded'&&Boolean(row.material_id));

  return <section aria-label="千川投放分析" className="space-y-5">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><h2 className="text-lg font-medium">千川素材反馈</h2><p className="mt-1 text-xs text-[var(--muted)]">按投放账号查看素材上传状态与报表；上传成功不代表已投流。</p><button className="btn-secondary mt-3" onClick={onManage}>绑定或管理我的千川账号</button></div>
      <label className="block min-w-60 text-sm">投放账号
        <select aria-label="投放账号" className="input-select mt-2 w-full" value={advertiserId} onChange={event=>setSelectedAdvertiserId(event.target.value)} disabled={!accounts.length}>
          {!accounts.length&&<option value="">暂无上传账号</option>}
          {accounts.map(row=><option key={row.advertiser_id} value={row.advertiser_id}>{row.advertiser_name||'千川账户'} · {row.advertiser_id}</option>)}
        </select>
      </label>
    </div>
    <div className="rounded border border-[var(--border)] p-4 text-sm">
      <p>素材推送状态：{error?'上传状态读取失败':uploadStatus(latestUpload)}</p>
      {reportUpload?.material_id&&<p className="mt-2 break-all text-[var(--muted)]">千川素材 ID：{reportUpload.material_id}</p>}
    </div>
    <QianchuanPlanDataPanel plans={plans} onCreate={onCreatePlan||onManage}/>
    <QianchuanReportPanel key={reportUpload?.id||advertiserId||'no-upload'} upload={reportUpload}/>
    <QianchuanUploadHistory uploads={accountUploads} error={error} reload={reload}/>
  </section>;
}
