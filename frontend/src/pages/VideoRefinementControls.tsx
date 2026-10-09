import {useEffect, useRef, useState} from 'react';
import {Loader2, Plus, RefreshCw, Search, Trash2} from 'lucide-react';
import {mediaUrl, videoRefinementApi} from '../api/client';
import type {DurationPreference, RefinementMaterialPage, RefinementSnapshot, VideoRefinementPlanItem, VideoRefinementTask} from '../types/videoRefinement';

type Action = (name: string, work: () => Promise<VideoRefinementTask>) => Promise<void>;
const seconds = (value: number) => (value/1000).toFixed(2)+' 秒';
const requirementLabels = {EXECUTABLE:'可执行', ALREADY_SATISFIED:'当前已满足', NEEDS_CLARIFICATION:'需要澄清', CONFLICT:'要求冲突', UNSUPPORTED:'能力不支持', AWAITING_MATERIAL:'等待执行条件'};

export function EvaluationProgress({task}: {task: VideoRefinementTask}) {
  const working=task.analysis_state?.status==='queued'||task.analysis_state?.status==='analyzing';
  const stageLabels: Record<string,string>={source:'正在核对源成片',requirements:'正在解析修改意见',materials:'正在准备素材分析',fitting:'正在匹配无字幕画面与配音时长',voiceover:'正在生成并绑定补充段配音',matching:'正在匹配修改意见',planning:'正在生成精修方案'};
  if(working)return <p className="refine-evaluation refine-note" role="status"><Loader2 size={16} className="animate-spin"/>{stageLabels[task.analysis_state?.progress.stage||'']||'正在准备精修方案'}</p>;
  const stats=task.plan?.material_evaluation;
  if(stats?.complete===false&&!stats.partial_accepted)return <p className="refine-evaluation refine-risk" role="status">{stats.failed?'部分素材评估失败, 可重试 AI 精修, 或使用已评估素材继续.':'素材评估尚未完成, 请重试 AI 精修.'}</p>;
  return null;
}

export function RequirementPanel({task, disabled, action}: {task: VideoRefinementTask; disabled: boolean; action: Action}) {
  const [answers,setAnswers]=useState<Record<string,string>>({});
  const rows=(task.plan?.requirements_resolution||[]).filter(row=>row.decision!=='waived'&&!['EXECUTABLE','ALREADY_SATISFIED'].includes(row.status)&&(row.status!=='AWAITING_MATERIAL'||!!row.question?.trim()));
  if(!rows.length)return null;
  async function decide(id: string, decision: 'answered'|'waived') {
    await action('requirements',async()=>{const next=await videoRefinementApi.requirements(task.id,{revision:task.current_revision,decisions:[{requirement_id:id,decision,answer:answers[id]||''}]});return videoRefinementApi.autoPreview(task.id,next.current_revision)});
  }
  return <section className="refine-requirements" aria-label="需要补充或确认的要求"><h3>需要您补充或确认</h3>{rows.map(row=><div key={row.requirement_id} className="refine-requirement"><div><strong>{row.text}</strong><span className="refine-status">{requirementLabels[row.status]}</span></div><p>{row.reason}</p>
    <p>{row.question||'请补充影响执行的必要信息.'}</p><label className="field">对此要求的回答<input value={answers[row.requirement_id]||''} maxLength={2000} onChange={e=>setAnswers(current=>({...current,[row.requirement_id]:e.target.value}))}/></label><div className="refine-inline-actions"><button className="btn-secondary" disabled={disabled||!answers[row.requirement_id]?.trim()} onClick={()=>decide(row.requirement_id,'answered')}>保存回答并重新规划</button><button className="refine-text-button" disabled={disabled} onClick={()=>decide(row.requirement_id,'waived')}>放弃这条要求并重新规划</button></div>
  </div>)}</section>;
}

export function GoalEditor({task,disabled,action}: {task: VideoRefinementTask; disabled: boolean; action: Action}) {
  const [brief,setBrief]=useState(task.user_brief);
  const [preference,setPreference]=useState<DurationPreference>(task.options?.duration_preference||'ai_recommend');
  const [target,setTarget]=useState(task.options?.target_duration_ms?String(task.options.target_duration_ms/1000):'');
  useEffect(()=>{setBrief(task.user_brief);setPreference(task.options?.duration_preference||'ai_recommend');setTarget(task.options?.target_duration_ms?String(task.options.target_duration_ms/1000):'')},[task.current_revision,task.user_brief,task.options]);
  return <details className="refine-goal-editor"><summary>修改意见与时长目标</summary><label className="field">精修意见<textarea rows={3} maxLength={2000} value={brief} onChange={e=>setBrief(e.target.value)}/></label><label className="field">时长目标<select value={preference} onChange={e=>setPreference(e.target.value as DurationPreference)}><option value="ai_recommend">由 AI 建议, 确认实际变化</option><option value="preserve">保持原时长</option><option value="shorten">缩短成片</option></select></label>{preference==='shorten'&&<label className="field">目标时长, 秒<input type="number" min="0.1" max={task.source_snapshot.source.duration_ms/1000} step="0.1" value={target} onChange={e=>setTarget(e.target.value)}/></label>}<button className="btn-secondary" disabled={disabled||!brief.trim()||(preference==='shorten'&&!!target&&Number(target)<=0)} onClick={()=>action('requirements',async()=>{const next=await videoRefinementApi.requirements(task.id,{revision:task.current_revision,user_brief:brief.trim(),duration_preference:preference,...(target&&preference==='shorten'?{target_duration_ms:Math.round(Number(target)*1000)}:{})});return videoRefinementApi.autoPreview(task.id,next.current_revision)})}>保存目标并生成预览</button></details>;
}

export function MaterialSegmentsEditor({task,item,disabled,onChange}: {task: VideoRefinementTask; item: VideoRefinementPlanItem; disabled: boolean; onChange: (patch: Partial<VideoRefinementPlanItem>)=>void}) {
  const [search,setSearch]=useState('');
  const [page,setPage]=useState<RefinementMaterialPage|null>(null);
  const [cursors,setCursors]=useState<Array<string|undefined>>([undefined]);
  const [index,setIndex]=useState(0);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [retry,setRetry]=useState(0);
  const [selected,setSelected]=useState<RefinementSnapshot['material_catalog'][number]|null>(null);
  const [start,setStart]=useState(0);
  const [end,setEnd]=useState(0);
  const player=useRef<HTMLVideoElement>(null);
  const playing=useRef(false);
  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');
    const timer=window.setTimeout(()=>videoRefinementApi.materials(task.id,search,cursors[index]).then(next=>{if(active)setPage(next)}).catch(()=>{if(active){setPage(null);setError('素材加载失败, 请重试')}}).finally(()=>{if(active)setLoading(false)}),250);
    return()=>{active=false;window.clearTimeout(timer)};
  },[task.id,task.snapshot_hash,search,index,cursors,retry]);
  const segments=item.material_segments||[];
  const length=segments.reduce((sum,s)=>sum+s.source_range.end_ms-s.source_range.start_ms,0);
  const needed=item.type==='VISUAL_REPLACE'?item.range.end_ms-item.range.start_ms:undefined;
  const valid=selected&&start>=0&&end>start&&end<=selected.duration_ms;
  function attach(append: boolean){if(!selected||!valid)return;const segment={asset_id:selected.asset_id,source_range:{start_ms:start,end_ms:end}};onChange({material_segments:append?[...segments,segment]:[segment],material_asset_id:append&&segments.length?segments[0].asset_id:selected.asset_id,material_start_ms:append&&segments.length?segments[0].source_range.start_ms:start,duration_delta_ms:0});}
  function seek(at: number){if(player.current){player.current.pause();player.current.currentTime=at/1000;playing.current=false}}
  return <section className="refine-material-editor" aria-label="素材局部选段"><h4>同商品已分析素材</h4><p className="refine-note">选择源文件的一段画面. 素材拍摄原声始终关闭.</p>
    <label className="refine-search"><Search size={16}/><input aria-label="搜索全部同商品素材" value={search} onChange={e=>{setSearch(e.target.value);setIndex(0);setCursors([undefined])}} placeholder="搜索文件名或分析描述"/></label>
    {loading?<p role="status"><Loader2 size={16} className="animate-spin"/>正在加载素材</p>:error?<p role="alert">{error}<button className="refine-text-button" onClick={()=>setRetry(v=>v+1)}>重新加载</button></p>:<><div className="refine-material-list">{page?.items.map(row=><button type="button" key={row.asset_id} disabled={disabled} aria-pressed={selected?.asset_id===row.asset_id} onClick={()=>{setSelected(row);setStart(0);setEnd(Math.min(row.duration_ms,needed||4000));playing.current=false}}><strong>{row.filename}</strong><span>{seconds(row.duration_ms)}</span></button>)}{page&&!page.items.length&&<p>没有匹配的可用素材, 请调整搜索或刷新素材库.</p>}</div><div className="refine-inline-actions"><button className="btn-secondary" disabled={index===0} onClick={()=>setIndex(v=>v-1)}>上一页</button><span>共 {page?.total??0} 条 / 第 {index+1} 页</span><button className="btn-secondary" disabled={!page?.next_cursor} onClick={()=>{setCursors(current=>[...current.slice(0,index+1),page!.next_cursor!]);setIndex(v=>v+1)}}>下一页</button></div></>}
    {selected&&<><h4>{selected.filename}</h4><video ref={player} className="refine-material-player" key={selected.asset_id} src={mediaUrl(selected.object_url)} muted controls playsInline preload="metadata" aria-label="选段范围播放器" onLoadedMetadata={()=>seek(start)} onPlay={()=>{const v=player.current;if(v&&(v.currentTime<start/1000||v.currentTime>=end/1000))v.currentTime=start/1000;playing.current=true}} onTimeUpdate={()=>{const v=player.current;if(v&&playing.current&&v.currentTime>=end/1000){v.pause();v.currentTime=end/1000;playing.current=false}}}/><div className="refine-time-fields"><label className="field">素材源起点, 秒<input type="number" min={0} max={selected.duration_ms/1000} step="0.001" disabled={disabled} value={start/1000} onChange={e=>setStart(Math.round(Number(e.target.value)*1000))}/></label><label className="field">素材源终点, 秒<input type="number" min={0} max={selected.duration_ms/1000} step="0.001" disabled={disabled} value={end/1000} onChange={e=>setEnd(Math.round(Number(e.target.value)*1000))}/></label></div><input type="range" aria-label="拖动素材起点" min={0} max={selected.duration_ms} step={Math.max(1,Math.round(1000/task.source_snapshot.source.fps))} value={start} disabled={disabled} onChange={e=>setStart(Number(e.target.value))}/><input type="range" aria-label="拖动素材终点" min={0} max={selected.duration_ms} step={Math.max(1,Math.round(1000/task.source_snapshot.source.fps))} value={end} disabled={disabled} onChange={e=>setEnd(Number(e.target.value))}/><div className="refine-inline-actions"><button className="btn-secondary" onClick={()=>seek(start)}>查看开始帧</button><button className="btn-secondary" onClick={()=>seek(Math.max(start,end-1000/task.source_snapshot.source.fps))}>查看结束帧</button><button className="btn-secondary" disabled={!valid} onClick={()=>{if(player.current){player.current.currentTime=start/1000;playing.current=true;void player.current.play().catch(()=>setError('播放失败, 请使用播放器控件重试'))}}}>播放选中区间</button><a href={mediaUrl(selected.object_url)} target="_blank" rel="noreferrer">查看完整素材</a></div><p>本次选择 {seconds(Math.max(0,end-start))}</p>{!valid&&<p role="alert" className="refine-risk">选段范围无效, 请检查开始和结束时间.</p>}<div className="refine-inline-actions"><button className="btn-secondary" disabled={disabled||!valid} onClick={()=>attach(false)}>使用此选段</button><button className="btn-secondary" disabled={disabled||!valid} onClick={()=>attach(true)}><Plus size={14}/>追加相关片段</button></div></>}
    <div className="refine-selected-segments">{segments.map((segment,i)=><div key={i}><span>素材 {segment.asset_id}: {seconds(segment.source_range.start_ms)} - {seconds(segment.source_range.end_ms)}</span><button type="button" disabled={disabled} aria-label={'移除选段 '+(i+1)} onClick={()=>{const next=segments.filter((_,index)=>index!==i);onChange({material_segments:next,material_asset_id:next[0]?.asset_id??null,material_start_ms:next[0]?.source_range.start_ms??0,duration_delta_ms:0})}}><Trash2 size={15}/></button></div>)}</div><p>已选画面共 {seconds(length)}{needed!==undefined?' / 成片目标区间 '+seconds(needed):''}</p>{needed!==undefined&&length<needed&&<p className="refine-risk">画面长度不足, 请调整选段或增加其他相关片段.</p>}
  </section>;
}

export function VoiceoverEditor({task,item,disabled,dirty,action,onChange}: {task: VideoRefinementTask; item: VideoRefinementPlanItem; disabled: boolean; dirty: boolean; action: Action; onChange: (patch: Partial<VideoRefinementPlanItem>)=>void}) {
  const voices=(task.voiceovers||[]).filter(v=>v.script.trim()===item.script.trim());
  return <section className="refine-voiceover" aria-label="局部配音"><h4>局部配音 / 豆包默认女声</h4><p className="refine-note">只生成这段新增或改写的台词. 原片其他配音继续保留.</p><button className="btn-secondary" disabled={disabled||dirty||!item.script.trim()||voices.some(v=>['queued','generating'].includes(v.status))} onClick={()=>action('voiceover',()=>videoRefinementApi.generateVoiceover(task.id,task.current_revision,task.plan_hash!,item.item_id))}>{voices.some(v=>['queued','generating'].includes(v.status))?<Loader2 size={16} className="animate-spin"/>:<RefreshCw size={16}/>}生成或重试局部配音</button>{dirty&&<p className="refine-note">请先保存台词草稿, 再生成配音.</p>}{voices.map(voice=><div key={voice.id}><p>{voice.status==='ready'?'配音已准备, 实测 '+seconds(voice.duration_ms):voice.status==='failed'?voice.error_message:voice.status==='queued'?'配音排队中':voice.status==='generating'?'正在生成配音':'历史配音已失效'}</p>{voice.status==='ready'&&<><audio controls preload="metadata" src={mediaUrl(voice.object_url)} aria-label="局部配音试听"/><button className="btn-secondary" disabled={disabled||item.voiceover_id===voice.id} onClick={()=>onChange({voiceover_id:voice.id,subtitle_strategy:voice.timing.segments?.length?'regenerate':item.subtitle_strategy})}>{item.voiceover_id===voice.id?'已绑定到草稿':'绑定到当前草稿'}</button><p className="refine-note">绑定后需保存并重新确认. 配音必须完整播放, 画面长度由所选区间决定.</p>{!voice.timing.segments?.length&&<p className="refine-risk">没有可信字幕时间, 可启用转写对齐或明确选择不生成字幕.</p>}</>}</div>)}</section>;
}
