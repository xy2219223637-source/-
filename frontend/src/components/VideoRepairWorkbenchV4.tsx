import {useEffect, useRef, useState} from 'react';
import {CheckCircle2, Loader2, Upload} from 'lucide-react';
import {Link} from 'react-router-dom';
import {mediaUrl, videoRepairApi} from '../api/client';
import type {VideoRepairAttachment, VideoRepairMaterial, VideoRepairTask} from '../api/client';

type Decision = '' | 'ACCEPT_AI' | 'REANALYZE';
type Action = Record<string, any>;

const actionLabels: Record<string, string> = {
  VISUAL_REPLACE: '局部画面替换', AUDIO_REPLACE: '音频替换', CONTENT_INSERT: '内容插入',
};
const statusLabels: Record<string, string> = {
  ANALYZING: 'AI 分析中', AWAITING_MANUAL_DECISION: '等待人工决策',
  AWAITING_MATERIAL: '等待素材', CONFIGURING: '配置方案',
  PREVIEW_QUEUED: '预览排队中', PREVIEW_RENDERING: '预览渲染中',
  PREVIEW_READY: '预览待确认', COMPLETED: '已完成', FAILED: '失败', CANCELED: '已取消',
};
function message(error: unknown) {
  const value = error as {response?: {data?: {detail?: string | {message?: string}}}; message?: string};
  const detail = value.response?.data?.detail;
  return typeof detail === 'string' ? detail : (detail?.message || value.message || '操作失败');
}

export default function VideoRepairWorkbenchV4({initialTask}: {initialTask: VideoRepairTask}) {
  const [task, setTask] = useState(initialTask);
  const [materials, setMaterials] = useState<VideoRepairMaterial[]>([]);
  const [materialsLoading, setMaterialsLoading] = useState(true);
  const [decision, setDecision] = useState<Decision>('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [attachment, setAttachment] = useState<VideoRepairAttachment | null>(null);
  const [tts, setTts] = useState('');
  const [insertAt, setInsertAt] = useState('');
  const [materialId, setMaterialId] = useState('');
  const [trimStart, setTrimStart] = useState('0');
  const materialPreview=useRef<HTMLVideoElement|null>(null);

  const diagnosis = task.diagnosis;
  const proposal = diagnosis.ai_proposal;
  const proposalAction = proposal?.action as Action | undefined;
  const proposalTemplate = proposal?.action_template as Action | undefined;
  const fallbackRange = diagnosis.suggested_ranges?.[0];
  const fallbackVisualTemplate: Action | undefined = !proposalAction?.type && !proposalTemplate?.type &&
    diagnosis.recommended_action === 'VISUAL_REPLACE' && proposal?.requires_input?.includes('visual_material') && fallbackRange
    ? {type:'VISUAL_REPLACE', target_range:{start_ms:fallbackRange.start_ms,end_ms:fallbackRange.end_ms},
      fit_mode:'COVER', transition:{type:'CUT',duration_ms:0}} : undefined;
  const proposed: Action | undefined = proposalAction?.type ? proposalAction : proposalTemplate?.type ? proposalTemplate : fallbackVisualTemplate;
  const planAction = (task.repair_plan as {action?: Action})?.action;
  const displayAction = planAction || proposed;
  const displayType = String(displayAction?.type || diagnosis.recommended_action || '');
  const facts = diagnosis.issues?.find(row => row.issue_id === diagnosis.primary_issue_id) || diagnosis.issues?.[0];
  const unsupportedAction = !diagnosis.recommended_action && ['AV_SEGMENT_REPLACE','UNSUPPORTED'].includes(facts?.suggested_action || '');
  const unsupportedAv = unsupportedAction && !!facts?.affected_modalities.includes('visual') &&
    facts.affected_modalities.some(value=>value==='audio'||value==='caption');
  const summary = unsupportedAv
    ? 'AI 判断该问题同时涉及画面与声音或字幕；当前三种返修动作无法完整修复，并非素材不足。'
    : proposal?.summary || `${actionLabels[displayType] || '修复'}，生成预览后由人工确认。`;
  const active = ['ANALYZING', 'PREVIEW_QUEUED', 'PREVIEW_RENDERING'].includes(task.status);
  const latest = task.previews?.[0];
  const needsVisualMaterial = !!proposal?.requires_input?.includes('visual_material');
  const productMaterials = materials.filter(row => row.asset_kind === 'material' && row.duration_seconds > 0);
  const ecommerceAnalyzedMaterials = productMaterials.filter(row => row.ecommerce_has_analyzed_segments);
  const suggestedMaterial=proposal?.recommended_material;
  const selectedMaterial = materials.find(row => row.id === Number(materialId || suggestedMaterial?.source_asset_id || proposed?.visual?.ref_id || proposed?.material?.ref_id));
  const requiredVisualSeconds = proposed?.type === 'VISUAL_REPLACE'
    ? (Number(proposed?.target_range?.end_ms) - Number(proposed?.target_range?.start_ms)) / 1000
    : Number(proposed?.duration_ms || 0) / 1000;
  const trimSeconds = Number(trimStart);
  const validTrim = !needsVisualMaterial || (!!selectedMaterial && Number.isFinite(trimSeconds) && trimSeconds >= 0 &&
    requiredVisualSeconds > 0 && trimSeconds + requiredVisualSeconds <= selectedMaterial.duration_seconds + 0.05);
  const needsInsertPosition = !!proposal?.requires_input?.includes('insert_position');
  const selectedCandidate=diagnosis.material_candidates?.find(row=>row.source_asset_id===selectedMaterial?.id&&
    Number(row.source_range?.start)<=trimSeconds&&Number(row.source_range?.end)+0.05>=trimSeconds+requiredVisualSeconds);
  const canUseOriginalAudio = proposed?.type === 'CONTENT_INSERT' && !!selectedMaterial?.has_audio && selectedCandidate?.audio_mode==='keep_original';
  const needsAudio = !proposed?.audio && (proposed?.type === 'AUDIO_REPLACE' || proposed?.type === 'CONTENT_INSERT');
  const needsUploadedAudio = needsAudio && !canUseOriginalAudio;
  const insertSeconds = Number(insertAt);
  const hasInsertPosition = !needsInsertPosition || (insertAt.trim() !== '' && Number.isFinite(insertSeconds) && insertSeconds >= 0 && insertSeconds <= task.source_asset.duration_seconds);
  const canPreview = !!proposed?.type && (proposed.type !== 'CONTENT_INSERT' || !!proposed.visual || needsVisualMaterial) && validTrim &&
    (!needsUploadedAudio || !!attachment) && hasInsertPosition && !active &&
    task.status !== 'COMPLETED' && task.status !== 'CANCELED';

  useEffect(() => {
    setMaterialsLoading(true);
    videoRepairApi.materials(task.id).then(setMaterials).catch(reason => setError(message(reason))).finally(()=>setMaterialsLoading(false));
  }, [task.id, task.status]);
  useEffect(()=>{
    if(suggestedMaterial?.source_asset_id){
      setMaterialId(String(suggestedMaterial.source_asset_id));
      setTrimStart(String(suggestedMaterial.source_range?.start||0));
    }
  },[task.id,task.status,suggestedMaterial?.source_asset_id,suggestedMaterial?.source_range?.start]);
  useEffect(()=>{
    if(materialPreview.current&&selectedMaterial&&Number.isFinite(trimSeconds))materialPreview.current.currentTime=trimSeconds;
  },[selectedMaterial?.id,trimSeconds]);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => videoRepairApi.get(task.id).then(setTask).catch(() => {}), 2000);
    return () => window.clearInterval(timer);
  }, [active, task.id]);

  async function upload(file: File) {
    setBusy('audio'); setError('');
    try { setAttachment(await videoRepairApi.uploadAttachment(task.id, file)); }
    catch (reason) { setError(message(reason)); }
    finally { setBusy(''); }
  }
  async function uploadMaterial(file: File) {
    setBusy('material'); setError('');
    try {
      const uploaded=await videoRepairApi.uploadMaterial(task.id,file);
      setMaterials(await videoRepairApi.materials(task.id));
      setMaterialId(String(uploaded.id));setTrimStart('0');
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(''); }
  }
  async function makeTts() {
    if (!tts.trim()) return;
    setBusy('tts'); setError('');
    try { setAttachment(await videoRepairApi.createTts(task.id, tts.trim())); }
    catch (reason) { setError(message(reason)); }
    finally { setBusy(''); }
  }
  function acceptedAction(): Action | null {
    if (!proposed?.type || !canPreview) return null;
    const action: Action = {...proposed};
    if (needsVisualMaterial && selectedMaterial) {
      const visual = {
        ref_type:'VIDEO_ASSET', ref_id:selectedMaterial.id,
        source_range:{start_ms:Math.round(trimSeconds*1000),end_ms:Math.round((trimSeconds+requiredVisualSeconds)*1000)},
        content_hash:selectedMaterial.content_hash,
      };
      if (action.type === 'VISUAL_REPLACE') action.material = visual;
      else if (action.type === 'CONTENT_INSERT') action.visual = visual;
    }
    if (needsInsertPosition) action.insert_at_ms = Math.round(insertSeconds * 1000);
    if (needsAudio) {
      if (attachment) {
        const duration = action.type === 'CONTENT_INSERT' ? action.duration_ms : action.target_range.end_ms - action.target_range.start_ms;
        action.audio = {
          source_type: attachment.source === 'TTS' ? 'TTS' : 'ATTACHMENT',
          attachment_id: attachment.id,
          source_range: {start_ms: 0, end_ms: duration},
        };
      } else if (canUseOriginalAudio && action.visual) {
        action.audio = {
          source_type: 'VIDEO_ASSET', ref_type: 'VIDEO_ASSET', ref_id: action.visual.ref_id,
          source_range: action.visual.source_range, content_hash: action.visual.content_hash,
        };
      }
    }
    if (action.type === 'AUDIO_REPLACE') {
      Object.assign(action, {fit_policy: 'PAD_SILENCE', fade_in_ms: 80, fade_out_ms: 80, subtitle_policy: 'KEEP_UNCHANGED_WITH_WARNING'});
    }
    return action;
  }
  async function saveAndPreview() {
    const action = acceptedAction();
    if (!action) return;
    setBusy('preview'); setError('');
    try {
      await videoRepairApi.updateV4Draft(task.id, task.draft_revision || 1, action);
      await videoRepairApi.createPreview(task.id);
      setTask(await videoRepairApi.get(task.id));
      setDecision('');
    } catch (reason) { setError(message(reason)); }
    finally { setBusy(''); }
  }
  async function reanalyze() {
    setBusy('reanalyze'); setError('');
    try { setTask(await videoRepairApi.retryAnalysis(task.id)); setDecision(''); setAttachment(null); setInsertAt(''); }
    catch (reason) { setError(message(reason)); }
    finally { setBusy(''); }
  }
  async function retryPreview() {
    if (!latest) return;
    setBusy('retry'); setError('');
    try { await videoRepairApi.retryPreview(task.id, latest.id); setTask(await videoRepairApi.get(task.id)); }
    catch (reason) { setError(message(reason)); }
    finally { setBusy(''); }
  }
  async function accept() {
    if (!latest) return;
    setBusy('accept'); setError('');
    try { setTask(await videoRepairApi.acceptPreview(task.id, latest.id, task.draft_revision || 1)); }
    catch (reason) { setError(message(reason)); }
    finally { setBusy(''); }
  }
  const audioInput = () => <div className="mt-4 rounded bg-slate-50 p-3">
    {canUseOriginalAudio && !attachment && <p className="mb-3 text-xs text-emerald-700">默认沿用所选素材的原声；原声不合适时可上传音频或生成 TTS。</p>}
    <div className="flex flex-wrap items-end gap-3">
      <label className="btn-secondary cursor-pointer"><Upload size={14}/>{busy === 'audio' ? '上传中' : '上传音频替换原声'}<input className="hidden" type="file" accept="audio/*" onChange={e => e.target.files?.[0] && upload(e.target.files[0])}/></label>
      <span className="text-xs text-[var(--muted)]">或</span>
      <label className="field min-w-64 flex-1"><span>TTS 文案</span><input className="input" value={tts} disabled={!task.tts_available} onChange={e => setTts(e.target.value)} placeholder={task.tts_available ? '输入确认后的口播文案' : '语音生成服务尚未配置'}/></label>
      <button className="btn-secondary" disabled={!task.tts_available || !tts.trim() || !!busy} onClick={makeTts}>{busy === 'tts' && <Loader2 size={14} className="animate-spin"/>}生成语音</button>
    </div>
    {!task.tts_available && !canUseOriginalAudio && <p className="mt-2 text-xs text-amber-700">当前未配置豆包 TTS，请使用“上传音频”。</p>}
    {attachment && <p className="mt-2 text-xs text-emerald-700">已就绪：{attachment.filename || attachment.source} #{attachment.id}</p>}
  </div>;

  return <section className="space-y-4" aria-label="AI 辅助视频返修工作台">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="font-display text-2xl">AI 辅助视频返修</h2><p className="mt-1 text-xs text-[var(--muted)]">AI 提出方案，人工确认后生成预览；原始 V1 永不覆盖。</p></div><span className="rounded-full bg-violet-100 px-3 py-1 text-xs text-violet-900">{statusLabels[task.status] || task.status}</span></header>
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="rounded border bg-white p-4"><h3 className="text-sm font-semibold">原始 V1</h3><video aria-label="原始 V1 视频" className="mt-3 aspect-video w-full bg-black object-contain" controls src={mediaUrl(task.source_asset.object_url)}/><p className="mt-2 text-xs text-[var(--muted)]">{task.source_asset.filename} · {task.source_asset.duration_seconds.toFixed(1)} 秒</p></section>
      <section className="rounded border border-violet-200 bg-violet-50 p-4"><h3 className="text-sm font-semibold text-violet-950">审核事实与 AI 提案</h3><p className="mt-2 text-sm">{task.input.raw_reason}</p><dl className="mt-4 grid grid-cols-[6rem_1fr] gap-2 text-xs"><dt>建议动作</dt><dd>{unsupportedAv ? '音画整段替换（暂不支持）' : unsupportedAction ? '当前动作暂不支持' : actionLabels[diagnosis.recommended_action || ''] || '需要人工判断'}</dd><dt>置信度</dt><dd>{diagnosis.confidence_level || Math.round((diagnosis.overall_confidence || 0) * 100) + '%'}</dd><dt>证据充分性</dt><dd>{diagnosis.evidence_sufficiency || '—'}</dd><dt>理由</dt><dd>{facts?.description || diagnosis.summary || '—'}</dd></dl><div className="mt-4 rounded border border-violet-200 bg-white p-3"><p className="text-xs font-semibold">AI 方案摘要</p><p className="mt-1 text-sm">{summary}</p>{proposal?.placement && <div className="mt-2 rounded bg-slate-50 p-2 text-xs"><p><strong>插入点判断：</strong>{(proposal.placement.recommended_ms / 1000).toFixed(1)} 秒 · {proposal.placement.confidence_level} {Math.round(proposal.placement.confidence * 100)}%</p><p className="mt-1 text-[var(--muted)]">{proposal.placement.basis}</p>{proposal.placement.alternatives.length > 0 && <p className="mt-1">备选：{proposal.placement.alternatives.map(value => (value / 1000).toFixed(1) + ' 秒').join(' / ')}</p>}</div>}{displayAction?.target_range && <p className="mt-1 text-xs text-[var(--muted)]">目标区间：{(displayAction.target_range.start_ms / 1000).toFixed(1)}–{(displayAction.target_range.end_ms / 1000).toFixed(1)} 秒</p>}{proposal?.risks?.map(row => <p key={row} className="mt-1 text-xs text-amber-700">注意：{row}</p>)}</div></section>
    </div>
    {needsVisualMaterial && !materialsLoading && productMaterials.length > 0 && <p className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">已找到 {productMaterials.length} 条有时长的可选视频；电商商品素材库中 {ecommerceAnalyzedMaterials.length} 条具有已分析片段。当前没有符合本次替换条件的 AI 候选，可在下方人工预览并选择。</p>}
    <section className="rounded border bg-white p-4"><h3 className="text-sm font-semibold">1. 人工审核 AI 方案</h3><label className="field mt-3 max-w-xl"><span>审核决定</span><select aria-label="人工审核决定" className="input-select" value={decision} onChange={e => setDecision(e.target.value as Decision)}><option value="">请选择</option><option value="ACCEPT_AI">接受 AI 方案</option><option value="REANALYZE">要求 AI 重新分析</option></select></label>
      {decision === 'ACCEPT_AI' && <div className="mt-4 rounded bg-emerald-50 p-4"><p className="text-sm">将按上方 AI 提案保存方案并生成真实视频预览。</p>
        {needsVisualMaterial && <div className="mt-3 rounded border border-emerald-200 bg-white p-3 space-y-3">
          <p className="text-sm font-semibold">选择或上传替换画面</p>
          {!materialsLoading && productMaterials.length > 0 && <p className="text-xs text-amber-800">电商商品素材库中 {ecommerceAnalyzedMaterials.length} 条具有已分析片段；这里仍可人工预览并选择有时长的视频。</p>}
          <label className="btn-secondary inline-flex cursor-pointer"><Upload size={14}/>{busy==='material'?'上传中':'手动上传替换视频'}<input className="hidden" type="file" accept=".mp4,.mov,.mkv,.webm,.m4v,video/*" disabled={!!busy} onChange={e=>e.target.files?.[0]&&uploadMaterial(e.target.files[0])}/></label>
          {!!diagnosis.material_candidates?.length&&<div className="space-y-2"><p className="text-xs font-medium">AI 候选片段（请预览确认）</p>{diagnosis.material_candidates.slice(0,3).map((candidate,index)=><button type="button" key={String(candidate.id||index)} className="block w-full rounded border border-[var(--border)] p-2 text-left text-xs hover:bg-emerald-50" onClick={()=>{setMaterialId(String(candidate.source_asset_id));setTrimStart(String(candidate.source_range?.start||0))}}><b>{candidate.description}</b><span className="ml-2 text-[var(--muted)]">{Number(candidate.source_range?.start||0).toFixed(1)}–{Number(candidate.source_range?.end||0).toFixed(1)} 秒</span><p className="mt-1">{candidate.reasons?.join('；')||'请核对是否匹配审核问题'}</p>{candidate.warnings?.map((warning:string)=><p key={warning} className="text-amber-700">{warning}</p>)}</button>)}</div>}
          <label className="field"><span>视频素材</span><select aria-label="选择返修素材" className="input-select" value={materialId} onChange={e=>{setMaterialId(e.target.value);setTrimStart('0')}}><option value="">请选择素材</option>{productMaterials.map(row=><option key={row.id} value={row.id}>{row.filename} · {row.duration_seconds.toFixed(1)} 秒</option>)}</select></label>
          {selectedMaterial&&<><video ref={materialPreview} controls preload="metadata" src={mediaUrl(selectedMaterial.object_url)} className="w-full max-w-lg aspect-video bg-black"/><label className="field max-w-xs"><span>从素材第几秒开始截取</span><input aria-label="素材截取起点" className="input" type="number" min={0} max={Math.max(0,selectedMaterial.duration_seconds-requiredVisualSeconds)} step="0.1" value={trimStart} onChange={e=>setTrimStart(e.target.value)}/></label><p className="text-xs text-[var(--muted)]">将截取 {requiredVisualSeconds.toFixed(1)} 秒；请人工核对画面和声音。只有分析确认口播完整时才会提供沿用原声选项。</p></>}
          {materialsLoading&&<p className="text-sm text-sky-800">正在读取素材视频时长，首次使用可能需要从云端下载。</p>}
          {!materialsLoading&&!materials.some(row=>row.asset_kind==='material')&&<p className="text-sm text-amber-800">还没有可用的替换视频，请在这里手动上传。</p>}
          {selectedMaterial&&!validTrim&&<p className="text-sm text-rose-700">所选视频时长不足或截取起点超出范围。</p>}
        </div>}
        {proposed?.visual && <div className="mt-3 rounded border border-emerald-200 bg-white p-3"><p className="text-xs font-semibold text-emerald-800">AI 已从素材库找到画面</p><p className="mt-1 text-sm">{selectedMaterial?.description || selectedMaterial?.summary || selectedMaterial?.filename || '推荐画面正在载入'}</p><p className="mt-1 text-xs text-[var(--muted)]">画面由 AI 选择；{needsInsertPosition ? '审核意见没有时间点，请确认插入位置。' : canUseOriginalAudio ? '默认沿用素材原声，也可在下方替换。' : '如需口播，请在下方补充音频。'}</p></div>}
        {needsInsertPosition && <label className="field mt-3 max-w-xs"><span>插入位置（秒）</span><input aria-label="插入位置" className="input" type="number" min={0} max={task.source_asset.duration_seconds} step="0.1" value={insertAt} onChange={e => setInsertAt(e.target.value)}/></label>}
        {needsAudio && audioInput()}
        {!proposed?.type || (proposed.type === 'CONTENT_INSERT' && !proposed.visual && !needsVisualMaterial) ? <p className="mt-3 text-sm text-amber-800">{unsupportedAction ? '当前工作台无法执行该修改，重新分析通常不会解决能力限制。' : 'AI 尚未确定可执行动作，请要求重新分析。'}</p> : <button className="primary mt-3" disabled={!canPreview || !!busy} onClick={saveAndPreview}>{busy === 'preview' && <Loader2 size={14} className="animate-spin"/>}{needsVisualMaterial ? '确认素材并生成预览' : needsAudio ? (canUseOriginalAudio && !attachment ? '沿用素材原声并生成预览' : '补充音频并生成预览') : '接受 AI 方案并生成预览'}</button>}
      </div>}
      {decision === 'REANALYZE' && <div className="mt-4 rounded bg-sky-50 p-4"><p className="text-sm">AI 会重新读取审核事实和现有素材，并刷新建议。</p><button className="btn-secondary mt-3" disabled={!!busy || !['FAILED', 'AWAITING_MANUAL_DECISION', 'CONFIGURING'].includes(task.status)} onClick={reanalyze}>{busy === 'reanalyze' && <Loader2 size={14} className="animate-spin"/>}重新分析</button></div>}
    </section>
    <section className="rounded border bg-white p-4"><h3 className="text-sm font-semibold">2. 视频预览与人工确认</h3><div className="mt-3 rounded bg-slate-50 p-3"><p className="text-sm font-medium">本次预览会发生什么</p><p className="mt-1 text-sm">{summary}</p>{displayType && <p className="mt-1 text-xs text-[var(--muted)]">动作：{actionLabels[displayType] || displayType}</p>}</div>{latest ? <div className="mt-4"><p className="text-xs">预览 #{latest.id} · {latest.status} · 草稿修订 {latest.draft_revision}</p>{latest.object_url && <div className="mt-3 grid gap-4 lg:grid-cols-2"><div><p className="mb-2 text-xs font-semibold">原始 V1</p><video className="aspect-video w-full bg-black object-contain" controls src={mediaUrl(task.source_asset.object_url)}/></div><div><p className="mb-2 text-xs font-semibold">修复预览</p><video aria-label="修复后预览" className="aspect-video w-full bg-black object-contain" controls src={mediaUrl(latest.object_url)}/></div></div>}{latest.error_message && <p className="mt-2 text-sm text-rose-700">{latest.error_message}</p>}<div className="mt-3 flex flex-wrap gap-2">{latest.status === 'FAILED' && <button className="btn-secondary" disabled={!!busy} onClick={retryPreview}>重试预览</button>}{latest.status === 'READY' && <button className="primary" disabled={!!busy} onClick={accept}>{busy === 'accept' ? <Loader2 size={14} className="animate-spin"/> : <CheckCircle2 size={14}/>}确认预览并生成 V2</button>}</div></div> : <p className="mt-3 text-xs text-[var(--muted)]">审核 AI 方案并生成后，这里会展示原片与真实渲染结果。</p>}</section>
    {active && <p className="rounded bg-sky-50 p-3 text-sm text-sky-800"><Loader2 className="mr-2 inline animate-spin" size={15}/>后台任务进行中，页面会自动刷新。</p>}{task.error_message && <p className="rounded bg-rose-50 p-3 text-sm text-rose-800">{task.error_message}</p>}{error && <p role="alert" className="rounded bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}
    {task.output_asset && <section className="rounded border border-emerald-300 bg-white p-4"><h3 className="text-sm font-semibold text-emerald-900">新成片 V2</h3><video className="mt-3 aspect-video w-full max-w-3xl bg-black object-contain" controls src={mediaUrl(task.output_asset.object_url)}/>{task.ecommerce_output_asset_id?<Link className="primary mt-3" to={`/ecommerce-final-videos/${task.ecommerce_output_asset_id}`}>在电商成品库查看 V2</Link>:<Link className="primary mt-3" to={`/final-videos/${task.output_asset.id}`}>查看 V2 详情</Link>}</section>}
  </section>;
}
