import {useEffect,useState} from 'react';
import {Loader2} from 'lucide-react';
import {Link,useParams} from 'react-router-dom';
import {videoRepairApi} from '../api/client';
import type {VideoRepairTask} from '../api/client';
import VideoRepairWorkbench from '../components/VideoRepairWorkbench';
import VideoRepairWorkbenchV4 from '../components/VideoRepairWorkbenchV4';

export default function VideoRepairWorkspace(){
  const {taskId}=useParams();const id=Number(taskId);const [task,setTask]=useState<VideoRepairTask|null>(null);const [error,setError]=useState('');
  useEffect(()=>{if(!Number.isInteger(id)||id<=0){setError('任务不存在');return}videoRepairApi.get(id).then(setTask).catch(()=>setError('任务不存在或无权访问'))},[id]);
  if(error)return <div className="panel p-10 text-center"><p>{error}</p><Link className="btn-secondary mt-5" to="/video-repair">返回任务列表</Link></div>;
  if(!task)return <div className="grid min-h-80 place-items-center"><Loader2 className="animate-spin"/></div>;
  return <div className="mx-auto max-w-7xl space-y-5 animate-fade-in"><Link className="text-sm text-[var(--muted)] hover:text-[var(--text)]" to="/video-repair">← 返回审核失败修改</Link>{task.schema_version==='video_repair_task.v2'?<VideoRepairWorkbenchV4 key={task.updated_at} initialTask={task}/>:<VideoRepairWorkbench key={task.updated_at} initialTask={task}/>}</div>;
}
