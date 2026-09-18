import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowUpRight,
  CheckCircle2,
  ClipboardList,
  Clock3,
  Loader2,
  RefreshCw,
  Search,
  UserRound,
} from 'lucide-react';
import { directorApi } from '../api/client';
import type { ShootingTask } from '../types';
import { notificationLabels } from '../lib/notificationStatus';

const statusLabel: Record<ShootingTask['status'], string> = {
  pending: '待处理',
  in_progress: '拍摄中',
  completed: '已完成',
};

function localTime(value: string) {
  // The backend's existing timestamp columns store UTC without a timezone suffix.
  return new Date(/(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`).toLocaleString();
}

export default function ShootingTasksPage() {
  const [tasks, setTasks] = useState<ShootingTask[]>([]);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'all' | ShootingTask['status']>('all');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retrying, setRetrying] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function refreshTasks() {
    setRefreshing(true);
    setError('');
    try { setTasks(await directorApi.shootingTasks()); }
    catch { setError('拍摄任务刷新失败，请重试'); }
    finally { setRefreshing(false); }
  }

  async function retry(task: ShootingTask) {
    if (retrying !== null) return;
    setRetrying(task.id);
    setError('');
    try {
      const updated = await directorApi.retryNotification(task.id);
      setTasks((rows) => rows.map((row) => row.id === updated.id ? updated : row));
    } catch (err: any) {
      setError(typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : '暂时无法确认重试结果，请刷新并核对飞书消息');
      try { setTasks(await directorApi.shootingTasks()); } catch { /* Preserve current records. */ }
    } finally { setRetrying(null); }
  }

  useEffect(() => {
    directorApi.shootingTasks()
      .then((rows) => setTasks(Array.isArray(rows) ? rows : []))
      .catch((requestError: any) => setError(requestError?.response?.data?.detail || '拍摄任务加载失败, 请刷新页面重试'))
      .finally(() => setLoading(false));
  }, []);

  const filtered = useMemo(() => tasks.filter((task) => {
    const search = query.trim().toLowerCase();
    if (status !== 'all' && task.status !== status) return false;
    return !search || [task.project_name, task.department_name, task.recipient_name, task.assignee_name].some((value) => value.toLowerCase().includes(search));
  }), [query, status, tasks]);

  return (
    <div className="space-y-8 animate-fade-in">
      <header className="flex flex-wrap items-end justify-between gap-4 pt-2">
        <div>
          <div className="text-xs tracking-[.18em] text-[var(--muted)] mb-2">SHOOTING TASKS</div>
          <h1 className="font-display text-4xl">拍摄任务</h1>
          <p className="mt-3 text-sm text-[var(--muted)]">已推送脚本的拍摄执行清单</p>
        </div>
        <div className="flex items-center gap-5 text-xs text-[var(--muted)]">
          <button className="btn-secondary" disabled={refreshing || retrying !== null} onClick={() => void refreshTasks()}><RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />刷新</button>
          <span className="inline-flex items-center gap-1.5"><ClipboardList size={14} />{tasks.length} 个任务</span>
          <span className="inline-flex items-center gap-1.5"><Clock3 size={14} />{tasks.filter((task) => task.status !== 'completed').length} 个待处理</span>
        </div>
      </header>

      <section>
        <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border)] pb-3">
          {([
            ['all', '全部'],
            ['pending', '待处理'],
            ['in_progress', '拍摄中'],
            ['completed', '已完成'],
          ] as const).map(([value, label]) => (
            <button
              key={value}
              className={`h-8 px-3 text-xs border ${status === value ? 'border-black bg-black text-white' : 'border-[var(--border)] bg-white text-[var(--muted)] hover:text-black'}`}
              onClick={() => setStatus(value)}
            >
              {label}
            </button>
          ))}
          <div className="relative ml-auto w-full sm:w-60">
            <Search size={14} className="absolute left-3 top-2.5 text-[var(--muted)]" />
            <input className="w-full h-9 border border-[var(--border)] rounded-sm bg-white pl-9 pr-3 text-xs outline-none focus:border-black" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索脚本或负责人" />
          </div>
        </div>

        {error && <div className="mt-4 border border-red-200 bg-red-50 rounded-sm px-4 py-3 text-sm text-red-700">{error}</div>}
        {loading ? (
          <div className="py-20 grid place-items-center text-sm text-[var(--muted)]"><span className="inline-flex items-center gap-2"><Loader2 size={16} className="animate-spin" />正在加载拍摄任务</span></div>
        ) : (
          <div className="mt-4 border-y border-[var(--border)] divide-y divide-[var(--border)] bg-white">
            {filtered.map((task) => (
              <article key={task.id} className="grid gap-4 px-4 py-5 md:grid-cols-[minmax(0,1fr)_240px_150px] md:items-center hover:bg-[#fafafa]">
                <div className="flex min-w-0 gap-3">
                  <div className="w-11 h-11 shrink-0 grid place-items-center rounded-sm bg-[#111] text-white"><ClipboardList size={19} /></div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="truncate text-sm font-medium">{task.project_name}</h2>
                      <span className={`text-[10px] px-2 py-0.5 rounded-full ${task.status === 'completed' ? 'badge-ok' : task.status === 'in_progress' ? 'badge-run' : 'badge-wait'}`}>{statusLabel[task.status]}</span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--muted)]">
                      <span>{task.section_count} 个脚本结构</span>
                      <span>{task.project_mode === 'reference' ? '参考视频仿写' : 'AI 原创脚本'}</span>
                      <span className="inline-flex items-center gap-1"><Clock3 size={12} />{localTime(task.created_at)}</span>
                    </div>
                    {task.note && <p className="mt-2 text-xs text-[var(--muted)] line-clamp-2">拍摄要求: {task.note}</p>}
                  </div>
                </div>
                <div className="min-w-0 text-xs">
                  <div className="mb-1 text-[10px] text-[var(--muted)]">{task.recipient_type === 'chat' ? '接收群' : '接收人'}</div>
                  <div className="flex min-w-0 items-center gap-1.5 font-medium">
                    <UserRound size={13} className="shrink-0" />
                    <span className="truncate">{task.recipient_name || task.assignee_name}</span>
                    {task.department_name && <span className="truncate text-[var(--muted)]">· {task.department_name}</span>}
                  </div>
                  {task.recipient_type === 'chat' && <p className="mt-1 text-[var(--muted)]">未指定拍摄负责人</p>}
                  <p className={`mt-2 ${task.notification_status === 'sent' ? 'text-emerald-700' : task.notification_status === 'failed' ? 'text-red-700' : 'text-amber-700'}`}>飞书：{notificationLabels[task.notification_status]}</p>
                  {task.notification_error && <p className="mt-1 break-words text-[var(--muted)]">{task.notification_error}</p>}
                  {task.notified_at && <p className="mt-1 text-[var(--muted)]">发送于 {localTime(task.notified_at)}</p>}
                  {task.can_retry_notification && <button className="mt-2 inline-flex items-center gap-1 underline disabled:opacity-50" disabled={retrying !== null} onClick={() => void retry(task)}>{retrying === task.id ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}重试通知</button>}
                </div>
                <Link className="btn-secondary h-9 justify-center" to={`/director-inspiration/${task.project_id}/workspace`}><ArrowUpRight size={14} />打开脚本</Link>
              </article>
            ))}
            {!filtered.length && (
              <div className="py-16 text-center text-sm text-[var(--muted)]">
                <CheckCircle2 size={24} className="mx-auto mb-3 opacity-55" />
                {tasks.length ? '当前筛选条件下暂无任务' : '还没有拍摄任务'}
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
