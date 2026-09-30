import {useEffect,useMemo,useState} from 'react';
import {Loader2,Plus,Trash2} from 'lucide-react';
import {Link,useNavigate,useSearchParams} from 'react-router-dom';
import {videoRepairApi} from '../api/client';
import type {VideoRepairAsset} from '../api/client';

type Range={start:string;end:string};
function message(error:unknown){const value=error as {response?:{data?:{detail?:string|{message?:string}}};message?:string};const detail=value.response?.data?.detail;return typeof detail==='string'?detail:(detail?.message||value.message||'创建失败')}

export default function VideoRepairCreate(){
  const navigate=useNavigate();const [params]=useSearchParams();
  const [assets,setAssets]=useState<VideoRepairAsset[]>([]);const [assetId,setAssetId]=useState(params.get('asset_id')||'');
  const [reason,setReason]=useState('');const [sourceKind,setSourceKind]=useState<'MANUAL'|'INTERNAL_REVIEW'>('MANUAL');
  const [expectedResult,setExpectedResult]=useState('');
  const [platform,setPlatform]=useState<'INTERNAL'|'AD'|'CONTENT'|'UNKNOWN'>('INTERNAL');const [suggestions,setSuggestions]=useState('');
  const [hint,setHint]=useState<'VISUAL'|'COPY'|'AV'|'PROCESS'|'FULL'|'UNKNOWN'>('UNKNOWN');const [ranges,setRanges]=useState<Range[]>([]);
  const [loading,setLoading]=useState(true);const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  useEffect(()=>{videoRepairApi.assets().then(rows=>{setAssets(rows);if(!assetId&&rows[0])setAssetId(String(rows[0].id))}).catch(()=>setError('成片列表加载失败')).finally(()=>setLoading(false))},[]);
  const selected=assets.find(item=>String(item.id)===assetId);
  const rangeError=useMemo(()=>ranges.some(row=>{const start=Number(row.start),end=Number(row.end);return row.start===''||row.end===''||start<0||end<=start||(selected&&end>selected.duration_seconds)}),[ranges,selected]);
  async function submit(){if(!assetId||!reason.trim()||rangeError)return;setBusy(true);setError('');try{const task=await videoRepairApi.create({source_asset_id:Number(assetId),input:{source_kind:sourceKind,audit_platform:platform,raw_reason:reason.trim(),expected_result:expectedResult.trim(),suggestions:suggestions.split('\n').map(item=>item.trim()).filter(Boolean).slice(0,20),time_ranges:ranges.map(row=>({start:Number(row.start),end:Number(row.end)})),category_hint:hint}});navigate(`/video-repair/${task.id}`)}catch(value){setError(message(value))}finally{setBusy(false)}}
  return <div className="mx-auto max-w-4xl space-y-6 animate-fade-in"><header><div className="mb-2 text-xs tracking-[.18em] text-[var(--muted)]">NEW VIDEO REPAIR</div><h1 className="font-display text-4xl">新建审核失败修改</h1><p className="mt-2 text-sm text-[var(--muted)]">描述问题事实即可，系统会根据原方案和素材库分析可修改范围。</p></header>
    <section className="panel space-y-5 p-6">
      <label className="field"><span>V1 成片 *</span><select className="input-select" value={assetId} onChange={event=>setAssetId(event.target.value)} disabled={loading}><option value="">请选择成片</option>{assets.map(asset=><option key={asset.id} value={asset.id}>{asset.filename}（{asset.duration_seconds.toFixed(1)} 秒）</option>)}</select></label>
      <label className="field"><span>问题描述 *</span><textarea className="input min-h-28" maxLength={4000} value={reason} onChange={event=>setReason(event.target.value)} placeholder="例如：6 到 10 秒商品效果展示不准确"/><small>{reason.length}/4000</small></label>
      <label className="field"><span>期望修改结果（可选）</span><textarea className="input min-h-20" maxLength={4000} value={expectedResult} onChange={event=>setExpectedResult(event.target.value)} placeholder="例如：保留原口播，只替换为真实商品使用画面"/><small>{expectedResult.length}/4000</small></label>
      <div className="grid gap-4 sm:grid-cols-3"><label className="field"><span>审核/检查来源</span><select className="input-select" value={sourceKind} onChange={event=>setSourceKind(event.target.value as typeof sourceKind)}><option value="MANUAL">人工检查</option><option value="INTERNAL_REVIEW">内部质检</option></select></label><label className="field"><span>审核平台</span><select className="input-select" value={platform} onChange={event=>setPlatform(event.target.value as typeof platform)}><option value="INTERNAL">内部</option><option value="AD">广告</option><option value="CONTENT">内容</option><option value="UNKNOWN">未知</option></select></label><label className="field"><span>问题类型提示</span><select className="input-select" value={hint} onChange={event=>setHint(event.target.value as typeof hint)}><option value="UNKNOWN">未知</option><option value="VISUAL">画面</option><option value="COPY">口播字幕</option><option value="AV">音视频整段</option><option value="PROCESS">缺过程</option><option value="FULL">全片</option></select></label></div>
      <label className="field"><span>建议（可选，每行一条）</span><textarea className="input min-h-24" value={suggestions} onChange={event=>setSuggestions(event.target.value)} placeholder="替换为真实商品展示画面"/></label>
      <div><div className="flex items-center justify-between"><span className="text-sm font-medium">问题时间段（可选）</span><button type="button" className="btn-secondary" disabled={ranges.length>=20} onClick={()=>setRanges(current=>[...current,{start:'',end:''}])}><Plus size={14}/>添加时间段</button></div><div className="mt-3 space-y-2">{ranges.map((range,index)=><div key={index} className="grid grid-cols-[1fr_1fr_auto] gap-2"><input aria-label={`开始秒数 ${index+1}`} className="input" type="number" min="0" step="0.1" placeholder="开始秒数" value={range.start} onChange={event=>setRanges(current=>current.map((item,i)=>i===index?{...item,start:event.target.value}:item))}/><input aria-label={`结束秒数 ${index+1}`} className="input" type="number" min="0.1" step="0.1" placeholder="结束秒数" value={range.end} onChange={event=>setRanges(current=>current.map((item,i)=>i===index?{...item,end:event.target.value}:item))}/><button type="button" className="btn-secondary" aria-label={`删除时间段 ${index+1}`} onClick={()=>setRanges(current=>current.filter((_,i)=>i!==index))}><Trash2 size={14}/></button></div>)}</div>{rangeError&&<p role="alert" className="mt-2 text-xs text-rose-700">时间段必须大于 0、结束晚于开始，且不能超过成片时长。</p>}</div>
      {error&&<p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
      <div className="flex gap-2"><button className="primary" onClick={submit} disabled={busy||loading||!assetId||!reason.trim()||rangeError}>{busy&&<Loader2 size={15} className="animate-spin"/>}创建并分析</button><Link className="btn-secondary" to="/video-repair">取消</Link></div>
    </section>
  </div>;
}
