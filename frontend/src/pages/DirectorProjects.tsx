import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  ArrowUpRight,
  CheckCircle2,
  ChevronRight,
  Clapperboard,
  Clock3,
  FileVideo2,
  Layers3,
  Loader2,
  MoreHorizontal,
  Puzzle,
  Search,
  Send,
  Sparkles,
  Trash2,
  Upload,
  Users,
  WandSparkles,
  X,
} from 'lucide-react';
import { directorApi, mediaUrl } from '../api/client';
import { CaptureExtensionError, createVideoCapture, extractSharedVideoUrl, isDirectVideoUrl } from '../lib/videoCaptureExtension';
import type { DirectorProject, ShootingTask } from '../types';
import FeishuPushDialog from '../components/FeishuPushDialog';
import { notificationSummary } from '../lib/notificationStatus';

const statusLabel: Record<string, string> = {
  draft: '草稿', capturing: '采集中', queued: '排队中', analyzing: '解析中', analyzed: '已解析',
  generating: '生成中', ready: '可编辑', mixing: '混剪中', preview_ready: '预览可用', failed: '失败',
};

function durationLabel(seconds: number) {
  const value = Math.max(0, Math.round(seconds || 0));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}

function projectThumb(project: DirectorProject) {
  const shot = project.sections.flatMap((section) => section.shots || [])[0];
  const asset = project.sections.flatMap((section) => section.assets)[0];
  return shot?.thumbnail_url || asset?.thumbnail_url || (asset?.media_kind === 'image' ? asset.url : '');
}

export default function DirectorProjectsPage() {
  const navigate = useNavigate();
  const fileInput = useRef<HTMLInputElement>(null);
  const autoCaptureStarted = useRef(false);
  const [mode, setMode] = useState<'reference' | 'original'>('reference');
  const [projects, setProjects] = useState<DirectorProject[]>([]);
  const [pushProject, setPushProject] = useState<DirectorProject | null>(null);
  const [pushResult, setPushResult] = useState<ShootingTask | null>(null);
  const [historyMode, setHistoryMode] = useState<'mine' | 'shared' | 'analysis'>('mine');
  const [query, setQuery] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const initialCaptureUrl = new URLSearchParams(window.location.search).get('capture_url') || '';
  const [url, setUrl] = useState(initialCaptureUrl);
  const [brief, setBrief] = useState('');
  const [language, setLanguage] = useState('中文');
  const [advanced, setAdvanced] = useState(false);
  const [menuId, setMenuId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [extensionMissing, setExtensionMissing] = useState(false);

  useEffect(() => {
    directorApi.list()
      .then((rows) => setProjects(Array.isArray(rows) ? rows : []))
      .catch(() => {
        setProjects([]);
        setError('编导项目加载失败, 请刷新页面重试');
      });
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get('capture') !== '1' || !initialCaptureUrl || autoCaptureStarted.current) return;
    autoCaptureStarted.current = true;
    window.history.replaceState({}, '', window.location.pathname);
    void createReference();
  }, []);

  function useFile(selected?: File) {
    if (!selected) return;
    if (!selected.type.startsWith('video/')) {
      setError('请选择 MP4, MOV, M4V 或 WEBM 视频');
      return;
    }
    setFile(selected);
    setUrl('');
    setError('');
    setExtensionMissing(false);
  }

  async function createReference() {
    if (!file && !url.trim()) {
      setError('请粘贴视频链接, 也可以上传本地视频');
      return;
    }
    setBusy(true);
    setError('');
    try {
      if (file) {
        const project = await directorApi.upload(file);
        navigate(`/director-inspiration/${project.id}`);
        return;
      }
      const sourcePageUrl = extractSharedVideoUrl(url);
      if (!/^https?:\/\//i.test(sourcePageUrl)) throw new Error('请输入有效的视频链接');
      if (isDirectVideoUrl(sourcePageUrl)) {
        const project = await directorApi.fromUrl(sourcePageUrl);
        navigate(`/director-inspiration/${project.id}`);
        return;
      }
      setExtensionMissing(false);
      const project = await createVideoCapture(sourcePageUrl);
      navigate(`/director-inspiration/${project.id}`);
    } catch (requestError: any) {
      if (requestError instanceof CaptureExtensionError) setExtensionMissing(true);
      setError(requestError?.response?.data?.detail || requestError?.message || '参考视频提交失败');
    } finally {
      setBusy(false);
    }
  }

  async function createOriginal() {
    if (brief.trim().length < 2) {
      setError('请填写脚本创作需求');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const project = await directorApi.original({ brief: brief.trim(), language });
      navigate(`/director-inspiration/${project.id}/workspace`);
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || '脚本生成失败');
    } finally {
      setBusy(false);
    }
  }

  async function deleteProject(project: DirectorProject) {
    if (!confirm(`确定删除脚本“${project.name}”及其关联拍摄任务吗? 此操作不可撤销.`)) return;
    setMenuId(null);
    setDeletingId(project.id);
    setError('');
    setSuccess('');
    try {
      await directorApi.remove(project.id);
      setProjects((rows) => rows.filter((row) => row.id !== project.id));
      setSuccess(`脚本“${project.name}”已删除`);
    } catch (requestError: any) {
      setError(requestError?.response?.data?.detail || '脚本删除失败, 请重试');
    } finally {
      setDeletingId(null);
    }
  }

  const filtered = useMemo(() => projects.filter((project) => {
    if (historyMode === 'shared') return false;
    if (historyMode === 'analysis' && project.mode !== 'reference') return false;
    return !query.trim() || project.name.toLowerCase().includes(query.trim().toLowerCase());
  }), [projects, historyMode, query]);

  return (
    <div className="space-y-10 animate-fade-in">
      <header className="mx-auto max-w-5xl pt-2">
        <div className="text-xs tracking-[.18em] text-[var(--muted)] mb-2">DIRECTOR INSPIRATION</div>
        <h1 className="font-display text-4xl">编导灵感</h1>
      </header>

      <section className="panel mx-auto max-w-5xl overflow-hidden">
        <div className="flex items-center gap-6 px-5 border-b border-[var(--border)]">
          <button className={`py-4 text-sm border-b-2 ${mode === 'reference' ? 'border-black text-black font-medium' : 'border-transparent text-[var(--muted)] hover:text-black'}`} onClick={() => setMode('reference')}><span className="inline-flex items-center gap-2"><Sparkles size={15} />复刻爆款脚本</span></button>
          <button className={`py-4 text-sm border-b-2 ${mode === 'original' ? 'border-black text-black font-medium' : 'border-transparent text-[var(--muted)] hover:text-black'}`} onClick={() => setMode('original')}><span className="inline-flex items-center gap-2"><WandSparkles size={15} />AI 生成脚本</span></button>
        </div>

        {mode === 'reference' ? (
          <div className="p-5 sm:p-6">
            <div className="flex flex-col lg:flex-row lg:items-center gap-3 lg:gap-2 text-sm">
              <span className="text-[var(--muted)] shrink-0">输入</span>
              <label htmlFor="director-video-url" className="font-medium shrink-0">视频链接:</label>
              <input id="director-video-url" className="h-10 min-w-0 lg:w-52 xl:w-64 border-b border-[var(--border)] bg-transparent px-2 outline-none focus:border-black" value={url} onChange={(event) => { setUrl(event.target.value); if (event.target.value) setFile(null); setExtensionMissing(false); }} placeholder="粘贴抖音, 小红书或快手视频链接" />
              <span className="text-[var(--muted)] shrink-0">你也可以</span>
              <button className="btn-secondary h-10 shrink-0 opacity-45 cursor-not-allowed" disabled title="即将开放"><Search size={15} />全网爆款搜索</button>
              <span className="text-[var(--muted)] shrink-0">或从本地</span>
              <button className="btn-secondary h-10 max-w-full shrink-0" onClick={() => fileInput.current?.click()} title={file?.name || '上传参考视频'}><Upload size={15} /><span className="max-w-40 truncate">{file ? file.name : '上传参考视频'}</span></button>
              <input ref={fileInput} className="hidden" type="file" accept="video/mp4,video/quicktime,video/x-m4v,video/webm" onChange={(event) => { useFile(event.target.files?.[0]); event.currentTarget.value = ''; }} />
              {file && <button className="w-8 h-8 grid place-items-center text-[var(--muted)] hover:text-black shrink-0" onClick={() => setFile(null)} title="移除视频"><X size={16} /></button>}
            </div>
            <div className="mt-6 pt-4 border-t border-[var(--border)] flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0 text-xs text-[var(--muted)]">
                {file ? <span className="inline-flex items-center gap-2 max-w-full"><FileVideo2 size={14} className="shrink-0" /><span className="truncate">{file.name}</span><span className="shrink-0">{(file.size / 1024 / 1024).toFixed(1)} MB</span></span> : <span>本次消耗 1 次解析额度</span>}
              </div>
              <button className="btn-primary h-10" onClick={createReference} disabled={busy}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}立即解析</button>
            </div>
          </div>
        ) : (
          <div className="p-5 sm:p-6">
            <div className="border border-[var(--border)] bg-white p-4">
              <textarea className="w-full min-h-28 resize-none bg-transparent text-sm leading-6 outline-none" value={brief} onChange={(event) => setBrief(event.target.value)} placeholder="描述产品或服务, 目标人群, 核心卖点和视频风格" />
              <div className="flex flex-wrap items-center gap-3 border-t border-[var(--border)] pt-3">
                <label className="inline-flex items-center gap-2 text-xs text-[var(--muted)] opacity-55 cursor-not-allowed" title="即将开放"><input type="checkbox" disabled />深度创作</label>
                <button className={`text-xs px-2 py-1 border ${advanced ? 'border-black bg-black text-white' : 'border-transparent text-[var(--muted)] hover:text-black'}`} onClick={() => setAdvanced((value) => !value)}>高级输入</button>
                {advanced && <select className="ml-1 border border-[var(--border)] bg-white rounded px-2 py-1 text-xs" value={language} onChange={(event) => setLanguage(event.target.value)}><option>中文</option><option>英文</option></select>}
                <button className="btn-primary ml-auto h-10" onClick={createOriginal} disabled={busy}>{busy ? <Loader2 size={15} className="animate-spin" /> : <WandSparkles size={15} />}立即生成</button>
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {['新品口播种草', '痛点解决方案', '使用教程分享'].map((example) => <button key={example} className="text-xs text-[var(--muted)] border border-[var(--border)] px-3 py-1.5 hover:text-black hover:border-black" onClick={() => setBrief(example)}>{example}</button>)}
            </div>
          </div>
        )}
      </section>

      {error && <div className="mx-auto max-w-4xl border border-red-200 bg-red-50 rounded-md px-4 py-3 text-sm text-red-700">{error}</div>}
      {success && <div className="mx-auto border border-emerald-200 bg-emerald-50 rounded-md px-4 py-3 text-sm text-emerald-800"><span className="inline-flex items-center gap-2"><CheckCircle2 size={16} />{success}</span></div>}
      {pushResult && <div role="status" className={`mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3 rounded-md border px-4 py-3 text-sm ${pushResult.notification_status === 'sent' ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-900'}`}><span>{notificationSummary(pushResult)}</span><button className="font-medium underline underline-offset-4" onClick={() => navigate('/shooting-tasks')}>查看拍摄任务</button></div>}
      {pushProject && <FeishuPushDialog key={pushProject.id} project={pushProject} onClose={() => setPushProject(null)} onResult={setPushResult} />}
      {extensionMissing && <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"><span>平台视频需要视频采集插件.</span><a className="inline-flex items-center gap-2 font-medium text-amber-950 underline underline-offset-4" href="/downloads/enrui-video-capture-extension.zip" download><Puzzle size={15} />下载插件</a></div>}

      <section>
        <div className="flex flex-wrap items-center gap-5 border-b border-[var(--border)]">
          <button className={`py-3 text-sm border-b-2 ${historyMode === 'mine' ? 'border-black text-black font-medium' : 'border-transparent text-[var(--muted)]'}`} onClick={() => setHistoryMode('mine')}>我创建的</button>
          <button className="py-3 text-sm border-b-2 border-transparent text-[var(--muted)] opacity-45 cursor-not-allowed" disabled title="即将开放">共享给我的</button>
          <button className={`py-3 text-sm border-b-2 ${historyMode === 'analysis' ? 'border-black text-black font-medium' : 'border-transparent text-[var(--muted)]'}`} onClick={() => setHistoryMode('analysis')}>视频解析</button>
          <div className="ml-auto relative w-56 mb-2"><Search size={14} className="absolute left-3 top-2.5 text-[var(--muted)]" /><input className="w-full border border-[var(--border)] rounded-md bg-white py-2 pl-9 pr-3 text-xs outline-none" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目" /></div>
        </div>

        <div className="py-4 flex items-center gap-6 text-xs text-[var(--muted)]">
          <span className="inline-flex items-center gap-1.5"><Layers3 size={14} />{filtered.length} 个项目</span>
          <span className="inline-flex items-center gap-1.5"><Clapperboard size={14} />{filtered.reduce((total, item) => total + item.sections.length, 0)} 个脚本结构</span>
          <span className="inline-flex items-center gap-1.5"><Users size={14} />当前账号</span>
        </div>

        <div className="border-y border-[var(--border)] divide-y divide-[var(--border)] bg-white">
          {filtered.map((project) => {
            const thumbnail = projectThumb(project);
            const isRunning = ['capturing', 'queued', 'analyzing', 'generating', 'mixing'].includes(project.status);
            return (
              <div key={project.id} className="relative grid grid-cols-[64px_minmax(0,1fr)_32px] sm:flex sm:items-center gap-3 sm:gap-4 px-4 py-4 hover:bg-[#fafafa] cursor-pointer" onClick={() => navigate(project.mode === 'reference' ? `/director-inspiration/${project.id}` : `/director-inspiration/${project.id}/workspace`)}>
                <div className="relative w-16 h-20 rounded-md bg-[#111] overflow-hidden shrink-0 grid place-items-center text-white/60">{thumbnail ? <img src={mediaUrl(thumbnail)} className="w-full h-full object-cover" /> : <FileVideo2 size={21} />} {project.mode === 'reference' && <span className="absolute bottom-1 right-1 bg-black/70 text-white text-[9px] px-1 py-0.5 rounded">{durationLabel(project.source_duration_seconds)}</span>}</div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium truncate max-w-full">{project.name}</h3><span className="text-[10px] bg-[var(--accent-soft)] text-[var(--text)] px-2 py-0.5 rounded-full">{project.mode === 'reference' ? '参考视频仿写' : 'AI 原创脚本'}</span><span className={`sm:hidden text-[10px] px-2 py-0.5 rounded-full ${project.status === 'failed' ? 'badge-err' : isRunning ? 'badge-run' : 'badge-ok'}`}>{statusLabel[project.status] || project.status}</span></div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--muted)]"><span>{project.sections.length} 个脚本结构</span><span>{project.sections.reduce((sum, section) => sum + section.assets.filter((asset) => asset.role === 'material').length, 0)} 个素材</span><span className="inline-flex items-center gap-1"><Clock3 size={12} />{new Date(project.updated_at).toLocaleString()}</span></div>
                  <div className="mt-2 flex gap-1.5">{project.sections.slice(0, 4).map((section) => <span key={section.id} className="text-[10px] border border-[var(--border)] rounded px-1.5 py-0.5 text-[var(--muted)]">{section.title}</span>)}</div>
                </div>
                <span className={`hidden sm:inline text-xs px-2.5 py-1 rounded-full ${project.status === 'failed' ? 'badge-err' : isRunning ? 'badge-run' : 'badge-ok'}`}>{statusLabel[project.status] || project.status}</span>
                <button className="w-8 h-8 grid place-items-center text-[var(--muted)] hover:text-black" onClick={(event) => { event.stopPropagation(); setMenuId(menuId === project.id ? null : project.id); }} title="更多操作"><MoreHorizontal size={18} /></button>
                <ChevronRight size={17} className="hidden sm:block text-[var(--muted)]" />
                {menuId === project.id && <div className="absolute right-12 top-12 z-10 w-44 border border-[var(--border)] rounded-md bg-white shadow-lg p-1" onClick={(event) => event.stopPropagation()}>
                    <button className="w-full px-3 py-2 text-xs text-left rounded hover:bg-[var(--bg-elevated)] inline-flex items-center gap-2" onClick={() => navigate(`/director-inspiration/${project.id}/workspace`)}><ArrowUpRight size={13} />打开工作台</button>
                    <button className="w-full px-3 py-2 text-xs text-left rounded hover:bg-[var(--bg-elevated)] flex items-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed" disabled={!project.sections.length || isRunning} title={isRunning ? '脚本正在处理中' : project.sections.length ? '推送到飞书' : '脚本暂无可推送内容'} onClick={() => { setMenuId(null); setPushResult(null); setSuccess(''); setError(''); setPushProject(project); }}><Send size={13} /><span className="flex-1">推送飞书</span><ChevronRight size={13} /></button>
                    <div className="my-1 border-t border-[var(--border)]" />
                    <button className="w-full px-3 py-2 text-xs text-left rounded text-red-700 hover:bg-red-50 inline-flex items-center gap-2 disabled:opacity-50" disabled={deletingId === project.id} onClick={() => void deleteProject(project)}>{deletingId === project.id ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}删除</button>
                </div>}
              </div>
            );
          })}
          {!filtered.length && <div className="py-14 text-center text-sm text-[var(--muted)]">暂无项目</div>}
        </div>
      </section>

    </div>
  );
}
