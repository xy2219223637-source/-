import {useEffect,useRef,useState} from 'react';
import axios from 'axios';
import {BarChart3,Loader2,RefreshCw} from 'lucide-react';
import {qianchuanApi} from '../api/client';
import type {QianchuanMaterialReport,QianchuanUpload} from '../api/client';

const groups=[
  {title:'投放表现',fields:['stat_cost_for_roi2','total_pay_order_gmv_include_coupon_for_roi2','total_pay_order_count_for_roi2','total_prepay_and_pay_settle_roi2_1h','product_show_count_for_roi2','product_click_count_for_roi2','product_cvr_rate_for_roi2','product_cpc_for_roi2']},
  {title:'视频播放质量',fields:['video_play_count_for_roi2_v2','video_play_duration_2s_rate_for_roi2','video_play_duration_3s_rate_for_roi2','video_play_duration_5s_rate_for_roi2','video_play_duration_10s_rate_for_roi2','video_play_finish_rate_for_roi2_v2']},
  {title:'互动表现',fields:['video_like_count_for_roi2','video_comment_count_for_roi2_v2']},
];

function message(error:unknown){
  if(axios.isAxiosError(error)&&typeof error.response?.data?.detail==='string')return error.response.data.detail;
  return '千川报表查询失败，请稍后重试';
}

function Metric({label,value}:{label:string;value:string|null}){
  return <div className="rounded border border-[var(--border)] bg-white p-4"><div className="text-xs text-[var(--muted)]">{label}</div><div className="mt-2 break-all text-2xl font-medium tracking-tight">{value??'—'}</div><div className="mt-1 text-[11px] text-[var(--muted)]">平台原始值</div></div>;
}

export default function QianchuanReportPanel({upload}:{upload?:QianchuanUpload}){
  const [days,setDays]=useState(7);
  const [report,setReport]=useState<QianchuanMaterialReport|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const requestId=useRef(0);
  const pending=useRef(false);

  // Changing query conditions only resets the display; fetching is button-driven.
  useEffect(()=>{
    requestId.current+=1;pending.current=false;
    setReport(null);setError('');setLoading(false);
    return()=>{requestId.current+=1;pending.current=false};
  },[upload?.id,upload?.material_id,upload?.status,days]);

  async function refresh(){
    if(pending.current||!upload?.material_id||upload.status!=='succeeded')return;
    pending.current=true;
    const current=++requestId.current;
    setLoading(true);setError('');setReport(null);
    try{
      const value=await qianchuanApi.report(upload.id,days);
      if(current===requestId.current)setReport(value);
    }catch(reason){
      if(current===requestId.current)setError(message(reason));
    }finally{
      if(current===requestId.current){pending.current=false;setLoading(false)}
    }
  }

  if(!upload?.material_id||upload.status!=='succeeded')return <section className="panel p-8 text-center text-sm text-[var(--muted)]"><BarChart3 className="mx-auto mb-3"/>素材上传并核验成功后，可尝试查询千川素材报表；上传成功不代表已投流。</section>;
  return <>
    <section className="panel p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-medium">千川素材数据</h2><p className="mt-1 text-xs text-[var(--muted)]">查询账户：{upload.advertiser_name||'千川账户'} · {upload.advertiser_id}</p><p className="mt-1 text-xs text-[var(--muted)]">{report?.data_topic==='SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO'?'全域商品视频素材报表':'全域/乘方商品素材报表'} · 素材 {upload.material_id}</p><p className="mt-1 text-xs text-[var(--muted)]">平台按账号和素材汇总；若素材被其他计划复用，指标不能视为个人独占。</p></div><div className="flex items-center gap-2"><select aria-label="报表时间范围" className="input-select" value={days} onChange={event=>setDays(Number(event.target.value))} disabled={loading}><option value={7}>近 7 天</option><option value={30}>近 30 天</option></select><button className="btn-secondary whitespace-nowrap shrink-0" onClick={()=>void refresh()} disabled={loading}>{loading?<Loader2 size={14} className="animate-spin"/>:<RefreshCw size={14}/>}刷新</button></div></div>
      {error&&<div className="mt-5 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>}
      <p className="mt-3 text-xs text-[var(--muted)]">数据缓存 1 小时，缓存期间刷新不会重复查询千川；到期后再次刷新获取新数据。</p>
      {!report&&!loading&&!error&&<p className="mt-5 text-sm text-[var(--muted)]" role="status">点击“刷新”获取当前账户、素材和时间范围的投放数据。</p>}
      {report&&<div className="mt-2 text-xs text-[var(--muted)]" aria-live="polite">
        上次成功查询：{new Date(report.queried_at).toLocaleString('zh-CN')} ·
        缓存到期：{new Date(report.cache_expires_at).toLocaleString('zh-CN')} ·
        {report.cache_status==='hit'?'本次读取缓存':report.cache_status==='joined'?'本次共用查询结果':'本次已查询千川'}
      </div>}
      {loading&&!report&&<div className="grid min-h-36 place-items-center text-sm text-[var(--muted)]"><Loader2 className="animate-spin"/></div>}
      {report&&<><div className={`mt-5 px-4 py-3 text-sm ${report.status==='no_data'?'border border-amber-200 bg-amber-50 text-amber-800':'border border-emerald-200 bg-emerald-50 text-emerald-800'}`}>{report.status==='no_data'?'当前账户、素材和时间范围内暂无匹配报表；不能据此判断从未投流。':'已读取千川账号级素材汇总报表。'}<span className="ml-2 text-xs opacity-75">{report.start_time} 至 {report.end_time}</span></div>
        {groups.map(group=>{const metrics=group.fields.map(field=>report.metrics[field]).filter(Boolean);return metrics.length?<div className="mt-7" key={group.title}><h3 className="font-medium">{group.title}</h3><div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">{metrics.map(metric=><Metric key={metric.label} label={metric.label} value={metric.value}/>)}</div></div>:null})}
      </>}
    </section>
    <section className="panel p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="text-lg font-medium">逐秒互动与流失</h2><p className="mt-1 text-xs text-[var(--muted)]">当前应用没有可用的逐秒字段接口。</p></div><span className="rounded bg-[#f0f0ee] px-2.5 py-1 text-xs text-[var(--muted)]">暂不可用</span></div><div className="mt-5 rounded bg-[#f6f5f2] p-4 text-sm text-[var(--muted)]">{report?.per_second.message||'固定的 2、3、5、10 秒播放率会显示在播放质量中，不用于插值或生成逐秒曲线。'}</div></section>
  </>;
}
