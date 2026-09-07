import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Film,
  GripVertical,
  ImagePlus,
  Loader2,
  PackageSearch,
  Plus,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  VolumeX,
  WandSparkles,
  X,
} from 'lucide-react';
import { directorApi, mediaUrl } from '../api/client';
import type { DirectorAsset, DirectorProject, DirectorSection, DirectorShot } from '../types';

function timecode(ms: number) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function AssetPreview({ asset, onRemove }: { asset: DirectorAsset; onRemove?: () => void }) {
  return (
    <div className="group relative w-24 shrink-0">
      <div className="w-24 h-32 rounded-md bg-black border border-[var(--border)] overflow-hidden">
        {asset.media_kind === 'image'
          ? <img src={mediaUrl(asset.thumbnail_url || asset.url)} className="w-full h-full object-cover" />
          : <video src={mediaUrl(asset.url)} poster={mediaUrl(asset.thumbnail_url)} className="w-full h-full object-cover" muted controls />}
      </div>
      <div className="text-[10px] text-[var(--muted)] mt-1 line-clamp-2">{asset.description || (asset.source === 'generated' ? 'AI 素材草图' : '视频素材')}</div>
      {onRemove && <button type="button" className="absolute right-1 top-1 w-7 h-7 rounded bg-black/75 text-white grid place-items-center opacity-0 group-hover:opacity-100 transition-opacity" onClick={onRemove} title="删除素材"><Trash2 size={13} /></button>}
    </div>
  );
}

function ShotReference({ shot }: { shot: DirectorShot }) {
  return <div className="w-24 shrink-0"><div className="relative w-24 h-32 rounded-md bg-black overflow-hidden">{shot.clip_url ? <video src={mediaUrl(shot.clip_url)} poster={mediaUrl(shot.thumbnail_url)} controls muted className="w-full h-full object-cover" /> : shot.thumbnail_url ? <img src={mediaUrl(shot.thumbnail_url)} className="w-full h-full object-cover" /> : null}<span className="absolute left-1 bottom-1 bg-black/70 text-white rounded px-1 text-[9px]">{timecode(shot.start_ms)}</span></div><div className="text-[10px] text-[var(--muted)] mt-1">{shot.shot_size || '子分镜'} · {((shot.end_ms - shot.start_ms) / 1000).toFixed(1)}s</div></div>;
}

export default function DirectorWorkspacePage() {
  const { id } = useParams();
  const projectId = Number(id);
  const revision = useRef(0);
  const [project, setProject] = useState<DirectorProject | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [subshotMode, setSubshotMode] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'error'>('saved');
  const [draggedId, setDraggedId] = useState<number | null>(null);
  const [compliance, setCompliance] = useState<{ status: 'passed' | 'review'; issue_count: number; issues: Array<{ section_id: number; section_title: string; phrase: string; message: string }> } | null>(null);

  async function load() {
    const row = await directorApi.get(projectId);
    setProject(row);
    setDirty(false);
    return row;
  }

  useEffect(() => { load().catch(() => setError('编导工作台加载失败')); }, [projectId]);
  useEffect(() => {
    if (!project || !['queued', 'mixing'].includes(project.status)) return;
    const timer = window.setInterval(() => load().catch(() => undefined), 1800);
    return () => window.clearInterval(timer);
  }, [project?.status, projectId]);

  useEffect(() => {
    if (!project || !dirty) return;
    const currentRevision = revision.current;
    const snapshot = project;
    const timer = window.setTimeout(async () => {
      setSaveState('saving');
      try {
        await Promise.all([
          directorApi.saveSections(snapshot.id, snapshot.sections),
          directorApi.saveShots(snapshot.id, snapshot.sections.flatMap((section) => section.shots || [])),
        ]);
        if (revision.current === currentRevision) setDirty(false);
        setSaveState('saved');
      } catch {
        setSaveState('error');
      }
    }, 900);
    return () => window.clearTimeout(timer);
  }, [project, dirty]);

  const shotCount = useMemo(() => project?.sections.reduce((total, section) => total + (section.shots?.length || 0), 0) || 0, [project]);

  function updateProject(updater: (value: DirectorProject) => DirectorProject) {
    setProject((current) => current ? updater(current) : current);
    revision.current += 1;
    setDirty(true);
  }

  function changeSection(sectionId: number, patch: Partial<DirectorSection>) {
    updateProject((current) => ({ ...current, sections: current.sections.map((section) => section.id === sectionId ? { ...section, ...patch } : section) }));
  }

  function changeLine(sectionId: number, lineId: string, text: string) {
    const section = project?.sections.find((item) => item.id === sectionId);
    if (!section) return;
    changeSection(sectionId, { script_lines: section.script_lines.map((line) => line.id === lineId ? { ...line, text } : line) });
  }

  function addLine(sectionId: number) {
    const section = project?.sections.find((item) => item.id === sectionId);
    if (!section) return;
    changeSection(sectionId, { script_lines: [...section.script_lines, { id: crypto.randomUUID(), text: '' }] });
  }

  function removeLine(sectionId: number, lineId: string) {
    const section = project?.sections.find((item) => item.id === sectionId);
    if (!section) return;
    changeSection(sectionId, { script_lines: section.script_lines.filter((line) => line.id !== lineId) });
  }

  function changeShot(sectionId: number, shotId: number, patch: Partial<DirectorShot>) {
    const section = project?.sections.find((item) => item.id === sectionId);
    if (!section) return;
    changeSection(sectionId, { shots: section.shots.map((shot) => shot.id === shotId ? { ...shot, ...patch } : shot) });
  }

  function dropSection(targetId: number) {
    if (!project || draggedId == null || draggedId === targetId) return;
    const sections = [...project.sections];
    const from = sections.findIndex((section) => section.id === draggedId);
    const to = sections.findIndex((section) => section.id === targetId);
    const [moved] = sections.splice(from, 1);
    sections.splice(to, 0, moved);
    updateProject((current) => ({ ...current, sections: sections.map((section, sort_order) => ({ ...section, sort_order })) }));
    setDraggedId(null);
  }

  async function rewriteSection(sectionId: number) {
    setBusy(`rewrite-${sectionId}`); setError('');
    try { setProject(await directorApi.rewriteSection(projectId, sectionId)); }
    catch (requestError: any) { setError(requestError?.response?.data?.detail || '段落改写失败'); }
    finally { setBusy(''); }
  }

  async function uploadAsset(sectionId: number, file: File | undefined, role: 'reference' | 'material') {
    if (!file) return;
    setBusy(`upload-${sectionId}-${role}`); setError('');
    try { setProject(await directorApi.uploadAsset(projectId, sectionId, file, role)); }
    catch (requestError: any) { setError(requestError?.response?.data?.detail || '素材上传失败'); }
    finally { setBusy(''); }
  }

  async function removeAsset(assetId: number) {
    setBusy(`delete-${assetId}`); setError('');
    try { await directorApi.removeAsset(projectId, assetId); await load(); }
    catch (requestError: any) { setError(requestError?.response?.data?.detail || '素材删除失败'); }
    finally { setBusy(''); }
  }

  async function checkCompliance() {
    setBusy('compliance'); setError('');
    try { setCompliance(await directorApi.compliance(projectId)); }
    catch (requestError: any) { setError(requestError?.response?.data?.detail || '合规检测失败'); }
    finally { setBusy(''); }
  }

  if (!project) return <div className="h-72 grid place-items-center text-sm text-[var(--muted)]">{error || <span className="inline-flex items-center gap-2"><Loader2 size={18} className="animate-spin" />正在加载编导工作台</span>}</div>;

  return (
    <div className="animate-fade-in space-y-4">
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border)] pb-4">
        <div className="min-w-0">
          <Link to={project.mode === 'reference' ? `/director-inspiration/${project.id}` : '/director-inspiration'} className="inline-flex items-center gap-1 text-xs text-[var(--muted)]"><ArrowLeft size={14} />返回{project.mode === 'reference' ? '视频解析' : '项目列表'}</Link>
          <div className="flex flex-wrap items-center gap-3 mt-2"><h1 className="text-xl font-medium truncate">{project.name}</h1><span className="text-xs text-[var(--muted)]">{project.sections.length} 个结构 · {shotCount} 个子分镜</span></div>
          <div className="mt-1 inline-flex items-center gap-1.5 text-xs text-[var(--muted)]">{saveState === 'saving' ? <Loader2 size={12} className="animate-spin" /> : saveState === 'saved' ? <Check size={12} /> : <X size={12} className="text-red-600" />}{saveState === 'saving' ? '自动保存中' : saveState === 'saved' ? '已自动保存' : '自动保存失败'}</div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button className="btn-secondary" onClick={checkCompliance} disabled={!!busy}>{busy === 'compliance' ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />}合规检测</button>
          <button className="btn-secondary opacity-45 cursor-not-allowed" disabled title="即将开放"><PackageSearch size={15} />素材收集</button>
          <button className="h-10 px-4 rounded-md border border-[var(--border)] bg-[#f5f5f5] text-[var(--muted)] text-sm font-medium inline-flex items-center gap-2 opacity-55 cursor-not-allowed" disabled title="即将开放"><Film size={15} />视频混剪</button>
        </div>
      </header>

      {error && <div className="border border-red-200 bg-red-50 rounded-md px-4 py-3 text-sm text-red-700 flex justify-between gap-3"><span>{error}</span><button onClick={() => setError('')} aria-label="关闭"><X size={16} /></button></div>}
      {project.status === 'failed' && project.error_message && <div className="border border-red-200 bg-red-50 rounded-md px-4 py-3 text-sm text-red-700">{project.error_message}</div>}

      <div className="border border-[var(--border)] rounded-lg bg-white overflow-hidden">
        <div className="hidden lg:grid grid-cols-[minmax(120px,.48fr)_minmax(280px,1.7fr)_minmax(180px,.95fr)_minmax(180px,.95fr)] bg-[#fafafa] border-b border-[var(--border)] text-xs font-medium text-[var(--muted)] sticky top-0 z-10">
          <div className="px-4 py-3">脚本结构</div>
          <div className="px-4 py-3 border-l border-[var(--border)]">台词</div>
          <div className="px-4 py-3 border-l border-[var(--border)]">拍摄参考</div>
          <div className="px-4 py-3 border-l border-[var(--border)] flex items-center justify-between"><span>视频素材</span><label className={`inline-flex items-center gap-2 ${!shotCount ? 'opacity-50' : ''}`}><span>子分镜模式</span><input type="checkbox" checked={subshotMode} disabled={!shotCount} onChange={(event) => setSubshotMode(event.target.checked)} /></label></div>
        </div>

        <div className="divide-y divide-[var(--border)]">
          {project.sections.map((section, index) => {
            const references = section.assets.filter((asset) => asset.role === 'reference');
            const materials = section.assets.filter((asset) => asset.role === 'material');
            return (
              <article key={section.id} draggable onDragStart={() => setDraggedId(section.id)} onDragOver={(event) => event.preventDefault()} onDrop={() => dropSection(section.id)} className={`grid grid-cols-1 lg:grid-cols-[minmax(120px,.48fr)_minmax(280px,1.7fr)_minmax(180px,.95fr)_minmax(180px,.95fr)] ${draggedId === section.id ? 'opacity-50' : ''}`}>
                <div className="p-3 flex flex-col gap-4 bg-[#fcfcfc] lg:bg-white">
                  <div><div className="flex items-center gap-2 text-[11px] text-[var(--muted)] mb-3"><GripVertical size={14} className="cursor-grab" />{String(index + 1).padStart(2, '0')} · {timecode(section.start_ms)}-{timecode(section.end_ms)}</div><input className="w-full text-sm font-medium bg-transparent border-b border-transparent hover:border-[var(--border)] focus:border-black outline-none pb-1" value={section.title} onChange={(event) => changeSection(section.id, { title: event.target.value })} /></div>
                </div>

                <div className="p-3 border-t lg:border-t-0 lg:border-l border-[var(--border)] flex flex-col min-w-0">
                  <div className="lg:hidden text-xs font-medium text-[var(--muted)] mb-3">台词</div>
                  {subshotMode ? <div className="space-y-2">{section.shots.map((shot, shotIndex) => <div key={shot.id} className="grid grid-cols-[56px_minmax(0,1fr)] border border-[var(--border)] rounded-md overflow-hidden"><div className="bg-[#f5f3ff] text-[#4f46e5] p-2 text-[10px] leading-5">{shotIndex + 1}<br />{timecode(shot.start_ms)}</div><textarea rows={2} className="w-full resize-y p-2 text-xs leading-5 outline-none" value={shot.transcript} onChange={(event) => changeShot(section.id, shot.id, { transcript: event.target.value })} placeholder="子分镜台词" /></div>)}</div> : <div className="space-y-2">{section.script_lines.map((line, lineIndex) => <div key={line.id} className="relative group"><textarea rows={2} className="w-full resize-y border border-[var(--border)] rounded-md bg-[#fafafa] px-3 py-2 pr-9 text-sm leading-6 outline-none focus:border-black" value={line.text} onChange={(event) => changeLine(section.id, line.id, event.target.value)} placeholder={`第 ${lineIndex + 1} 段台词`} /><button className="absolute right-2 top-2 w-6 h-6 grid place-items-center text-[var(--muted)] opacity-0 group-hover:opacity-100 focus:opacity-100" onClick={() => removeLine(section.id, line.id)} title="删除分段"><X size={14} /></button></div>)}{!section.script_lines.length && <div className="h-20 border border-dashed border-[var(--border)] rounded-md grid place-items-center text-xs text-[var(--muted)]">暂无台词</div>}</div>}
                  <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
                    <button className="inline-flex items-center gap-1 text-[var(--muted)] hover:text-black" onClick={() => addLine(section.id)}><Plus size={13} />分段</button>
                    <button className="inline-flex items-center gap-1 text-[var(--muted)] opacity-45 cursor-not-allowed" disabled title="音频功能未启用"><VolumeX size={13} />音频</button>
                    <button className="inline-flex items-center gap-1 text-[#4f46e5]" onClick={() => rewriteSection(section.id)} disabled={!!busy}>{busy === `rewrite-${section.id}` ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}AI 改写</button>
                    {!subshotMode && <button className="text-[var(--muted)] hover:text-black" onClick={() => changeSection(section.id, { script_lines: [] })}>清空台词</button>}
                  </div>
                </div>

                <div className="p-3 border-t lg:border-t-0 lg:border-l border-[var(--border)] flex flex-col min-w-0">
                  <div className="lg:hidden text-xs font-medium text-[var(--muted)] mb-3">拍摄参考</div>
                  <div className="flex gap-2 overflow-x-auto pb-2">{subshotMode ? section.shots.map((shot) => <ShotReference key={shot.id} shot={shot} />) : references.map((asset) => <AssetPreview key={asset.id} asset={asset} onRemove={asset.source === 'uploaded' ? () => removeAsset(asset.id) : undefined} />)}{!(subshotMode ? section.shots.length : references.length) && <div className="text-xs text-[var(--muted)] py-10 w-full text-center">暂无拍摄参考</div>}</div>
                  <textarea rows={2} className="mt-2 w-full resize-y border-0 border-t border-[var(--border)] pt-2 text-xs leading-5 outline-none" value={section.shooting_notes} onChange={(event) => changeSection(section.id, { shooting_notes: event.target.value })} placeholder="拍摄建议" />
                  <label className="mt-2 inline-flex items-center justify-center gap-1 border border-[var(--border)] rounded-md py-2 text-xs cursor-pointer hover:bg-black hover:text-white"><Upload size={13} />上传参考视频<input type="file" className="hidden" accept="image/*,video/*" onChange={(event) => { uploadAsset(section.id, event.target.files?.[0], 'reference'); event.currentTarget.value = ''; }} /></label>
                </div>

                <div className="p-3 border-t lg:border-t-0 lg:border-l border-[var(--border)] flex flex-col min-w-0">
                  <div className="lg:hidden text-xs font-medium text-[var(--muted)] mb-3">视频素材</div>
                  <div className="flex gap-2 overflow-x-auto pb-2">{materials.map((asset) => <AssetPreview key={asset.id} asset={asset} onRemove={() => removeAsset(asset.id)} />)}{!materials.length && <div className="text-xs text-[var(--muted)] py-10 w-full text-center">等待添加或生成素材</div>}</div>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    <label className="inline-flex items-center justify-center gap-1 border border-[var(--border)] rounded-md py-2 text-xs cursor-pointer hover:bg-black hover:text-white"><ImagePlus size={13} />视频素材<input type="file" className="hidden" accept="image/*,video/*" onChange={(event) => { uploadAsset(section.id, event.target.files?.[0], 'material'); event.currentTarget.value = ''; }} /></label>
                    <button className="inline-flex items-center justify-center gap-1 border border-[var(--border)] rounded-md bg-[#f5f5f5] py-2 text-xs text-[var(--muted)] opacity-55 cursor-not-allowed" disabled title="即将开放"><WandSparkles size={13} />AI 复刻</button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </div>

      {compliance && <div className="fixed inset-0 z-50 bg-black/45 grid place-items-center p-4" onMouseDown={() => setCompliance(null)}><div className="w-full max-w-lg bg-white rounded-lg border border-[var(--border)] shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
        <div className="px-5 py-4 border-b border-[var(--border)] flex justify-between items-center"><div className="flex items-center gap-2">{compliance.status === 'passed' ? <CheckCircle2 size={19} className="text-emerald-700" /> : <ShieldCheck size={19} className="text-amber-700" />}<h2 className="font-medium">合规检测</h2></div><button className="w-8 h-8 grid place-items-center" onClick={() => setCompliance(null)} aria-label="关闭"><X size={17} /></button></div>
        <div className="p-5">{compliance.status === 'passed' ? <p className="text-sm">未发现预设高风险用语. 正式投放前仍需复核.</p> : <div className="space-y-3">{compliance.issues.map((issue, index) => <div key={`${issue.section_id}-${issue.phrase}-${index}`} className="border-l-2 border-amber-500 pl-3"><div className="text-sm font-medium">{issue.section_title}: {issue.phrase}</div><div className="text-xs text-[var(--muted)] mt-1">{issue.message}</div></div>)}</div>}</div>
      </div></div>}
    </div>
  );
}
