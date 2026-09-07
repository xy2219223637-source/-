import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  FileSearch,
  FileVideo2,
  Loader2,
  Pencil,
  Play,
  RefreshCw,
  Save,
  Sparkles,
  TableProperties,
  Video,
  X,
} from 'lucide-react';
import { directorApi, mediaUrl } from '../api/client';
import type { DirectorProject, DirectorShot } from '../types';

const stageLabel: Record<string, string> = {
  waiting_extension: '等待视频采集插件', opening_source: '正在后台打开原视频',
  locating_video: '正在识别视频资源', downloading: '正在下载源视频', uploading: '正在上传源视频',
  queued: '等待解析',
  probing_media: '正在读取视频媒体信息', detecting_shots: '正在检测镜头切换',
  extracting_shots: '正在生成子分镜', extracting_keyframes: '正在提取关键帧和子分镜',
  transcribing: '正在识别源视频台词', analyzing_shots: '多模态模型正在分析画面',
  summarizing: '正在汇总脚本结构', saving_clips: '正在保存原视频切片', completed: '解析完成', failed: '解析失败',
};

const roleLabel: Record<string, string> = {
  hook: '内容钩子', pain_point: '痛点引入', product_intro: '产品引入', feature: '卖点讲解',
  demo: '使用演示', proof: '效果证据', benefit: '用户收益', trust: '信任建立', cta: '行动引导', transition: '内容过渡',
};

interface AnalysisDraft {
  product_name: string;
  selling_points: string[];
  target_audience: string[];
  target_pain_points: string;
}

function timecode(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const decimals = Math.floor((Math.max(0, ms) % 1000) / 100);
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}.${decimals}`;
}

function durationText(seconds: number) {
  const total = Math.max(0, Math.round(seconds || 0));
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

function toTags(value?: string | string[]) {
  let values: string[];
  if (Array.isArray(value)) {
    values = value;
  } else {
    const text = (value || '').trim();
    if (text.startsWith('[') && text.endsWith(']')) {
      try {
        const parsed = JSON.parse(text.replaceAll("'", '"'));
        values = Array.isArray(parsed) ? parsed.map(String) : [text];
      } catch {
        values = text.split(/[，,\n]/);
      }
    } else {
      values = text.split(/[，,\n]/);
    }
  }
  return [...new Set(values.map((item) => item.trim()).filter(Boolean))];
}

function toText(value?: string | string[]) {
  return Array.isArray(value) ? value.filter(Boolean).join('，') : value || '';
}

function toParagraph(value?: string | string[]) {
  if (Array.isArray(value)) return [...new Set(value.map((item) => item.trim()).filter(Boolean))].join('；');
  const text = (value || '').trim();
  if (!text) return '';
  if (text.startsWith('[') && text.endsWith(']')) return toTags(text).join('；');
  return text;
}

function draftFromProject(project: DirectorProject): AnalysisDraft {
  return {
    product_name: project.analysis.product_name || '',
    selling_points: toTags(project.analysis.selling_points),
    target_audience: toTags(project.analysis.target_audience),
    target_pain_points: toText(project.analysis.target_pain_points),
  };
}

function TagEditor({ values, onChange, placeholder }: { values: string[]; onChange: (values: string[]) => void; placeholder: string }) {
  const [input, setInput] = useState('');

  function addValue() {
    const additions = toTags(input);
    if (additions.length) onChange([...new Set([...values, ...additions])]);
    setInput('');
  }

  return (
    <div className="min-h-10 overflow-x-auto rounded-md border border-[var(--border)] bg-white px-2 py-1.5 focus-within:border-black">
      <div className="flex w-max min-w-full flex-nowrap items-center gap-1.5">
        {values.map((value) => (
          <span key={value} className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded bg-[#eef2ff] px-2 py-1 text-xs text-[#4338ca]">
            <span>{value}</span>
            <button type="button" className="shrink-0" onClick={() => onChange(values.filter((item) => item !== value))} aria-label={`移除${value}`}><X size={12} /></button>
          </span>
        ))}
        <input
          className="h-7 w-36 shrink-0 bg-transparent text-sm outline-none"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onBlur={addValue}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ',' || event.key === '，') {
              event.preventDefault();
              addValue();
            }
          }}
          placeholder={values.length ? '' : placeholder}
        />
      </div>
    </div>
  );
}

function TagList({ values }: { values: string[] }) {
  if (!values.length) return <span className="text-sm text-[var(--muted)]">待确认</span>;
  return (
    <div className="w-full min-w-0 overflow-x-auto pb-1">
      <div className="flex w-max min-w-full flex-nowrap gap-1.5">
        {values.map((value) => <span key={value} className="shrink-0 whitespace-nowrap rounded bg-[#eef2ff] px-2.5 py-1 text-xs text-[#4338ca]">{value}</span>)}
      </div>
    </div>
  );
}

export default function DirectorAnalysisPage() {
  const { id } = useParams();
  const projectId = Number(id);
  const navigate = useNavigate();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [project, setProject] = useState<DirectorProject | null>(null);
  const [analysisDraft, setAnalysisDraft] = useState<AnalysisDraft | null>(null);
  const [editingAnalysis, setEditingAnalysis] = useState(false);
  const [savingAnalysis, setSavingAnalysis] = useState(false);
  const [showDetailedAnalysis, setShowDetailedAnalysis] = useState(false);
  const [showDetailedShots, setShowDetailedShots] = useState(false);
  const [showRewrite, setShowRewrite] = useState(false);
  const [productService, setProductService] = useState('');
  const [sellingPoints, setSellingPoints] = useState('');
  const [language, setLanguage] = useState('中文');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [activeShot, setActiveShot] = useState<number | null>(null);
  const [copiedShot, setCopiedShot] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function load() {
    const row = await directorApi.get(projectId);
    setProject(row);
    setAnalysisDraft(draftFromProject(row));
    if (row.sections.length) {
      setExpanded((current) => current.size ? current : new Set(row.sections.map((section) => section.id)));
      setSelected((current) => current.size ? current : new Set(row.sections.map((section) => section.id)));
    }
    return row;
  }

  useEffect(() => { load().catch(() => setError('解析项目加载失败')); }, [projectId]);
  useEffect(() => {
    if (!project || !['capturing', 'queued', 'analyzing'].includes(project.status)) return;
    const timer = window.setInterval(() => load().catch(() => undefined), 1800);
    return () => window.clearInterval(timer);
  }, [project?.status, projectId]);
  useEffect(() => {
    const hasModal = showDetailedAnalysis || showDetailedShots || showRewrite;
    if (!hasModal) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (showDetailedShots) setShowDetailedShots(false);
      else if (showDetailedAnalysis) setShowDetailedAnalysis(false);
      else setShowRewrite(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [showDetailedAnalysis, showDetailedShots, showRewrite]);

  const allShots = useMemo(() => project?.sections.flatMap((section) => section.shots || []) || [], [project]);
  const isRunning = !!project && ['capturing', 'queued', 'analyzing'].includes(project.status);

  function playShot(shot: DirectorShot) {
    const player = videoRef.current;
    if (!player) return;
    player.currentTime = shot.start_ms / 1000;
    setActiveShot(shot.id);
    void player.play();
  }

  function trackPlayback() {
    const ms = (videoRef.current?.currentTime || 0) * 1000;
    const current = allShots.find((shot) => ms >= shot.start_ms && ms < shot.end_ms);
    setActiveShot(current?.id || null);
  }

  async function copyTranscript(shot: DirectorShot) {
    await navigator.clipboard.writeText(shot.transcript || '');
    setCopiedShot(shot.id);
    window.setTimeout(() => setCopiedShot(null), 1200);
  }

  async function saveAnalysis() {
    if (!analysisDraft) return;
    setSavingAnalysis(true);
    setError('');
    try {
      const row = await directorApi.updateAnalysis(projectId, analysisDraft);
      setProject(row);
      setAnalysisDraft(draftFromProject(row));
      setEditingAnalysis(false);
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || '解析摘要保存失败');
    } finally {
      setSavingAnalysis(false);
    }
  }

  function cancelAnalysis() {
    if (project) setAnalysisDraft(draftFromProject(project));
    setEditingAnalysis(false);
  }

  async function rewrite() {
    if (!productService.trim()) {
      setError('请填写产品或服务');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await directorApi.rewrite(projectId, { product_service: productService, selling_points: sellingPoints, language });
      navigate(`/director-inspiration/${projectId}/workspace`);
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || '脚本仿写失败');
    } finally {
      setBusy(false);
    }
  }

  async function reanalyze() {
    setError('');
    try {
      const row = await directorApi.reanalyze(projectId);
      setProject(row);
      setEditingAnalysis(false);
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || '重新解析失败');
    }
  }

  if (!project || !analysisDraft) return <div className="h-72 grid place-items-center text-sm text-[var(--muted)]">{error || <span className="inline-flex items-center gap-2"><Loader2 className="animate-spin" size={18} />正在加载解析项目</span>}</div>;
  const media = project.analysis.media || {};
  const videoTags = toTags(project.analysis.tags);
  const detailedAudience = toParagraph(project.analysis.target_audience_detail || project.analysis.target_audience);
  const detailedSellingPoints = toParagraph(project.analysis.selling_points_detail || project.analysis.selling_points);

  return (
    <div className="space-y-5 animate-fade-in">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="min-w-0">
          <Link to="/director-inspiration" className="inline-flex items-center gap-1 text-xs text-[var(--muted)]"><ArrowLeft size={14} />返回编导灵感</Link>
          <h1 className="text-xl font-semibold mt-2 truncate">{project.name}</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <button className="btn-secondary" onClick={reanalyze} disabled={isRunning}><RefreshCw size={15} />重新解析</button>
          <button className="btn-secondary" onClick={() => navigate(`/director-inspiration/${projectId}/workspace`)} disabled={isRunning || !project.sections.length}><Video size={15} />进入工作台</button>
          <button className="h-10 px-4 rounded-md bg-[#4f46e5] text-white text-sm font-medium inline-flex items-center gap-2 disabled:opacity-50" onClick={() => setShowRewrite(true)} disabled={isRunning || project.status === 'failed'}><Sparkles size={15} />仿写脚本</button>
        </div>
      </header>

      {error && <div className="border border-red-200 bg-red-50 rounded-md px-4 py-3 text-sm text-red-700">{error}</div>}
      {isRunning && <div className="border border-[#c7d2fe] bg-[#eef2ff] rounded-md px-5 py-4 flex items-center gap-3 text-sm"><Loader2 size={18} className="animate-spin text-[#4f46e5]" /><div><div className="font-medium">{stageLabel[project.progress_stage] || '正在解析参考视频'}</div><div className="text-xs text-[var(--muted)] mt-1">{project.status === 'capturing' ? '视频采集完成后将自动开始拆解' : '正在生成视频解析结果'}</div></div></div>}
      {project.status === 'failed' && <div className="border border-red-200 bg-red-50 rounded-md p-4 text-sm text-red-700">{project.error_message || '视频解析失败, 请检查文件后重试'}</div>}

      <section className="border border-[var(--border)] bg-white rounded-lg p-4 grid lg:grid-cols-[220px_minmax(0,1fr)] gap-5">
        <div className="aspect-[9/16] w-full max-w-[220px] max-h-80 lg:max-h-none bg-black rounded-md grid place-items-center overflow-hidden mx-auto lg:mx-0">
          {project.source_url ? <video ref={videoRef} className="w-full h-full object-contain" src={mediaUrl(project.source_url)} controls onTimeUpdate={trackPlayback} /> : <FileVideo2 className="text-white/60" />}
        </div>
        <div className="min-w-0">
          <div className="flex flex-col items-start gap-3 border-b border-[var(--border)] pb-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0"><h2 className="text-base font-semibold">解析结果</h2><p className="text-xs text-[var(--muted)] mt-1">确认产品和受众信息后再进行脚本仿写</p></div>
            {!editingAnalysis && <div className="flex shrink-0 items-center gap-2">
              <button className="btn-secondary" onClick={() => setShowDetailedAnalysis(true)} disabled={isRunning}><FileSearch size={14} />详细解析</button>
              <button className="btn-secondary" onClick={() => setEditingAnalysis(true)} disabled={isRunning}><Pencil size={14} />编辑</button>
            </div>}
          </div>

          {editingAnalysis ? (
            <div className="mt-4 space-y-4">
              <label className="field"><span>产品名称</span><input value={analysisDraft.product_name} onChange={(event) => setAnalysisDraft({ ...analysisDraft, product_name: event.target.value })} placeholder="请输入产品名称" /></label>
              <div className="field"><span>受众人群</span><TagEditor values={analysisDraft.target_audience} onChange={(values) => setAnalysisDraft({ ...analysisDraft, target_audience: values })} placeholder="输入后按 Enter 添加" /></div>
              <div className="field"><span>产品买点</span><TagEditor values={analysisDraft.selling_points} onChange={(values) => setAnalysisDraft({ ...analysisDraft, selling_points: values })} placeholder="输入后按 Enter 添加" /></div>
              <label className="field"><span>受众痛点</span><textarea rows={3} value={analysisDraft.target_pain_points} onChange={(event) => setAnalysisDraft({ ...analysisDraft, target_pain_points: event.target.value })} placeholder="概括受众最需要解决的问题" /></label>
              <div className="flex justify-end gap-2 pt-1">
                <button className="btn-secondary" onClick={cancelAnalysis} disabled={savingAnalysis}>取消</button>
                <button className="h-10 px-4 rounded-md bg-black text-white text-sm font-medium inline-flex items-center gap-2 disabled:opacity-50" onClick={saveAnalysis} disabled={savingAnalysis}>{savingAnalysis ? <Loader2 size={15} className="animate-spin" /> : <Save size={15} />}保存</button>
              </div>
            </div>
          ) : (
            <div className="mt-2 divide-y divide-[var(--border)]">
              <div className="grid min-w-0 gap-2 py-3 sm:grid-cols-[96px_minmax(0,1fr)] sm:items-start"><div className="text-xs text-[var(--muted)] sm:pt-0.5">产品名称</div><div className="min-w-0 text-sm font-medium break-words">{analysisDraft.product_name || '待确认'}</div></div>
              <div className="grid min-w-0 gap-2 py-3 sm:grid-cols-[96px_minmax(0,1fr)] sm:items-start"><div className="text-xs text-[var(--muted)] sm:pt-1">受众人群</div><div className="min-w-0"><TagList values={analysisDraft.target_audience} /></div></div>
              <div className="grid min-w-0 gap-2 py-3 sm:grid-cols-[96px_minmax(0,1fr)] sm:items-start"><div className="text-xs text-[var(--muted)] sm:pt-1">产品买点</div><div className="min-w-0"><TagList values={analysisDraft.selling_points} /></div></div>
              <div className="grid min-w-0 gap-2 py-3 sm:grid-cols-[96px_minmax(0,1fr)] sm:items-start"><div className="text-xs text-[var(--muted)] sm:pt-0.5">受众痛点</div><p className="min-w-0 text-sm leading-6 whitespace-pre-wrap break-words">{analysisDraft.target_pain_points || '待确认'}</p></div>
            </div>
          )}
        </div>
      </section>

      {!!project.sections.length && (
        <section className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><h2 className="text-base font-semibold">脚本结构拆解</h2><p className="text-xs text-[var(--muted)] mt-1">{selected.size} 个结构组已选择</p></div>
            <div className="flex items-center gap-2">
              <button className="btn-secondary" onClick={() => setShowDetailedShots(true)}><TableProperties size={14} />详细分镜</button>
              <button className="btn-secondary" onClick={() => { const section = project.sections.find((item) => selected.has(item.id)); const shot = section?.shots?.[0]; if (shot) playShot(shot); }} disabled={!selected.size}><Play size={14} />播放选中</button>
            </div>
          </div>

          {project.sections.map((section, sectionIndex) => {
            const open = expanded.has(section.id);
            return (
              <article key={section.id} className="border border-[var(--border)] bg-white rounded-lg overflow-hidden">
                <div className="min-h-14 flex items-center gap-3 px-4 bg-[#fafafa] border-b border-[var(--border)]">
                  <input type="checkbox" checked={selected.has(section.id)} onChange={() => setSelected((current) => { const next = new Set(current); next.has(section.id) ? next.delete(section.id) : next.add(section.id); return next; })} aria-label={`选择${section.title}`} />
                  <button className="flex-1 min-w-0 flex items-center gap-3 text-left" onClick={() => setExpanded((current) => { const next = new Set(current); next.has(section.id) ? next.delete(section.id) : next.add(section.id); return next; })}>
                    {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    <span className="w-7 h-7 rounded bg-[#eef2ff] text-[#4338ca] grid place-items-center text-xs font-semibold shrink-0">{sectionIndex + 1}</span>
                    <span className="font-medium text-sm truncate">{section.title}</span>
                    <span className="hidden sm:inline text-xs text-[var(--muted)] shrink-0">{timecode(section.start_ms)} - {timecode(section.end_ms)}</span>
                    <span className="hidden sm:block ml-auto text-xs text-[var(--muted)] shrink-0">{section.shots?.length || 0} 个子分镜</span>
                  </button>
                </div>

                {open && <div>
                  <div className="hidden lg:grid grid-cols-[120px_minmax(0,1.45fr)_140px_minmax(0,1fr)] text-[11px] text-[var(--muted)] border-b border-[var(--border)]">
                    <div className="px-3 py-2">脚本结构</div><div className="px-3 py-2 border-l border-[var(--border)]">台词</div><div className="px-3 py-2 border-l border-[var(--border)]">画面</div><div className="px-3 py-2 border-l border-[var(--border)]">画面描述</div>
                  </div>
                  {(section.shots || []).map((shot) => (
                    <div key={shot.id} className={`grid grid-cols-1 lg:grid-cols-[120px_minmax(0,1.45fr)_140px_minmax(0,1fr)] border-b last:border-b-0 border-[var(--border)] ${activeShot === shot.id ? 'bg-[#f5f3ff]' : ''}`}>
                      <div className="p-3 min-w-0">
                        <div className="lg:hidden text-[11px] text-[var(--muted)] mb-2">脚本结构</div>
                        <button className="w-full text-left text-xs text-[#4f46e5]" onClick={() => playShot(shot)}><span className="block font-medium">{timecode(shot.start_ms)} - {timecode(shot.end_ms)}</span><span className="mt-2 inline-flex items-center gap-1"><Play size={13} />播放</span></button>
                        <div className="mt-2 text-[10px] text-[var(--muted)]">{roleLabel[shot.story_role] || shot.story_role || '待确认'}</div>
                      </div>
                      <div className="p-3 min-w-0 border-t lg:border-t-0 lg:border-l border-[var(--border)] text-sm leading-6">
                        <div className="lg:hidden text-[11px] text-[var(--muted)] mb-2">台词</div>
                        <div className="whitespace-pre-wrap break-words">{shot.transcript || <span className="text-[var(--muted)]">暂无识别台词</span>}</div>
                        <button className="mt-3 inline-flex items-center gap-1 text-xs text-[#4f46e5]" onClick={() => copyTranscript(shot)}>{copiedShot === shot.id ? <Check size={13} /> : <Copy size={13} />}{copiedShot === shot.id ? '已复制' : '复制台词'}</button>
                      </div>
                      <div className="p-3 min-w-0 border-t lg:border-t-0 lg:border-l border-[var(--border)]">
                        <div className="lg:hidden text-[11px] text-[var(--muted)] mb-2">画面</div>
                        <div className="relative w-20 h-28 rounded bg-black overflow-hidden mx-auto">{shot.clip_url ? <video src={mediaUrl(shot.clip_url)} poster={mediaUrl(shot.thumbnail_url)} controls className="w-full h-full object-contain" /> : shot.thumbnail_url ? <img src={mediaUrl(shot.thumbnail_url)} className="w-full h-full object-cover" /> : <FileVideo2 className="absolute inset-0 m-auto text-white/45" size={20} />}</div>
                        {shot.clip_url && <a href={mediaUrl(shot.clip_url)} download className="mt-2 text-[11px] text-[#4f46e5] inline-flex items-center gap-1 justify-center w-full"><Download size={12} />保存切片</a>}
                      </div>
                      <div className="p-3 min-w-0 border-t lg:border-t-0 lg:border-l border-[var(--border)] text-xs leading-5">
                        <div className="lg:hidden text-[11px] text-[var(--muted)] mb-2">画面描述</div>
                        <div className="whitespace-pre-wrap break-words">{shot.description || '待确认'}</div>
                        <div className="mt-2 text-[var(--muted)] break-words">{[shot.scene, shot.shot_size, shot.camera].filter(Boolean).join(' · ')}</div>
                      </div>
                    </div>
                  ))}
                  {!section.shots?.length && <div className="p-8 text-center text-xs text-[var(--muted)]">当前项目暂无子分镜数据, 重新解析后生成</div>}
                </div>}
              </article>
            );
          })}
        </section>
      )}

      {showDetailedAnalysis && createPortal(<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-2 sm:p-5" onMouseDown={() => setShowDetailedAnalysis(false)}>
        <section role="dialog" aria-modal="true" aria-label="详细解析" className="flex max-h-[94vh] w-full max-w-[1500px] flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-white shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
          <header className="flex shrink-0 items-center justify-between gap-4 border-b border-[var(--border)] px-4 py-3 sm:px-6 sm:py-4">
            <div className="min-w-0"><h2 className="font-semibold">详细解析</h2><p className="mt-1 truncate text-xs text-[var(--muted)]">{project.name}</p></div>
            <button className="grid h-9 w-9 shrink-0 place-items-center rounded-md hover:bg-[#f3f3f1]" onClick={() => setShowDetailedAnalysis(false)} aria-label="关闭详细解析"><X size={18} /></button>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">
            <div className="space-y-6">
              <TagList values={videoTags} />
              <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2 lg:grid-cols-5">
                <div className="min-w-0"><div className="text-xs text-[var(--muted)]">产品名称</div><div className="mt-2 text-sm font-medium break-words">{analysisDraft.product_name || '待确认'}</div></div>
                <div><div className="text-xs text-[var(--muted)]">视频时长</div><div className="mt-2 text-sm font-medium">{durationText(project.source_duration_seconds)}</div></div>
                <div><div className="text-xs text-[var(--muted)]">拆解结果</div><div className="mt-2 text-sm font-medium">{project.analysis.section_count ?? project.sections.length} 组 · {project.analysis.shot_count ?? allShots.length} 镜</div></div>
                <div><div className="text-xs text-[var(--muted)]">画面规格</div><div className="mt-2 text-sm font-medium">{media.width && media.height ? `${media.width} x ${media.height}` : '待确认'}</div></div>
                <div><div className="text-xs text-[var(--muted)]">帧率和音轨</div><div className="mt-2 text-sm font-medium">{media.fps ? `${media.fps} FPS` : '待确认'} · {media.has_audio == null ? '待确认' : media.has_audio ? '有音轨' : '无音轨'}</div></div>
              </div>

              <div className="grid gap-6 border-t border-[var(--border)] pt-6 lg:grid-cols-3">
                <div className="min-w-0"><h3 className="text-sm font-medium">受众人群</h3><p className="mt-3 text-sm leading-6 whitespace-pre-wrap break-words">{detailedAudience || '待确认'}</p></div>
                <div className="min-w-0"><h3 className="text-sm font-medium">产品买点</h3><p className="mt-3 text-sm leading-6 whitespace-pre-wrap break-words">{detailedSellingPoints || '待确认'}</p></div>
                <div className="min-w-0"><h3 className="text-sm font-medium">受众痛点</h3><p className="mt-3 text-sm leading-6 whitespace-pre-wrap break-words">{analysisDraft.target_pain_points || '待确认'}</p></div>
              </div>

              <div className="grid gap-3 lg:grid-cols-3">
                <div className="rounded-md border border-[var(--border)] p-4"><div className="text-xs text-[var(--muted)]">开场钩子</div><p className="mt-2 text-sm leading-6 break-words">{project.analysis.hook?.content || '待确认'}</p></div>
                <div className="rounded-md border border-[var(--border)] p-4"><div className="text-xs text-[var(--muted)]">镜头节奏</div><p className="mt-2 text-sm leading-6 break-words">{project.analysis.rhythm?.summary || '待确认'}</p>{project.analysis.rhythm?.average_shot_seconds != null && <div className="mt-2 text-xs text-[var(--muted)]">平均镜头 {project.analysis.rhythm.average_shot_seconds}s</div>}</div>
                <div className="rounded-md border border-[var(--border)] p-4"><div className="text-xs text-[var(--muted)]">行动引导</div><p className="mt-2 text-sm leading-6 break-words">{project.analysis.cta?.content || '待确认'}</p>{project.analysis.cta?.type && <div className="mt-2 text-xs text-[var(--muted)]">{project.analysis.cta.type}</div>}</div>
              </div>

              <div><h3 className="text-sm font-medium">卖点链路</h3><div className="mt-3 space-y-2">
                {(project.analysis.selling_point_chain || []).map((item, index) => <div key={`${item.stage || 'stage'}-${index}`} className="border border-[var(--border)] px-4 py-3 text-sm leading-6"><span className="mr-2 font-semibold">{index + 1}. {roleLabel[item.stage || ''] || item.stage || '内容展开'}</span>{item.content || '待确认'}</div>)}
                {!project.analysis.selling_point_chain?.length && <div className="border border-dashed border-[var(--border)] px-4 py-8 text-center text-sm text-[var(--muted)]">暂无卖点链路</div>}
              </div></div>
            </div>
          </div>
        </section>
      </div>, document.body)}

      {showDetailedShots && createPortal(<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-2 sm:p-5" onMouseDown={() => setShowDetailedShots(false)}>
        <section role="dialog" aria-modal="true" aria-label="详细分镜" className="flex h-[94vh] w-full max-w-[1680px] flex-col overflow-hidden rounded-lg border border-[var(--border)] bg-white shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
          <header className="flex shrink-0 items-center justify-between gap-4 border-b border-[var(--border)] px-4 py-3 sm:px-6 sm:py-4">
            <div className="min-w-0"><h2 className="font-semibold">详细分镜</h2><p className="mt-1 truncate text-xs text-[var(--muted)]">{project.sections.length} 个结构组 · {allShots.length} 个子分镜</p></div>
            <button className="grid h-9 w-9 shrink-0 place-items-center rounded-md hover:bg-[#f3f3f1]" onClick={() => setShowDetailedShots(false)} aria-label="关闭详细分镜"><X size={18} /></button>
          </header>
          <div className="min-h-0 flex-1 overflow-auto bg-[#f7f7f5]">
            <table className="w-full min-w-[2920px] table-fixed border-collapse bg-white text-left text-sm">
              <colgroup><col className="w-[150px]" /><col className="w-[330px]" /><col className="w-[210px]" /><col className="w-[180px]" /><col className="w-[250px]" /><col className="w-[300px]" /><col className="w-[230px]" /><col className="w-[270px]" /><col className="w-[250px]" /><col className="w-[260px]" /><col className="w-[290px]" /></colgroup>
              <thead className="sticky top-0 z-20 bg-[#fafafa] text-xs text-[var(--muted)] shadow-[0_1px_0_var(--border)]">
                <tr><th className="sticky left-0 z-30 bg-[#fafafa] px-4 py-3 font-medium">时间</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">台词</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">原视频切片</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">持续时间和视频结构</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">人物或主体及置信度</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">镜头描述</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">场景</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">景别, 摄影角度和运镜</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">动作</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">商品和道具</th><th className="border-l border-[var(--border)] px-4 py-3 font-medium">导演意图和情绪</th></tr>
              </thead>
              <tbody>
                {project.sections.map((section, sectionIndex) => <Fragment key={section.id}>
                  <tr><td colSpan={11} className="border-y border-[var(--border)] bg-[#f5f5f2] px-4 py-3"><div className="flex items-center gap-3"><span className="grid h-7 w-7 place-items-center rounded bg-[#eef2ff] text-xs font-semibold text-[#4338ca]">{sectionIndex + 1}</span><span className="font-medium">{section.title}</span><span className="text-xs text-[var(--muted)]">{timecode(section.start_ms)} - {timecode(section.end_ms)}</span><span className="ml-auto text-xs text-[var(--muted)]">{section.shots?.length || 0} 个子分镜</span></div></td></tr>
                  {(section.shots || []).map((shot) => <tr key={shot.id} className={activeShot === shot.id ? 'bg-[#f5f3ff]' : 'bg-white'}>
                    <td className={`sticky left-0 z-10 border-b border-[var(--border)] px-4 py-5 align-top ${activeShot === shot.id ? 'bg-[#f5f3ff]' : 'bg-white'}`}><button className="text-left text-[#4f46e5]" onClick={() => playShot(shot)}><span className="block font-medium">{timecode(shot.start_ms)}</span><span className="mt-1 block text-xs text-[var(--muted)]">{timecode(shot.end_ms)}</span><span className="mt-3 inline-flex items-center gap-1 text-xs"><Play size={13} />播放</span></button></td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top"><p className="leading-6 whitespace-pre-wrap break-words">{shot.transcript || <span className="text-[var(--muted)]">暂无识别台词</span>}</p><button className="mt-4 inline-flex items-center gap-1 text-xs text-[#4f46e5]" onClick={() => copyTranscript(shot)}>{copiedShot === shot.id ? <Check size={13} /> : <Copy size={13} />}{copiedShot === shot.id ? '已复制' : '复制台词'}</button></td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top"><div className="mx-auto h-36 w-24 overflow-hidden rounded-md bg-black">{shot.clip_url ? <video src={mediaUrl(shot.clip_url)} poster={mediaUrl(shot.thumbnail_url)} controls preload="metadata" className="h-full w-full object-contain" /> : shot.thumbnail_url ? <img src={mediaUrl(shot.thumbnail_url)} className="h-full w-full object-cover" /> : <div className="grid h-full place-items-center"><FileVideo2 className="text-white/45" size={20} /></div>}</div>{shot.clip_url && <a href={mediaUrl(shot.clip_url)} download className="mt-3 inline-flex w-full items-center justify-center gap-1 text-xs text-[#4f46e5]"><Download size={12} />保存切片</a>}</td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top"><div className="font-medium">{((shot.end_ms - shot.start_ms) / 1000).toFixed(1)}s</div><div className="mt-3 text-xs leading-5 text-[var(--muted)]">{roleLabel[shot.story_role] || shot.story_role || '待确认'}</div></td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top"><p className="leading-6 break-words">{shot.subject || '待确认'}</p><div className="mt-3 text-xs text-[var(--muted)]">置信度 {shot.confidence > 0 ? `${Math.round(shot.confidence * 100)}%` : '待确认'}</div></td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top leading-6 whitespace-pre-wrap break-words">{shot.description || '待确认'}</td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top leading-6 whitespace-pre-wrap break-words">{shot.scene || '待确认'}</td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top"><div className="text-xs text-[var(--muted)]">景别</div><p className="mt-1 leading-6 break-words">{shot.shot_size || '待确认'}</p><div className="mt-4 text-xs text-[var(--muted)]">摄影角度和运镜</div><p className="mt-1 leading-6 whitespace-pre-wrap break-words">{shot.camera || '待确认'}</p></td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top leading-6 break-words">{shot.action || '待确认'}</td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top"><div className="text-xs text-[var(--muted)]">商品</div><p className="mt-1 leading-6 whitespace-pre-wrap break-words">{shot.product || '待确认'}</p><div className="mt-4 text-xs text-[var(--muted)]">道具</div><p className="mt-1 leading-6 whitespace-pre-wrap break-words">{shot.props || shot.analysis?.props || '待确认'}</p></td>
                    <td className="border-b border-l border-[var(--border)] px-4 py-5 align-top"><div className="text-xs text-[var(--muted)]">导演意图</div><p className="mt-1 leading-6 whitespace-pre-wrap break-words">{shot.director_intent || '待确认'}</p><div className="mt-4 text-xs text-[var(--muted)]">情绪</div><p className="mt-1 leading-6 whitespace-pre-wrap break-words">{shot.emotion || shot.analysis?.emotion || '待确认'}</p></td>
                  </tr>)}
                </Fragment>)}
              </tbody>
            </table>
          </div>
        </section>
      </div>, document.body)}

      {showRewrite && createPortal(<div className="fixed inset-0 z-50 bg-black/45 grid place-items-center p-4" onMouseDown={() => setShowRewrite(false)}><div className="bg-white border border-[var(--border)] rounded-lg w-full max-w-lg shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
        <div className="px-5 py-4 border-b border-[var(--border)] flex justify-between items-center"><div><h2 className="font-medium">脚本仿写</h2><p className="text-xs text-[var(--muted)] mt-1">沿用参考结构, 生成新的原创表达</p></div><button className="w-8 h-8 grid place-items-center" onClick={() => setShowRewrite(false)} aria-label="关闭"><X size={18} /></button></div>
        <div className="p-5 space-y-4">
          <label className="field"><span>产品或服务</span><input value={productService} onChange={(event) => setProductService(event.target.value)} placeholder="请输入产品或服务" /></label>
          <label className="field"><span>卖点关键词</span><textarea rows={4} value={sellingPoints} onChange={(event) => setSellingPoints(event.target.value)} placeholder="每个卖点可换行填写" /></label>
          <label className="field"><span>输出语言</span><select className="input-select" value={language} onChange={(event) => setLanguage(event.target.value)}><option>中文</option><option>英文</option></select></label>
        </div>
        <div className="px-5 py-4 border-t border-[var(--border)] flex justify-end gap-2"><button className="btn-secondary" onClick={() => setShowRewrite(false)}>取消</button><button className="h-10 px-4 rounded-md bg-[#4f46e5] text-white text-sm font-medium inline-flex items-center gap-2" onClick={rewrite} disabled={busy}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}立即生成</button></div>
      </div></div>, document.body)}
    </div>
  );
}
