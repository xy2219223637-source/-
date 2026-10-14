import {VoiceMatchSummary} from './RefinementVoicePicker';
import {useEffect, useMemo, useRef, useState} from 'react';
import {Link, useNavigate, useParams, useSearchParams} from 'react-router-dom';
import {AlertTriangle, ArrowLeft, Check, ChevronLeft, ChevronRight, Download, Eye, Film, Loader2, LockKeyhole, Pencil, RefreshCw, Search, WandSparkles} from 'lucide-react';
import {mediaUrl, videoRefinementApi} from '../api/client';
import {VideoTaxonomySummary} from '../components/EcommerceVideoTaxonomyEditor';
import {refinementDurationError} from '../lib/refinementDuration';
import type {RefinementSource, RefinementSnapshot, VideoRefinementPlanItem, VideoRefinementTask, VideoRefinementTaskSummary} from '../types/videoRefinement';
import './VideoRefinementWorkbench.css';
import {EvaluationProgress, GoalEditor, MaterialSegmentsEditor, RequirementPanel, VoiceoverEditor} from './VideoRefinementControls';

const statusLabels: Record<VideoRefinementTask['status'], string> = {CREATED:'待生成方案', PLAN_READY:'方案待确认', CONFIGURING:'方案已修改', NEEDS_HUMAN_CONFIRMATION:'需要人工确认', MATERIAL_REQUIRED:'需要素材映射', PLAN_CONFIRMED:'方案已确认', CANCELLED:'已取消', COMPLETED:'精修版本已保存'};
const itemLabels = {VISUAL_REPLACE:'画面替换', PACE_RECUT:'剪辑节奏', SEGMENT_INSERT:'片段插入', VOICE_REPLACE:'配音台词', SUBTITLE_EDIT:'字幕文本'};
const seconds = (ms: number) => `${(ms / 1000).toFixed(2)} 秒`;
const sourceTaxonomy = (source: RefinementSource) => ({
  category_id:source.category_id??null, category:source.category??null, taxonomy_tags:source.taxonomy_tags??[],
  public_tags:source.public_tags??[], personal_tags:source.personal_tags??[], ai_tags:[], effective_tags:[],
});
const timecode = (ms: number) => `${String(Math.floor(ms / 60000)).padStart(2,'0')}:${(ms % 60000 / 1000).toFixed(3).padStart(6,'0')}`;
const actionNotices: Record<string, string> = {
  confirm:'整套方案已确认, 可以生成视频预览.',
  save:'新草稿已保存, 旧预览失效, 需要重新确认.',
  cancel:'任务已取消.',
  preview:'预览已排队, 完成后可播放核对.',
  accept:'精修版本已保存到商品成片库.',
  voiceover:'配音已排队, 完成后试听并显式绑定到草稿.',
  refresh:'素材已刷新, 旧确认和预览失效, 请重新分析.',
};
function analysisNotice(task: VideoRefinementTask): string {
  const status = task.analysis_state?.status;
  if(task.worker_state&&task.worker_state.status!=='running'&&(status==='queued'||status==='analyzing'))return '';
  if(status==='queued')return '分析已排队, 将匹配已分析素材并生成方案.';
  if(status==='analyzing')return '正在分析并生成精修方案, 请稍候.';
  if(!task.plan)return '';
  if(status==='needs_user_input')return '精修方案已生成, 请处理需要澄清或补充的要求.';
  if(task.plan.automatic_preview&&status==='ready'){
    const preview=task.previews?.find(row=>row.revision===task.current_revision&&row.plan_hash===task.plan_hash&&row.status!=='superseded');
    if(preview?.status==='ready')return '精修预览已生成, 请播放核对后另存精修版本.';
    if(preview?.status==='accepted')return '精修版本已保存到商品成片库.';
    if(preview?.status==='failed')return '';
    if(preview?.status==='queued'||preview?.status==='rendering')return '精修方案已就绪, 视频预览正在生成.';
  }
  if(status==='ready'||!task.analysis_state)return '真实 AI 精修方案已生成.';
  return '';
}
function generatePreview(task: VideoRefinementTask) {
  return task.source_snapshot.schema_version==='video_refinement_snapshot.v2'
    ?videoRefinementApi.autoPreview(task.id,task.current_revision)
    :videoRefinementApi.analyze(task.id,task.current_revision);
}
function errorMessage(error: unknown, fallback = '操作失败, 请检查网络并重试') {
  const detail = (error as {response?: {data?: {detail?: unknown}}})?.response?.data?.detail;
  if(typeof detail==='string')return detail;
  if(Array.isArray(detail)){const fields=detail.flatMap(row=>(row as {loc?: string[]}).loc||[]);if(fields.includes('duration_preference')||fields.includes('target_duration_ms'))return '服务尚未更新到当前版本, 请稍后重试';if(fields.includes('user_brief'))return '请填写修改意见, 最多 2000 字';return '提交失败, 请刷新页面后重试';}
  return fallback;
}

function SourceSetup({source}: {source: RefinementSource}) {
  const navigate = useNavigate();
  const [brief, setBrief] = useState('');
  const [durationPreference,setDurationPreference]=useState<'preserve'|'shorten'|'ai_recommend'>('ai_recommend');
  const [target,setTarget]=useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submitting = useRef(false);
  const sourceMs = Math.round(source.duration_seconds * 1000);
  const durationError = refinementDurationError(durationPreference, target, sourceMs);
  async function create() {
    if(submitting.current||durationError||!brief.trim())return;
    submitting.current=true;
    setBusy(true); setError('');
    try {
      const task = await videoRefinementApi.create({source_asset_id:source.id, user_brief:brief.trim(),duration_preference:durationPreference,...(durationPreference==='shorten'&&target?{target_duration_ms:Math.round(Number(target)*1000)}:{})});
      navigate(`/video-refinements/${task.id}?analyze=1`);
    } catch (error) {setError(errorMessage(error))} finally {submitting.current=false;setBusy(false)}
  }
  return <div className="refine-setup">
    <section className="refine-source-preview" aria-label="已选商品成片">
      <video key={source.id} src={mediaUrl(source.object_url)} controls playsInline preload="metadata" aria-label="商品成片播放器" />
      <div><strong>{source.filename}</strong><p>{source.product_name} / {source.width} x {source.height} / {seconds(source.duration_seconds * 1000)}</p><Link to={`/ecommerce-final-videos/${source.id}`}>查看商品成片详情</Link></div>
    </section>
    <section className="refine-brief">
      <h2>您希望这条视频怎么改?</h2>
      <p>用自己的话填写修改意见, AI 会分析所选商品成片和同商品素材并生成视频预览.</p>
      <label className="field">修改意见<textarea rows={4} maxLength={2000} value={brief} onChange={e=>{setBrief(e.target.value);setError('')}} placeholder="自由描述您的意见, 例如: 厨房展示部分短一点, 多展示产品细节, 保留原配音" /></label>
      <label className="field">时长目标<select value={durationPreference} onChange={e=>setDurationPreference(e.target.value as typeof durationPreference)}><option value="ai_recommend">由 AI 建议, 确认实际变化</option><option value="preserve">保持原时长</option><option value="shorten">缩短成片</option></select></label>{durationPreference==='shorten'&&<label className="field">目标时长, 秒<input type="number" min="0.001" max={(sourceMs-1)/1000} step="0.001" value={target} aria-invalid={!!durationError} aria-describedby={durationError?'source-duration-error':undefined} onChange={e=>setTarget(e.target.value)}/></label>}
      {durationError&&<p id="source-duration-error" role="alert" className="refine-risk">{durationError}</p>}
      <p className="refine-note">AI 根据修改意向自主匹配同商品素材并完成画面, 配音和时长调整. 播放核对后另存到商品成片库, 原成片保持不变.</p>
      {error&&<p role="alert" className="refine-error">{error}</p>}
      <button className="btn-primary" disabled={busy||!brief.trim()||!!durationError} onClick={create}>{busy?<Loader2 size={16} className="animate-spin"/>:<WandSparkles size={16}/>}开始精修</button>
    </section>
  </div>;
}

function SourcePicker() {
  const [params] = useSearchParams();
  const preselected = Number(params.get('source_asset_id'));
  const [sources, setSources] = useState<RefinementSource[]>([]);
  const [tasks, setTasks] = useState<VideoRefinementTaskSummary[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [sourcesError, setSourcesError] = useState('');
  const [tasksError, setTasksError] = useState('');
  const [selectionError, setSelectionError] = useState('');
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState<'sources'|'tasks'>('sources');
  const [search, setSearch] = useState('');
  const [product, setProduct] = useState('');
  const [duration, setDuration] = useState('');
  const [aspect, setAspect] = useState('');
  const [recent, setRecent] = useState('');
  const [sourcesRetry, setSourcesRetry] = useState(0);
  const [tasksRetry, setTasksRetry] = useState(0);
  useEffect(()=>{
    let active = true;
    setSourcesLoading(true); setSourcesError(''); setSelectionError('');
    videoRefinementApi.sources().then(assets=>{
      if(!active)return;
      setSources(assets);
      if(preselected){
        if(assets.some(row=>row.id===preselected))setSelected(preselected);
        else setSelectionError('预选商品成片不存在或不可用, 请重新选择');
      }
    }).catch(error=>{if(active)setSourcesError(errorMessage(error, '商品成片加载失败, 请重试'))}).finally(()=>{if(active)setSourcesLoading(false)});
    return ()=>{active=false};
  },[preselected,sourcesRetry]);
  useEffect(()=>{
    let active = true;
    setTasksLoading(true); setTasksError('');
    videoRefinementApi.list().then(rows=>{if(active)setTasks(rows)})
      .catch(error=>{if(active)setTasksError(errorMessage(error, '精修任务加载失败, 请重试'))})
      .finally(()=>{if(active)setTasksLoading(false)});
    return ()=>{active=false};
  },[tasksRetry]);
  const products = useMemo(()=>Array.from(new Map(sources.map(row=>[row.product_id,row.product_name])).entries()),[sources]);
  const visible = sources.filter(row=>(!search||`${row.filename} ${row.product_name} ${(row.taxonomy_tags??[]).map(tag=>tag.name).join(' ')}`.toLowerCase().includes(search.trim().toLowerCase()))&&(!product||row.product_id===Number(product))&&(!duration||(duration==='short'?row.duration_seconds<15:duration==='medium'?row.duration_seconds>=15&&row.duration_seconds<=30:row.duration_seconds>30))&&(!aspect||(aspect==='portrait'?row.height>row.width:row.width>=row.height))&&(!recent||Date.now()-new Date(row.created_at).getTime()<=Number(recent)*86400000));
  const source = sources.find(row=>row.id===selected);
  const loading = tab==='sources'?sourcesLoading:tasksLoading;
  const error = tab==='sources'?sourcesError:tasksError;
  if(source)return <><button className="refine-text-button" onClick={()=>setSelected(null)}><ArrowLeft size={15}/>重新选择商品成片</button><SourceSetup key={source.id} source={source}/></>;
  return <>
    <div className="refine-tabs" role="tablist" aria-label="精修入口">
      <button role="tab" aria-selected={tab==='sources'} onClick={()=>setTab('sources')}>选择商品成片</button>
      <button role="tab" aria-selected={tab==='tasks'} onClick={()=>setTab('tasks')}>精修任务 <span>{tasksLoading?'...':tasksError?'加载失败':tasks.length}</span></button>
    </div>
    {error&&<div className="refine-error" role="alert">{error}<button onClick={()=>tab==='sources'?setSourcesRetry(value=>value+1):setTasksRetry(value=>value+1)}>重新加载</button></div>}
    {tab==='sources'&&selectionError&&<div className="refine-error" role="alert">{selectionError}</div>}
    {loading?<div className="refine-empty" role="status"><Loader2 className="animate-spin"/>{tab==='sources'?'正在加载商品成片':'正在加载精修任务'}</div>:tab==='sources'?<>
      <div className="refine-filters">
        <label className="refine-search"><Search size={16}/><input aria-label="搜索商品成片" value={search} onChange={e=>setSearch(e.target.value)} placeholder="文件名, 商品或标签" /></label>
        <select aria-label="筛选商品" value={product} onChange={e=>setProduct(e.target.value)}><option value="">全部商品</option>{products.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select>
        <select aria-label="筛选时长" value={duration} onChange={e=>setDuration(e.target.value)}><option value="">全部时长</option><option value="short">15 秒内</option><option value="medium">15-30 秒</option><option value="long">30 秒以上</option></select>
        <select aria-label="筛选画幅" value={aspect} onChange={e=>setAspect(e.target.value)}><option value="">全部画幅</option><option value="portrait">竖屏</option><option value="landscape">横屏或方形</option></select>
        <select aria-label="筛选创建时间" value={recent} onChange={e=>setRecent(e.target.value)}><option value="">全部时间</option><option value="7">近 7 天</option><option value="30">近 30 天</option></select>
      </div>
      {(!sourcesError||sources.length>0)&&<div className="refine-source-toolbar"><p className="refine-note">从商品成片库选择一条成片开始精修. 共 {visible.length} 条结果.</p><button className="btn-secondary" onClick={()=>setSourcesRetry(value=>value+1)}><RefreshCw size={14}/>刷新成片</button></div>}
      <div className="refine-source-grid">{visible.map(row=><article className="refine-source-choice" key={row.id}>
        <video className="refine-source-thumbnail" src={mediaUrl(row.object_url)} controls playsInline preload="metadata" aria-label={`预览 ${row.filename}`}/>
        <div className="refine-source-info"><strong>{row.filename}</strong><p>{row.product_name}</p><span>{seconds(row.duration_seconds*1000)} / {row.width} x {row.height}</span>
          <VideoTaxonomySummary asset={sourceTaxonomy(row)} compact showAiTags={false}/>
          <div className="refine-inline-actions"><Link className="btn-secondary" to={`/ecommerce-final-videos/${row.id}`}><Eye size={14}/>查看详情</Link><a className="btn-secondary" href={mediaUrl(row.object_url)} download><Download size={14}/>下载</a></div>
          <button className="btn-primary w-full justify-center" onClick={()=>setSelected(row.id)} aria-label={`选择 ${row.filename}`}><WandSparkles size={14}/>选择此成片</button>
        </div>
      </article>)}</div>
      {!sourcesError&&!visible.length&&<div className="refine-empty"><Film size={32}/><h2>{sources.length?'没有符合筛选条件的商品成片':'商品成片库还没有可用视频'}</h2><p>{sources.length?'调整搜索或筛选条件后重试.':'请先为电商商品上传成片, 完成文件校验后即可开始精修, 无需库内分析.'}</p><Link className="btn-secondary" to="/ecommerce-final-videos">打开商品成片库</Link></div>}
    </>:<div className="refine-task-list">{tasks.map(task=><Link key={task.id} to={`/video-refinements/${task.id}`}><div><strong>{task.source_filename}</strong><p>{task.user_brief}</p></div><span>{statusLabels[task.status]} / r{task.current_revision}</span><ChevronRight size={18}/></Link>)}{!tasksError&&!tasks.length&&<div className="refine-empty"><h2>还没有精修任务</h2><p>选择商品成片并保存修改意见后, 任务会出现在这里.</p></div>}</div>}
  </>;
}

function TaskWorkspace({taskId}: {taskId: number}) {
  const [params,setParams]=useSearchParams();
  const autoAnalyzeStarted=useRef(false);
  const navigate = useNavigate();
  const [task, setTask] = useState<VideoRefinementTask|null>(null);
  const [draft, setDraft] = useState<VideoRefinementPlanItem[]>([]);
  const [activeId, setActiveId] = useState('');
  const [editing, setEditing] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [noticeAction, setNoticeAction] = useState('');
  const [pollError,setPollError]=useState('');
  const pollingFailures=useRef(0);
  const [retry, setRetry] = useState(0);
  const [playTime, setPlayTime] = useState(0);
  const [mediaError, setMediaError] = useState(false);
  const [previewReviewed, setPreviewReviewed] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  const previewPlayer=useRef<HTMLVideoElement>(null);
  const previewStop=useRef<number|null>(null);
  const mounted = useRef(true);
  const actionPending = useRef(false);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
  function apply(next: VideoRefinementTask) {setTask(next);setDraft(next.plan?.items.map(row=>structuredClone(row))||[]);setEditing(false);setAcknowledged(false);setPreviewReviewed(false);setActiveId(current=>next.plan?.items.some(row=>row.item_id===current)?current:next.plan?.items[0]?.item_id||'')}
  useEffect(()=>{
    let active = true;
    setTask(null);setError('');setNoticeAction('');
    videoRefinementApi.get(taskId).then(next=>{if(active)apply(next)}).catch(error=>{if(active)setError(errorMessage(error))});
    return()=>{active=false};
  },[taskId,retry]);
  const pendingPreview = task?.previews?.some(row=>row.revision===task.current_revision&&(row.status==='queued'||row.status==='rendering'));
  const pendingAnalysis=task?.analysis_state?.status==='queued'||task?.analysis_state?.status==='analyzing';
  const workerUnavailable=!!task?.worker_state&&task.worker_state.status!=='running';
  const waitingForWorker=workerUnavailable&&task?.status!=='CANCELLED'&&task?.status!=='COMPLETED';
  const pendingVoice=task?.voiceovers?.some(row=>row.status==='queued'||row.status==='generating');
  useEffect(()=>{
    if(!pendingPreview&&!pendingAnalysis&&!pendingVoice&&!waitingForWorker)return;
    let active = true;
    const controller = new AbortController();
    let timer: ReturnType<typeof window.setTimeout>;
    async function poll() {
      try {
        const next = await videoRefinementApi.state(taskId,controller.signal);
        if(!active)return;
        pollingFailures.current=0;setPollError('');setError(current=>current==='预览状态加载失败, 请重新加载'?'':current);
        if(next.current_revision!==task?.current_revision){
          const updated=await videoRefinementApi.get(taskId,controller.signal);
          if(active)apply(updated);
        } else setTask(current=>current?{...current,...next}:current);
      } catch {
        if(active){pollingFailures.current+=1;if(pollingFailures.current>=3)setPollError('任务状态更新暂时中断, 正在自动重试')}
      } finally {
        if(active)timer=window.setTimeout(poll,Math.min(30000,2000*2**Math.min(pollingFailures.current,4)));
      }
    }
    timer=window.setTimeout(poll,2000);
    return()=>{active=false;window.clearTimeout(timer);controller.abort()};
  },[taskId,pendingPreview,pendingAnalysis,pendingVoice,waitingForWorker,task?.current_revision]);
  const dirty = JSON.stringify(draft)!==JSON.stringify(task?.plan?.items||[]);
  const active = draft.find(row=>row.item_id===activeId);
  const activeVoice = task?.voiceovers?.find(row=>row.id===active?.voiceover_id);
  async function action(name: string, work: () => Promise<VideoRefinementTask>) {
    if(actionPending.current)return;
    actionPending.current=true;
    setBusy(name);setError('');setNoticeAction('');
    try {const next=await work();if(mounted.current){if(name==='voiceover')setTask(next);else apply(next);setNoticeAction(name)}}
    catch(error){if(mounted.current)setError(errorMessage(error))}finally{actionPending.current=false;if(mounted.current)setBusy('')}
  }
  useEffect(()=>{
    if(!task||params.get('analyze')!=='1'||autoAnalyzeStarted.current||task.plan||task.status!=='CREATED')return;
    autoAnalyzeStarted.current=true;
    const nextParams=new URLSearchParams(params);nextParams.delete('analyze');setParams(nextParams,{replace:true});
    void action('analyze',()=>generatePreview(task));
  },[task?.id,params]);
  function update(patch: Partial<VideoRefinementPlanItem>) {if(patch.script!==undefined||patch.tts_speaker!==undefined)patch.voiceover_id=null;setEditing(true);setDraft(rows=>rows.map(row=>row.item_id===activeId?{...row,...patch}:row));setAcknowledged(false)}
  function seek(ms: number) {const video=player.current;if(video){video.pause();video.currentTime=Math.max(0,Math.min(ms,task?.source_snapshot.source.duration_ms||0))/1000;setPlayTime(video.currentTime*1000)}}
  if(!task)return <div className="refine-empty">{error?<><p role="alert">{error}</p><button className="btn-secondary" onClick={()=>setRetry(value=>value+1)}>重新加载</button><Link to="/video-refinements">返回精修工作台</Link></>:<><Loader2 className="animate-spin"/>正在加载任务</>}</div>;
  const source=task.source_snapshot.source;
  const activeMaterial=task.source_snapshot.material_catalog.find(row=>row.asset_id===active?.material_asset_id);
  const actualSegments=task.plan?.video_track?.filter(clip=>clip.source_kind==='material'&&active&&clip.item_ids.includes(active.item_id)).map(clip=>({asset_id:clip.source_asset_id,source_range:clip.source_range}))??active?.material_segments;
  const cancelled=task.status==='CANCELLED'||task.status==='COMPLETED';
  const legacy=task.source_snapshot.is_mock;
  const v2=task.source_snapshot.schema_version==='video_refinement_snapshot.v2';
  const showingAnalysisNotice=noticeAction==='analyze'||noticeAction==='requirements';
  const notice=showingAnalysisNotice?analysisNotice(task):actionNotices[noticeAction];
  const noticePending=showingAnalysisNotice&&pendingAnalysis;
  const noticeNeedsInput=showingAnalysisNotice&&task.analysis_state?.status==='needs_user_input';
  const timeline=task.plan?.analysis?.timeline.length?task.plan.analysis.timeline:task.source_snapshot.timeline;
  const preview=task.previews?.find(row=>row.revision===task.current_revision&&row.plan_hash===task.plan_hash&&row.status!=='superseded');
  const delta=v2&&!dirty?(task.plan?.summary.duration_delta_ms||0):draft.filter(row=>row.enabled).reduce((sum,row)=>sum+row.duration_delta_ms,0);
  const enabled=draft.filter(row=>row.enabled);
  const risks=enabled.some(row=>row.confidence_level==='LOW'||row.type==='VOICE_REPLACE'||row.type==='SUBTITLE_EDIT'||(row.type==='VISUAL_REPLACE'&&!row.material_asset_id));
  return <>
    <div className="refine-task-heading"><Link to="/video-refinements"><ArrowLeft size={15}/>返回精修工作台</Link><div><h2>{source.filename}</h2><span className="refine-status">{statusLabels[task.status]} / r{task.current_revision}</span></div><p>{source.product_name} / SKU 未核实 / <LockKeyhole size={13}/>画幅锁定 {source.aspect_ratio} / 源片 {seconds(source.duration_ms)}</p></div>
    {legacy&&<div className="refine-legacy" role="status"><p>这条历史方案使用了示例数据. 使用系统真实视频和素材创建新任务, 原方案保留为历史记录.</p><button className="btn-primary" disabled={!!busy} onClick={async()=>{setBusy('restart');setError('');try{const next=await videoRefinementApi.create({source_asset_id:task.source_asset_id,user_brief:task.user_brief,expected_result:task.expected_result,focus:task.focus});navigate(`/video-refinements/${next.id}`)}catch(error){setError(errorMessage(error))}finally{setBusy('')}}}>使用真实数据重新开始</button></div>}
    {error&&<div className="refine-error" role="alert">{error}<button disabled={!!busy} onClick={()=>{if(dirty){setError('请先保存或放弃本地修改, 再重新加载')}else setRetry(value=>value+1)}}>重新加载</button></div>}
    {notice&&<p className={noticePending||noticeNeedsInput?'refine-note':'refine-notice'} role="status">{noticePending?<Loader2 size={16} className="animate-spin"/>:noticeNeedsInput?<AlertTriangle size={16}/>:<Check size={16}/>} {notice}</p>}
    {pollError&&<p className="refine-note" role="status">{pollError}</p>}
    <div className="refine-workspace">
      <section className="refine-viewer" aria-label="源片与时间轴">
        <div className="refine-section-heading"><h3>{source.version.toUpperCase()} 商品成片</h3><span>源文件保持原样</span></div>
        <video ref={player} src={mediaUrl(source.object_url)} controls playsInline preload="metadata" onTimeUpdate={()=>setPlayTime((player.current?.currentTime||0)*1000)} onError={()=>setMediaError(true)} onLoadedMetadata={()=>setMediaError(false)} aria-label="V1 商品成片播放器" />
        {mediaError&&<p className="refine-error" role="alert">源成片无法播放, 请检查媒体地址或返回商品成片库验证文件.</p>}
        <div className="refine-playback"><button onClick={()=>seek(playTime-1000/source.fps)} aria-label="前一帧"><ChevronLeft size={18}/></button><output>{timecode(playTime)}</output><button onClick={()=>seek(playTime+1000/source.fps)} aria-label="后一帧"><ChevronRight size={18}/></button><span>{timecode(source.duration_ms)}</span></div>
        <input className="refine-scrubber" aria-label="源片播放位置" type="range" min={0} max={source.duration_ms} step={Math.max(1,Math.round(1000/source.fps))} value={playTime} onChange={e=>seek(Number(e.target.value))}/>
        <div className="refine-section-heading"><h3>源片时间轴</h3><span>{legacy?'历史示例时间轴':task.plan?.analysis?'AI 镜头分析':'系统保存的来源'}</span></div>
        <div className="refine-shot-track">{timeline.map(shot=><button key={shot.shot_id} style={{flex:shot.end_ms-shot.start_ms}} onClick={()=>seek(shot.start_ms)} title={`${shot.label}, ${timecode(shot.start_ms)} - ${timecode(shot.end_ms)}`}>{shot.label}</button>)}</div>
        {preview&&<section className="refine-preview" aria-label="精修预览"><div className="refine-section-heading"><h3>精修预览 / r{preview.revision}</h3><span>{preview.status==='ready'?'待核对':preview.status==='accepted'?'已保存':preview.status==='failed'?'生成失败':'生成中'}</span></div>
          {(preview.status==='queued'||preview.status==='rendering')&&<p role="status"><Loader2 size={18} className="animate-spin"/>{preview.status==='queued'?'等待生成完整预览':'正在渲染完整预览'}. 完成后自动更新.</p>}
          {preview.status==='failed'&&<p className="refine-error" role="alert">{preview.error_message}</p>}
          {(preview.status==='ready'||preview.status==='accepted')&&<><video ref={previewPlayer} key={preview.id} src={mediaUrl(preview.output_url)} controls playsInline preload="metadata" aria-label="精修预览播放器" onTimeUpdate={()=>{if(previewStop.current!==null&&previewPlayer.current&&previewPlayer.current.currentTime>=previewStop.current){previewPlayer.current.pause();previewStop.current=null}}}/>{v2&&active&&<button className="btn-secondary" onClick={()=>{const clip=task.plan?.video_track?.find(c=>c.item_ids.includes(active.item_id));const mapped=task.plan?.source_to_output_map?.find(c=>c.source_range.start_ms<=active.range.start_ms&&active.range.start_ms<=c.source_range.end_ms);const range=clip?.output_range||mapped?.output_range;if(previewPlayer.current&&range){previewPlayer.current.currentTime=Math.max(0,range.start_ms-1500)/1000;previewStop.current=Math.min(task.plan!.summary.expected_duration_ms,range.end_ms+1500)/1000;void previewPlayer.current.play().catch(()=>setError('连续预览播放失败, 请使用播放器控件重试'))}}}>连续核对当前修改前后</button>}<p>实测 {seconds((preview.validation.duration_seconds||0)*1000)} / {preview.validation.width} x {preview.validation.height}. 媒体与解码检查已通过.</p><p className="refine-note">请对照源片核对剪切点, 原声衔接, 字幕和商品. 自动媒体检查不代替内容核对.</p>
            {preview.status==='ready'&&!editing&&<><label className="refine-risk-ack"><input type="checkbox" checked={previewReviewed} onChange={e=>setPreviewReviewed(e.target.checked)}/>我已播放预览并核对音画, 字幕和商品</label><button className="btn-primary" disabled={!!busy||!previewReviewed||dirty} onClick={()=>action('accept',()=>videoRefinementApi.accept(task.id,preview.id,{revision:task.current_revision,plan_hash:task.plan_hash!}))}>{busy==='accept'&&<Loader2 size={16} className="animate-spin"/>}接受预览并另存精修版本</button></>}
          </>}
        </section>}
        {task.output_asset_id&&<Link className="btn-primary" to={`/ecommerce-final-videos/${task.output_asset_id}`}>查看精修 V{task.output_version} 成片</Link>}
      </section>
      <section className="refine-plan" aria-label="精修方案">
        <div className="refine-section-heading"><h3>精修目标</h3><span>{legacy?'历史示例方案':task.plan?.analysis?.mode==='sampled_frames'?'AI 关键帧分析':'真实 AI 分析'}</span></div><p className="refine-objective">{task.user_brief}</p>{task.expected_result&&<p className="refine-note">预期结果: {task.expected_result}</p>}
        {v2&&<><VoiceMatchSummary selection={task.plan?.analysis?.voice_selection} manualSpeaker={task.options?.tts_speaker}/><GoalEditor task={task} disabled={!!busy||cancelled||dirty||!!pendingAnalysis} action={action}/><EvaluationProgress task={task}/>{!!task.plan?.material_evaluation?.failed&&!task.plan.material_evaluation.partial_accepted&&!pendingAnalysis&&!task.plan.material_evaluation.complete&&<button className="btn-secondary" disabled={!!busy||cancelled||dirty} onClick={()=>action('requirements',async()=>{const next=await videoRefinementApi.requirements(task.id,{revision:task.current_revision,accept_partial_evaluation:true});return videoRefinementApi.autoPreview(task.id,next.current_revision)})}>接受当前已评估范围并重新规划</button>}<RequirementPanel task={task} disabled={!!busy||cancelled||dirty||!!pendingAnalysis} action={action}/></>}
        {!v2&&!legacy&&!cancelled&&<button className="btn-secondary" disabled={!!busy||dirty} onClick={()=>action('refresh',()=>videoRefinementApi.refreshMaterials(task.id,task.current_revision))}>刷新素材并使用音画独立新规则</button>}
        {task.analysis_state?.status==='failed'&&<div className="refine-error" role="alert">{task.analysis_state.error_message}<button disabled={!!busy||dirty} onClick={()=>action('analyze',()=>generatePreview(task))}>重试后台分析</button></div>}
        {!task.plan?<div className="refine-no-plan"><h3>目标已保存, 现在生成精修预览</h3><p>AI 自动匹配同商品无字幕素材, 仅按明确要求生成局部配音, 完成音画检查后生成视频预览.</p><button className="btn-primary" disabled={!!busy||cancelled||legacy||!!pendingAnalysis||workerUnavailable} onClick={()=>action('analyze',()=>generatePreview(task))}>{busy==='analyze'?<Loader2 className="animate-spin" size={16}/>:<WandSparkles size={16}/>}{v2?'AI 精修并生成预览':'AI 分析并生成方案'}</button>{busy==='analyze'&&<p role="status">正在读取真实媒体并调用 AI, 请稍候.</p>}</div>:<>
          {v2&&!cancelled&&!editing&&task.status!=='PLAN_CONFIRMED'&&<div className="refine-inline-actions"><button className="btn-primary" disabled={!!busy||dirty||!!pendingAnalysis||workerUnavailable} onClick={()=>action('analyze',()=>generatePreview(task))}>{(busy==='analyze'||pendingAnalysis)&&!workerUnavailable?<Loader2 size={16} className="animate-spin"/>:<WandSparkles size={16}/>}AI 精修并生成预览</button><span className="refine-note">自动选择可复用的同商品素材, 按目标自主完成画面与配音</span></div>}
          <div className="refine-section-heading"><h3>{editing?'修改方案':'完整精修方案'}</h3><button className="refine-text-button" disabled={!!busy||cancelled||!!pendingAnalysis} onClick={()=>{if(editing){setDraft(task.plan!.items.map(row=>structuredClone(row)));setAcknowledged(false)}setEditing(value=>!value)}}><Pencil size={14}/>{editing?'放弃修改':'修改方案'}</button></div>
          <div className="refine-duration"><span>源片 <strong>{seconds(source.duration_ms)}</strong></span><span>预计 <strong>{seconds(source.duration_ms+delta)}</strong></span><span>{delta>0?'+':''}{seconds(delta)}{dirty?' / 未保存':''}</span></div>
          <p className="refine-note">{delta?'时长变化来自启用的裁切和片段插入, 实际结果需渲染后测量.':'当前方案保持源片时长.'}</p>
          {!dirty&&task.plan.automatic_adjustments?.map((adjustment,index)=><p className="refine-note" role="status" key={index}>{adjustment.message} 素材 {adjustment.asset_id}: {timecode(adjustment.before_range.start_ms)} - {timecode(adjustment.before_range.end_ms)} 调整为 {timecode(adjustment.after_range.start_ms)} - {timecode(adjustment.after_range.end_ms)}.</p>)}
          {v2&&task.plan.execution_blockers.length>0&&<div className="refine-blockers" role="status"><h4>当前方案缺少的执行条件</h4><p className="refine-note">点击 AI 精修并生成预览, 系统会自动核验素材, 准备配音和检查插入边界.</p>{task.plan.execution_blockers.map((row,index)=><p key={index}>{row.message}</p>)}</div>}
          {!draft.length&&<p className="refine-note">当前评估没有需要执行的剪辑动作. 请查看逐条要求的处理结果.</p>}
          <div className="refine-items">{draft.map(item=><article key={item.item_id} className={`${item.item_id===activeId?'is-active':''} ${item.enabled?'':'is-off'}`}>
            <label className="refine-enable"><input type="checkbox" aria-label={`启用${itemLabels[item.type]}`} checked={item.enabled} disabled={!editing||!!busy||cancelled} onChange={()=>{setActiveId(item.item_id);setDraft(rows=>rows.map(row=>row.item_id===item.item_id?{...row,enabled:!row.enabled}:row));setAcknowledged(false)}}/></label>
            <button className="refine-item-select" onClick={()=>{setActiveId(item.item_id);seek(item.range.start_ms)}}><span>{itemLabels[item.type]}<small>{item.confidence_level==='LOW'?'低置信度, 需要确认':item.status==='HUMAN_EDITED'?'人工已修改':legacy?'历史示例建议':'AI 建议'}</small></span><strong>{item.operation}</strong><span>{item.type==='SEGMENT_INSERT'?`插入于 ${timecode(item.range.start_ms)}`:`${timecode(item.range.start_ms)} - ${timecode(item.range.end_ms)}`}</span></button>
          </article>)}</div>
          {active&&<section className="refine-inspector" aria-label="修改项详情"><h4>{itemLabels[active.type]}详情</h4><p>{active.reason}</p>
            {activeMaterial&&!v2&&<><p className="refine-note">候选素材: {activeMaterial.filename}{activeMaterial.analysis_status==='analysis_failed'?' / 分析失败, 仍可使用':''}</p><video className="refine-material-player" key={activeMaterial.asset_id} src={mediaUrl(activeMaterial.object_url)} controls playsInline preload="metadata" aria-label="候选素材播放器"/></>}
            {editing&&<fieldset disabled={!!busy||cancelled}><div className="refine-time-fields"><label className="field">{active.type==='SEGMENT_INSERT'?'插入位置, 秒':'起始时间, 秒'}<input type="number" min={0} max={source.duration_ms/1000} step="0.001" value={active.range.start_ms/1000} onChange={e=>{const start_ms=Math.round(Number(e.target.value)*1000);update({range:{...active.range,start_ms,...(active.type==='SEGMENT_INSERT'?{end_ms:start_ms}:{})}})}}/></label>{active.type!=='SEGMENT_INSERT'&&<label className="field">结束时间, 秒<input type="number" min={0} max={source.duration_ms/1000} step="0.001" value={active.range.end_ms/1000} onChange={e=>update({range:{...active.range,end_ms:Math.round(Number(e.target.value)*1000)}})}/></label>}</div>
              <label className="field">修改描述<textarea rows={2} maxLength={2000} value={active.operation} onChange={e=>update({operation:e.target.value})}/></label>
              {(!v2&&active.type==='PACE_RECUT')&&<label className="field">缩短时长, 秒<input type="number" min={0} step="0.001" value={-active.duration_delta_ms/1000} onChange={e=>update({duration_delta_ms:-Math.round(Number(e.target.value)*1000)})}/></label>}
              {(!v2&&active.type==='PACE_RECUT')&&<label className="field">删除位置<select value={active.cut_from||'end'} onChange={e=>update({cut_from:e.target.value as 'start'|'end'})}><option value="start">区间开头</option><option value="end">区间末尾</option></select></label>}
              {(!v2&&active.type==='SEGMENT_INSERT')&&<label className="field">插入时长, 秒<input type="number" min="0.001" step="0.001" value={active.duration_delta_ms/1000} onChange={e=>update({duration_delta_ms:Math.round(Number(e.target.value)*1000)})}/></label>}
              {!v2&&active.type==='SEGMENT_INSERT'&&<p className="refine-note">插入片段会同步使用素材原声和原字幕.</p>}
              {!v2&&(active.type==='VISUAL_REPLACE'||active.type==='SEGMENT_INSERT')&&<><label className="field">同商品候选素材<select className="input-select" value={active.material_asset_id||''} onChange={e=>update({material_asset_id:Number(e.target.value)||null})}><option value="">待选择素材</option>{task.source_snapshot.material_catalog.map(row=><option key={row.asset_id} value={row.asset_id}>{row.filename} / {row.duration_ms?seconds(row.duration_ms):'渲染前验证时长'}{row.analysis_status==='analysis_failed'?' / 分析失败, 仍可使用':''}</option>)}</select><span>候选按商品筛选, 请核对 SKU 和实际内容. 分析失败不影响选用, 文件须可读取.</span></label><label className="field">素材起点, 秒<input type="number" min={0} step="0.001" value={(active.material_start_ms||0)/1000} onChange={e=>update({material_start_ms:Math.round(Number(e.target.value)*1000)})}/></label>{active.type==='VISUAL_REPLACE'&&<label className="field">音频处理<select value={active.audio_strategy||'use_material'} onChange={e=>update({audio_strategy:e.target.value as 'keep_source'|'use_material'})}><option value="use_material">采用素材原声, 保持素材音画绑定</option><option value="keep_source">保留源片原声, 仅替换画面</option></select></label>}</>}
              {(active.type==='VOICE_REPLACE'||(v2&&(active.type==='SEGMENT_INSERT'||active.edit_intent==='compress_expression')))&&<label className="field">新台词<textarea rows={3} maxLength={4000} value={active.script} onChange={e=>update({script:e.target.value})}/></label>}
              {active.type==='SUBTITLE_EDIT'&&<label className="field">新字幕<textarea rows={3} maxLength={4000} value={active.subtitle_text} onChange={e=>update({subtitle_text:e.target.value})}/></label>}
              {v2&&(active.type==='VISUAL_REPLACE'||active.type==='SEGMENT_INSERT'||active.edit_intent==='compress_expression')&&<MaterialSegmentsEditor key={active.item_id} task={task} item={active} disabled={!!busy||cancelled} onChange={update}/>}
              {v2&&active.type==='VISUAL_REPLACE'&&<label className="field">画面修改目的<select value={active.edit_intent||'replace_visual'} onChange={e=>update({edit_intent:e.target.value as VideoRefinementPlanItem['edit_intent']})}><option value="replace_visual">替换画面或增加辅助镜头, 保留原配音</option><option value="reduce_shot_share">减少当前镜头占比, 保留完整台词</option></select></label>}
              {v2&&active.type==='PACE_RECUT'&&<><label className="field">裁切目的<select value={active.edit_intent||'remove_content'} onChange={e=>update({edit_intent:e.target.value as VideoRefinementPlanItem['edit_intent'],duration_delta_ms:0,removed_ranges:[]})}><option value="remove_pause">删除确认的停顿</option><option value="remove_content">删除完整内容与台词</option><option value="compress_expression">精简台词并局部重配</option></select></label>{active.edit_intent!=='compress_expression'&&<><button type="button" className="btn-secondary" onClick={()=>update({removed_ranges:[...(active.removed_ranges||[]),{...active.range}],duration_delta_ms:0})}>添加待删除区间</button>{(active.removed_ranges||[]).map((range,index)=><div className="refine-time-fields" key={index}>{(['start_ms','end_ms'] as const).map((key,i)=><label className="field" key={key}>{i?'删除终点, 秒':'删除起点, 秒'}<input type="number" min={0} max={source.duration_ms/1000} step="0.001" value={range[key]/1000} onChange={e=>update({removed_ranges:active.removed_ranges!.map((r,j)=>j===index?{...r,[key]:Math.round(Number(e.target.value)*1000)}:r),duration_delta_ms:0,boundary_reviewed:false})}/></label>)}<button type="button" className="refine-text-button" onClick={()=>update({removed_ranges:active.removed_ranges!.filter((_,j)=>j!==index),duration_delta_ms:0})}>移除此删除区间</button></div>)}</>}</>}
              {v2&&(active.type==='PACE_RECUT'||active.type==='SEGMENT_INSERT'||active.type==='VOICE_REPLACE')&&<label className="refine-risk-ack"><input type="checkbox" checked={!!active.boundary_reviewed} onChange={e=>update({boundary_reviewed:e.target.checked})}/>我已播放并核对完整句子边界, 前后台词与发音衔接</label>}
              {v2&&active.narration_strategy==='tts'&&<><label className="field">字幕处理<select value={active.subtitle_strategy||'regenerate'} onChange={e=>update({subtitle_strategy:e.target.value as VideoRefinementPlanItem['subtitle_strategy']})}><option value="regenerate">按可信配音时间生成字幕</option><option value="none">本段不生成字幕</option></select></label><label className="field">无讲话留白的原因, 可选<input maxLength={500} value={active.narration_padding_reason||''} onChange={e=>update({narration_padding_reason:e.target.value})}/></label></>}
            </fieldset>}
            {v2&&editing&&active.narration_strategy==='tts'&&<VoiceoverEditor task={task} item={active} disabled={!!busy||cancelled} dirty={dirty} action={action} onChange={update}/>}
            {v2&&<p className="refine-note">{active.narration_strategy==='tts'?'新增或改写台词使用局部 TTS':active.narration_strategy==='cut_source'?'完整裁切确认区间的原音频':'原成片配音保留'} / 素材拍摄原声关闭</p>}
            {v2&&active.type==='PACE_RECUT'&&<p className="refine-note">将删除的台词: {(task.plan?.analysis as unknown as {context?: {transcript: RefinementSnapshot['transcript']}})?.context?.transcript.filter(c=>(active.removed_ranges?.length?active.removed_ranges:[active.range]).some(r=>r.start_ms<c.end_ms&&c.start_ms<r.end_ms)).map(c=>c.text).join(' / ')||'暂无可信转写, 请播放并核对边界'}</p>}
            {(active.script||active.subtitle_text)&&!editing&&<p>文本: {active.script||active.subtitle_text}</p>}
            {!editing&&activeVoice?.parameters.selection&&<div className="refine-note"><p>配音音色: {activeVoice.parameters.selection.name} / {activeVoice.parameters.selection.mode==='manual'?'手动指定':'自动适配'}</p><p>{activeVoice.parameters.selection.reason}</p></div>}
            <p className="refine-risk"><AlertTriangle size={15}/>{active.risk||'区间与修改内容需人工核对.'}</p>{active.dependencies.length>0&&<p className="refine-note">依赖: {active.dependencies.join(', ')}</p>}
            {active.evidence.length>0&&<p className="refine-evidence">分析依据: {active.evidence.join(', ')}</p>}
            {!editing&&actualSegments?.map((segment,index)=><p className="refine-note" key={index}>实际使用素材 {segment.asset_id}: {timecode(segment.source_range.start_ms)} - {timecode(segment.source_range.end_ms)} / {seconds(segment.source_range.end_ms-segment.source_range.start_ms)}</p>)}
            {!dirty&&task.plan.local_selection?.filter(selection=>selection.item_id===active.item_id).map(selection=><div key={selection.candidate_id} className="refine-note"><p>素材 {selection.asset_id} 选择依据: {selection.reason}</p><p>采样核验范围: {timecode(selection.safe_range.start_ms)} - {timecode(selection.safe_range.end_ms)}. 已采样 {selection.verification.sampled_times_ms.length} 个时间点, 不代表连续观看.</p><p>商品依据: {selection.product_match.evidence}{!selection.product_match.sku_verified?' / SKU 尚需人工核对':''}</p>{selection.verification.risks.map((risk,index)=><p key={index}>{risk}</p>)}</div>)}
          </section>}
          <details className="refine-findings"><summary>发现但不建议自动修改</summary>{task.plan.findings_not_applied.map(text=><p key={text}>{text}</p>)}</details>
          <div className="refine-plan-actions">{editing?<button className="btn-primary" disabled={!!busy||!dirty||cancelled||legacy} onClick={()=>action('save',()=>videoRefinementApi.updateDraft(task.id,{revision:task.current_revision,items:draft}))}>{busy==='save'&&<Loader2 className="animate-spin" size={16}/>}保存方案修改</button>:task.status==='COMPLETED'?<p className="refine-confirmed"><Check size={18}/>精修 V{task.output_version} 已保存.</p>:task.status==='PLAN_CONFIRMED'?<><p className="refine-confirmed"><Check size={18}/>r{task.confirmed_revision} 已整体确认. {legacy?'请使用真实数据重新开始.':'可以生成视频预览.'}</p>{!legacy&&<><button className="btn-primary" disabled={!!busy||dirty||!!task.plan.execution_blockers.length||pendingPreview||preview?.status==='ready'} onClick={()=>action('preview',()=>videoRefinementApi.preview(task.id,{revision:task.current_revision,plan_hash:task.plan_hash!}))}>{(busy==='preview'||pendingPreview)&&<Loader2 size={16} className="animate-spin"/>}{preview?.status==='failed'?'重新生成预览':'生成精修预览'}</button>{task.plan.execution_blockers.length>0&&<p className="refine-note">请关闭无法自动执行的修改项或补充素材, 保存并重新确认后生成预览.</p>}</>}</>:<>
            {risks&&<label className="refine-risk-ack"><input type="checkbox" checked={acknowledged} disabled={!!busy||cancelled} onChange={e=>setAcknowledged(e.target.checked)}/>我已核对低置信度建议和素材, 配音, 字幕的处理限制</label>}
            <button className="btn-primary" disabled={!!busy||dirty||!enabled.length||cancelled||legacy||(v2&&!!task.plan.execution_blockers.length)||(risks&&!acknowledged)} onClick={()=>action('confirm',()=>videoRefinementApi.confirm(task.id,{revision:task.current_revision,plan_hash:task.plan_hash!,acknowledge_risks:acknowledged}))}>{busy==='confirm'?<Loader2 className="animate-spin" size={16}/>:<Check size={16}/>}确认整套方案</button>
          </>}</div>
        </>}
        {!cancelled&&<button className="refine-text-button refine-cancel" disabled={!!busy} onClick={()=>action('cancel',()=>videoRefinementApi.cancel(task.id,task.current_revision))}>取消任务</button>}
      </section>
    </div>
  </>;
}

export default function VideoRefinementWorkbench() {
  const {id} = useParams();
  return <div className="refinement-ui"><header className="refine-header"><div><h1>视频精修</h1><p>选择商品成片并描述修改意见, 核对预览后另存到商品成片库.</p></div><span>方案工作台</span></header>{id?<TaskWorkspace key={id} taskId={Number(id)}/>:<SourcePicker/>}</div>;
}
