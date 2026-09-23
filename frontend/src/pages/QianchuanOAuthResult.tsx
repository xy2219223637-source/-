import {useEffect, useState} from 'react';
import {Link, useSearchParams} from 'react-router-dom';
import {Loader2} from 'lucide-react';
import {qianchuanApi, type QianchuanSessionResult} from '../api/client';
import {qianchuanError} from '../components/QianchuanUploadModal';

export default function QianchuanOAuthResult() {
  const [params] = useSearchParams();
  const sessionId = params.get('session_id') || '';
  const [result, setResult] = useState<QianchuanSessionResult | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      if (!/^[0-9a-f-]{36}$/.test(sessionId)) { setError('授权结果地址无效，请从成片详情重新发起授权'); return; }
      try {
        const next = await qianchuanApi.session(sessionId);
        if (!active) return;
        setResult(next); setError('');
        const waiting = ['pending', 'processing'].includes(next.status) ||
          (next.status === 'completed' && ['sync_pending', 'syncing'].includes(next.connection_status));
        if (waiting && !document.hidden) timer = setTimeout(load, 2000);
      } catch (err) { if (active) setError(qianchuanError(err)); }
    }
    function visibility() { if (timer) clearTimeout(timer); if (!document.hidden) void load(); }
    void load();
    document.addEventListener('visibilitychange', visibility);
    return () => { active = false; if (timer) clearTimeout(timer); document.removeEventListener('visibilitychange', visibility); };
  }, [sessionId, revision]);
  const ready = result?.status === 'completed' && result.connection_status === 'active';
  const waiting = result && (['pending', 'processing'].includes(result.status) ||
    (result.status === 'completed' && ['sync_pending', 'syncing'].includes(result.connection_status)));
  return <section className="panel mx-auto mt-10 max-w-xl p-8">
    <h1 className="text-2xl font-medium">{ready ? '授权已连接' : '千川授权结果'}</h1>
    {(!result && !error || waiting) && <p className="mt-4 flex items-center gap-2 text-sm"><Loader2 size={16} className="animate-spin"/>正在处理授权与账户同步</p>}
    {error && <p role="alert" className="mt-4 text-sm text-red-600">{error}</p>}
    {result?.message && <p className="mt-4 text-sm text-amber-800">{result.message}</p>}
    {result?.status === 'completed' && result.connection_error && <p className="mt-4 text-sm text-amber-800">{result.connection_error}</p>}
    {result?.status === 'completed' && result.connection_status === 'reauthorization_required' && !result.connection_error && <p className="mt-4 text-sm text-amber-800">授权已失效，请返回成片重新授权。</p>}
    {ready && <p className="mt-4 text-sm">账户范围已同步。返回成片详情后，可打开千川窗口核对并选择账户。</p>}
    {result?.status === 'completed' && result.connection_status === 'disabled' && <p className="mt-4 text-sm">此连接已停用，请重新授权。</p>}
    {result?.connection_status === 'deleted' && <p className="mt-4 text-sm">此授权连接已删除。需要使用时，请返回成片添加新的授权。</p>}
    <div className="mt-6 flex gap-3">
      <Link className="btn-primary" to={result?.return_path || '/final-videos'} onClick={() => sessionStorage.removeItem('qianchuan_oauth_return')}>返回成片</Link>
      {error && <button className="btn-secondary" onClick={() => setRevision(value => value + 1)}>重新查询</button>}
    </div>
  </section>;
}
