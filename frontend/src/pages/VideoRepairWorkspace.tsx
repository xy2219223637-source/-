import {Component,useEffect,useState} from 'react';
import type {ErrorInfo,ReactNode} from 'react';
import {Loader2} from 'lucide-react';
import {Link,useParams} from 'react-router-dom';
import {videoRepairApi} from '../api/client';
import type {VideoRepairTask} from '../api/client';
import VideoRepairWorkbenchV4 from '../components/VideoRepairWorkbenchV4';

class VideoRepairErrorBoundary extends Component<{children:ReactNode},{error:Error|null}> {
  state:{error:Error|null}={error:null};
  static getDerivedStateFromError(error:Error){return {error};}
  componentDidCatch(error:Error,info:ErrorInfo){console.error('Video repair page failed to render',error,info);}
  render(){
    if(this.state.error)return <div role="alert" className="panel p-8">
      <h2 className="text-lg font-semibold">返修页面加载失败</h2>
      <p className="mt-2 text-sm">任务数据仍保留。请刷新页面重试；若持续出现，请提供浏览器控制台的第一条错误。</p>
      {import.meta.env.DEV&&<pre className="mt-3 overflow-auto rounded bg-slate-50 p-3 text-xs">{this.state.error.message}</pre>}
      <button className="btn-secondary mt-4" onClick={()=>window.location.reload()}>重新加载</button>
    </div>;
    return this.props.children;
  }
}

export default function VideoRepairWorkspace(){
  const {taskId}=useParams();const id=Number(taskId);const [task,setTask]=useState<VideoRepairTask|null>(null);const [error,setError]=useState('');
  useEffect(()=>{if(!Number.isInteger(id)||id<=0){setError('任务不存在');return}videoRepairApi.get(id).then(setTask).catch(()=>setError('任务不存在或无权访问'))},[id]);
  if(error)return <div className="panel p-10 text-center"><p>{error}</p><Link className="btn-secondary mt-5" to="/video-repair">返回任务列表</Link></div>;
  if(!task)return <div className="grid min-h-80 place-items-center"><Loader2 className="animate-spin"/></div>;
  return <div className="mx-auto max-w-7xl space-y-5 animate-fade-in"><Link className="text-sm text-[var(--muted)] hover:text-[var(--text)]" to="/video-repair">← 返回审核失败修改</Link><VideoRepairErrorBoundary key={task.updated_at}><VideoRepairWorkbenchV4 initialTask={task}/></VideoRepairErrorBoundary></div>;
}
