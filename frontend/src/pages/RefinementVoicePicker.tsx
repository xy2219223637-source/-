import {useEffect, useState} from 'react';
import {videoRefinementApi} from '../api/client';
import type {RefinementSpeakerCatalog, RefinementVoiceSelection} from '../types/videoRefinement';

export function VoiceMatchSummary({selection,manualSpeaker}: {selection?: RefinementVoiceSelection; manualSpeaker?: string | null}) {
  return <section className="refine-voice-summary" aria-label="新增配音策略">
    <div className="refine-voice-heading"><h4>新增配音</h4><span className="refine-note">{manualSpeaker?'手动指定':'自动选择'}</span></div>
    <p><strong>{manualSpeaker?(selection?.speaker===manualSpeaker?selection.name:'已指定中文音色'):selection?.name||'按视频内容自动选择'}</strong></p>
    <p className="refine-note">仅调整新增或改写台词, 其余部分保留原配音.</p>
    <details className="refine-disclosure"><summary>配音说明</summary>
      {manualSpeaker?<p>可在“修改意见与时长目标”中更改指定音色.</p>:selection&&<><p>{selection.basis==='content_only'?'按视频内容选声':'参考原声与视频内容'}</p><p>{selection.reason}</p></>}
      <p>不做声音复刻. 无可靠原声时按内容选声, 生成后仍需试听核对.</p>
    </details>
  </section>;
}

export function RefinementVoicePicker({value,onChange,disabled=false,label='配音音色',automaticLabel='自动适配原视频'}: {
  value: string | null; onChange: (value: string | null)=>void; disabled?: boolean; label?: string; automaticLabel?: string;
}) {
  const [catalog,setCatalog]=useState<RefinementSpeakerCatalog|null>(null);
  const [error,setError]=useState(false);
  const [retry,setRetry]=useState(0);
  const [search,setSearch]=useState('');
  const [gender,setGender]=useState('');
  useEffect(()=>{
    let active=true;
    setError(false);
    videoRefinementApi.voiceOptions().then(next=>{if(active)setCatalog(next)}).catch(()=>{if(active)setError(true)});
    return()=>{active=false};
  },[retry]);
  const term=search.trim().toLocaleLowerCase();
  const matches=(catalog?.items||[]).filter(row=>(!gender||row.gender===gender)&&(!term||(row.name+' '+row.id).toLocaleLowerCase().includes(term)));
  const visible=(catalog?.items||[]).filter(row=>row.id===value||matches.some(match=>match.id===row.id));
  return <div className="refine-voice-picker">
    <div className="refine-voice-filters">
      <label className="field">搜索中文音色<input type="search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="音色名称或ID" disabled={disabled||!catalog}/></label>
      <label className="field">音色类型<select value={gender} onChange={e=>setGender(e.target.value)} disabled={disabled||!catalog}><option value="">全部</option><option value="female">女声</option><option value="male">男声</option></select></label>
    </div>
    <label className="field">{label}<select value={value||''} onChange={e=>onChange(e.target.value||null)} disabled={disabled||!catalog}>
      <option value="">{automaticLabel}</option>
      {value&&!catalog?.items.some(row=>row.id===value)&&<option value={value}>{value}</option>}
      {(['female','male'] as const).map(kind=><optgroup key={kind} label={kind==='female'?'中文女声':'中文男声'}>{visible.filter(row=>row.gender===kind).map(row=><option value={row.id} key={row.id}>{row.name}</option>)}</optgroup>)}
    </select></label>
    {error?<p className="refine-risk" role="alert">中文音色列表加载失败.<button type="button" className="refine-text-button" onClick={()=>setRetry(v=>v+1)}>重新加载音色</button></p>:catalog?<p className="refine-note" role="status">共{catalog.total}种中文相关音色, 当前筛选{matches.length}种.{!matches.length?' 请调整搜索或音色类型.':''}</p>:<p className="refine-note" role="status">正在加载中文音色...</p>}
  </div>;
}
