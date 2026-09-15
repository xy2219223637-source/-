import {useEffect,useRef,useState} from 'react';
import {ArrowLeft,Download,Loader2,Send} from 'lucide-react';
import {Link,useParams} from 'react-router-dom';
import {mediaUrl,productApi,videoLibraryApi} from '../api/client';
import type {Product,VideoLibraryAsset} from '../types';
import QianchuanReportPanel from '../components/QianchuanReportPanel';
import QianchuanUploadModal from '../components/QianchuanUploadModal';
import QianchuanUploadHistory,{useQianchuanUploads,uploadStatus} from '../components/QianchuanUploadHistory';

function formatDuration(seconds:number){const total=Math.round(seconds);return Math.floor(total/60).toString().padStart(2,'0')+':'+(total%60).toString().padStart(2,'0')}
function formatDate(value:string){return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))}

export default function FinalVideoDetail(){
  const {id}=useParams();
  const videoRef=useRef<HTMLVideoElement>(null);
  const [video,setVideo]=useState<VideoLibraryAsset|null>(null);
  const [product,setProduct]=useState<Product|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [pushNotice,setPushNotice]=useState(false);
  const [uploadRevision,setUploadRevision]=useState(0);
  const {uploads,error:uploadError}=useQianchuanUploads(Number(id)||0,uploadRevision);
  const latestUpload=uploads[0];
  const [reportUploadId,setReportUploadId]=useState('');
  const reportUploads=uploads.filter(item=>item.status==='succeeded'&&!!item.material_id);
  const reportUpload=reportUploads.find(item=>item.id===reportUploadId)||reportUploads[0];

  useEffect(()=>{let active=true;(async()=>{try{
    const videos=await videoLibraryApi.list(undefined,'finished','final_output');
    const selected=videos.find(item=>String(item.id)===id);
    if(!selected)throw new Error('not-found');
    const products=await productApi.list();
    if(active){setVideo(selected);setProduct(products.find(item=>item.id===selected.product_id)||null)}
  }catch{if(active)setError('没有找到这条成片')}finally{if(active)setLoading(false)}})();return()=>{active=false}},[id]);

  if(loading)return <div className="grid min-h-80 place-items-center text-sm text-[var(--muted)]"><Loader2 className="animate-spin"/></div>;
  if(error||!video)return <div className="panel p-10 text-center"><p>{error}</p><Link className="btn-secondary mt-5" to="/final-videos">返回成片库</Link></div>;
  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><Link to="/final-videos" className="mb-4 inline-flex items-center gap-2 text-sm text-[var(--muted)] hover:text-black"><ArrowLeft size={15}/>返回成片库</Link><h1 className="font-display text-4xl">{product?.name||'成片详情'}</h1><p className="mt-2 max-w-xl truncate text-sm text-[var(--muted)]" title={video.filename}>{video.filename}</p></div><div className="flex gap-2"><a className="btn-secondary" href={mediaUrl(video.object_url)} download><Download size={15}/>下载视频</a><button className="btn-primary" onClick={()=>setPushNotice(true)}><Send size={15}/>推送到千川</button></div></header>
    {pushNotice&&<QianchuanUploadModal videoAssetId={video.id} filename={video.filename} onClose={()=>setPushNotice(false)} onUploaded={()=>setUploadRevision(value=>value+1)}/>}
    <QianchuanUploadHistory uploads={uploads} error={uploadError} reload={()=>setUploadRevision(value=>value+1)}/>

    <section className="panel grid overflow-hidden lg:grid-cols-[minmax(360px,1.1fr)_minmax(320px,.9fr)]"><div className="grid min-h-[600px] place-items-center bg-black p-5"><video ref={videoRef} className="max-h-[70vh] max-w-full" controls playsInline src={mediaUrl(video.object_url)}/></div><div className="p-6 lg:p-8"><div className="flex items-center justify-between border-b border-[var(--border)] pb-5"><div><div className="text-xs text-[var(--muted)]">推送状态</div><div className="mt-2 font-medium">{uploadError?'上传状态读取失败':uploadStatus(latestUpload)}</div></div><span className="rounded-full bg-[#f0f0ee] px-3 py-1 text-xs">{reportUpload?'千川素材':'本地成片'}</span></div><dl className="mt-6 grid grid-cols-[100px_1fr] gap-x-4 gap-y-5 text-sm"><dt className="text-[var(--muted)]">商品</dt><dd>{product?.name||'—'}</dd><dt className="text-[var(--muted)]">文件名</dt><dd className="break-all">{video.filename}</dd><dt className="text-[var(--muted)]">时长</dt><dd>{formatDuration(video.duration_seconds)}</dd><dt className="text-[var(--muted)]">画面规格</dt><dd>{video.width} × {video.height}{video.height>video.width?' · 竖版':video.height===video.width?' · 方形':' · 横版'}</dd><dt className="text-[var(--muted)]">生成时间</dt><dd>{formatDate(video.created_at)}</dd><dt className="text-[var(--muted)]">千川素材 ID</dt><dd className="break-all">{reportUpload?.material_id||latestUpload?.material_id||'—'}</dd><dt className="text-[var(--muted)]">投放使用</dt><dd>— 个计划</dd></dl></div></section>

    {reportUploads.length>1&&<label className="block text-sm">查询投放账户<select aria-label="查询投放账户" className="input-select mt-2 w-full" value={reportUpload?.id||''} onChange={event=>setReportUploadId(event.target.value)}>{reportUploads.map(item=><option key={item.id} value={item.id}>{item.advertiser_name||'千川账户'} · {item.advertiser_id} · 素材 {item.material_id}</option>)}</select></label>}
    <QianchuanReportPanel key={reportUpload?.id||'no-upload'} upload={reportUpload}/>
  </div>;
}
