import {useEffect,useMemo,useRef,useState} from 'react';
import {ArrowLeft,Download,ExternalLink,Loader2,Send} from 'lucide-react';
import {Link,useParams} from 'react-router-dom';
import {mediaUrl,productApi,videoLibraryApi} from '../api/client';
import type {Product,VideoLibraryAsset} from '../types';

type MetricKey='click_cnt'|'dy_follow'|'dy_comment'|'dy_like'|'user_lose_cnt';
const metricLabels:Record<MetricKey,string>={click_cnt:'点击数',dy_follow:'关注数',dy_comment:'评论数',dy_like:'点赞数',user_lose_cnt:'流失数'};
const metricColors:Record<MetricKey,string>={click_cnt:'#6d5dfc',dy_follow:'#168a5b',dy_comment:'#d97706',dy_like:'#db2777',user_lose_cnt:'#e05252'};
const demoPoints=Array.from({length:31},(_,second)=>({
  second,
  click_cnt:Math.round(22+Math.sin(second*.7)*8+(second<4?28:0)+(second===17?22:0)),
  dy_follow:Math.round(3+Math.max(0,Math.sin(second*.5))*4),
  dy_comment:Math.round(2+Math.max(0,Math.cos(second*.8))*3),
  dy_like:Math.round(9+Math.max(0,Math.sin(second*.55))*11+(second===8?12:0)),
  user_lose_cnt:Math.round(12+second*.7+(second===4?24:0)+(second===21?18:0)),
}));

function formatDuration(seconds:number){const total=Math.round(seconds);return `${Math.floor(total/60).toString().padStart(2,'0')}:${(total%60).toString().padStart(2,'0')}`}
function formatDate(value:string){return new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value))}
function MetricCard({label,value,helper}:{label:string;value:string;helper?:string}){
  return <div className="rounded border border-[var(--border)] bg-white p-4"><div className="text-xs text-[var(--muted)]">{label}</div><div className="mt-2 text-2xl font-medium tracking-tight">{value}</div>{helper&&<div className="mt-1 text-[11px] text-[var(--muted)]">{helper}</div>}</div>;
}

export default function FinalVideoDetail(){
  const {id}=useParams();
  const videoRef=useRef<HTMLVideoElement>(null);
  const [video,setVideo]=useState<VideoLibraryAsset|null>(null);
  const [product,setProduct]=useState<Product|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [pushNotice,setPushNotice]=useState(false);
  const [primary,setPrimary]=useState<MetricKey>('click_cnt');
  const [secondary,setSecondary]=useState<MetricKey>('user_lose_cnt');
  const [currentSecond,setCurrentSecond]=useState(0);
  const [hoverSecond,setHoverSecond]=useState<number|null>(null);

  useEffect(()=>{let active=true;(async()=>{try{
    const videos=await videoLibraryApi.list(undefined,'finished','final_output');
    const selected=videos.find(item=>String(item.id)===id);
    if(!selected)throw new Error('not-found');
    const products=await productApi.list();
    if(active){setVideo(selected);setProduct(products.find(item=>item.id===selected.product_id)||null)}
  }catch{if(active)setError('没有找到这条成片')}finally{if(active)setLoading(false)}})();return()=>{active=false}},[id]);

  const chartPoints=useMemo(()=>{
    const duration=Math.max(1,Math.min(30,Math.round(video?.duration_seconds||30)));
    return demoPoints.filter(point=>point.second<=duration);
  },[video]);
  const plot={left:54,right:706,top:20,bottom:200};
  const lastSecond=chartPoints.at(-1)?.second||1;
  const niceMax=(value:number)=>Math.max(5,Math.ceil(value/10)*10);
  const primaryMax=niceMax(Math.max(...chartPoints.map(point=>point[primary]),1));
  const secondaryMax=niceMax(Math.max(...chartPoints.map(point=>point[secondary]),1));
  const xFor=(second:number)=>plot.left+(second/lastSecond)*(plot.right-plot.left);
  const yFor=(value:number,max:number)=>plot.bottom-(value/max)*(plot.bottom-plot.top);
  const line=(key:MetricKey,max:number)=>chartPoints.map((point,index)=>`${index?'L':'M'} ${xFor(point.second)} ${yFor(point[key],max)}`).join(' ');
  const selectedPoint=chartPoints.reduce((closest,point)=>Math.abs(point.second-currentSecond)<Math.abs(closest.second-currentSecond)?point:closest,chartPoints[0]);
  const focusPoint=hoverSecond===null?selectedPoint:chartPoints.reduce((closest,point)=>Math.abs(point.second-hoverSecond)<Math.abs(closest.second-hoverSecond)?point:closest,chartPoints[0]);
  const xTicks=Array.from(new Set(Array.from({length:7},(_,index)=>Math.round(lastSecond*index/6))));
  const secondFromPointer=(event:React.MouseEvent<SVGSVGElement>)=>{
    const matrix=event.currentTarget.getScreenCTM();if(!matrix)return;
    const cursor=event.currentTarget.createSVGPoint();cursor.x=event.clientX;cursor.y=event.clientY;
    const svgX=cursor.matrixTransform(matrix.inverse()).x;
    return Math.round(Math.max(0,Math.min(lastSecond,(svgX-plot.left)/(plot.right-plot.left)*lastSecond)));
  };
  const seek=(event:React.MouseEvent<SVGSVGElement>)=>{
    const second=secondFromPointer(event);if(second===undefined)return;
    if(videoRef.current)videoRef.current.currentTime=second;setCurrentSecond(second);
  };

  if(loading)return <div className="grid min-h-80 place-items-center text-sm text-[var(--muted)]"><Loader2 className="animate-spin"/></div>;
  if(error||!video)return <div className="panel p-10 text-center"><p>{error}</p><Link className="btn-secondary mt-5" to="/final-videos">返回成片库</Link></div>;
  const overview=[['消耗','¥1,280.60'],['成交金额','¥4,621.80'],['成交订单','126'],['支付 ROI','3.61']];
  const delivery=[['展示次数','120,540'],['点击次数','6,230'],['点击率','5.17%'],['千次展现费用','¥10.62']];
  const quality=[['播放数','98,320'],['3 秒播放率','72.4%'],['有效播放率','48.6%'],['完播率','19.2%'],['平均播放时长','8.4 秒']];
  const interactions=[['点赞','2,310'],['评论','186'],['分享','93'],['新增关注','428']];
  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <header className="flex flex-wrap items-end justify-between gap-4"><div><Link to="/final-videos" className="mb-4 inline-flex items-center gap-2 text-sm text-[var(--muted)] hover:text-black"><ArrowLeft size={15}/>返回成片库</Link><h1 className="font-display text-4xl">{product?.name||'成片详情'}</h1><p className="mt-2 max-w-xl truncate text-sm text-[var(--muted)]" title={video.filename}>{video.filename}</p></div><div className="flex gap-2"><a className="btn-secondary" href={mediaUrl(video.object_url)} download><Download size={15}/>下载视频</a><button className="btn-primary" onClick={()=>setPushNotice(true)}><Send size={15}/>推送到千川</button></div></header>
    {pushNotice&&<div className="flex items-center justify-between border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"><span>千川账户与素材推送接口尚未配置，目前不会执行真实推送。</span><button className="underline" onClick={()=>setPushNotice(false)}>知道了</button></div>}

    <section className="panel grid overflow-hidden lg:grid-cols-[minmax(360px,1.1fr)_minmax(320px,.9fr)]"><div className="grid min-h-[600px] place-items-center bg-black p-5"><video ref={videoRef} className="max-h-[70vh] max-w-full" controls playsInline src={mediaUrl(video.object_url)} onTimeUpdate={event=>setCurrentSecond(event.currentTarget.currentTime)}/></div><div className="p-6 lg:p-8"><div className="flex items-center justify-between border-b border-[var(--border)] pb-5"><div><div className="text-xs text-[var(--muted)]">推送状态</div><div className="mt-2 font-medium">未推送到千川</div></div><span className="rounded-full bg-[#f0f0ee] px-3 py-1 text-xs">本地成片</span></div><dl className="mt-6 grid grid-cols-[100px_1fr] gap-x-4 gap-y-5 text-sm"><dt className="text-[var(--muted)]">商品</dt><dd>{product?.name||'—'}</dd><dt className="text-[var(--muted)]">文件名</dt><dd className="break-all">{video.filename}</dd><dt className="text-[var(--muted)]">时长</dt><dd>{formatDuration(video.duration_seconds)}</dd><dt className="text-[var(--muted)]">画面规格</dt><dd>{video.width} × {video.height}{video.height>video.width?' · 竖版':video.height===video.width?' · 方形':' · 横版'}</dd><dt className="text-[var(--muted)]">生成时间</dt><dd>{formatDate(video.created_at)}</dd><dt className="text-[var(--muted)]">千川素材 ID</dt><dd>—</dd><dt className="text-[var(--muted)]">投放使用</dt><dd>— 个计划</dd></dl></div></section>

    <section className="panel p-5"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-lg font-medium">投放表现</h2><p className="mt-1 text-xs text-[var(--muted)]">展示结构依据巨量千川素材报表字段设计</p></div><div className="flex items-center gap-3"><span className="rounded bg-amber-50 px-2.5 py-1 text-xs text-amber-800">示例数据 · 尚未接入千川</span><select className="input-select"><option>近 7 天</option><option>近 30 天</option><option>自定义时间</option></select></div></div><div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">{overview.map(([label,value])=><MetricCard key={label} label={label} value={value}/>)}</div><div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">{delivery.map(([label,value])=><MetricCard key={label} label={label} value={value}/>)}</div><div className="mt-7 flex items-center justify-between"><h3 className="font-medium">视频播放质量</h3><span className="text-xs text-[var(--muted)]">数据更新时间：—</span></div><div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-5">{quality.map(([label,value])=><MetricCard key={label} label={label} value={value}/>)}</div></section>

    <section className="panel p-5"><div className="flex items-center justify-between"><div><h2 className="text-lg font-medium">互动表现</h2><p className="mt-1 text-xs text-[var(--muted)]">当前时间范围内的互动汇总</p></div><span className="rounded bg-amber-50 px-2.5 py-1 text-xs text-amber-800">示例数据</span></div><div className="mt-5 grid grid-cols-2 gap-3 lg:grid-cols-4">{interactions.map(([label,value])=><MetricCard key={label} label={label} value={value}/>)}</div></section>

    <section className="panel p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><h2 className="text-lg font-medium">逐秒互动与流失</h2><p className="mt-1 text-xs text-[var(--muted)]">移动鼠标查看每秒完整数据，点击可跳转到视频对应位置；展示的是事件计数，不是点击率或流失率。</p></div><span className="rounded bg-amber-50 px-2.5 py-1 text-xs text-amber-800">示例数据</span></div><div className="mt-5 grid gap-5 lg:grid-cols-[260px_1fr]"><div className="rounded bg-black p-3"><video className="aspect-[9/16] w-full object-contain" muted playsInline src={`${mediaUrl(video.object_url)}#t=${Math.floor(currentSecond)}`}/><div className="mt-3 text-center text-xs text-white/70">当前位置 {formatDuration(currentSecond)}</div></div><div><div className="mb-4 flex flex-wrap gap-3"><label className="text-xs text-[var(--muted)]">指标一<select className="input-select mt-1 block" value={primary} onChange={event=>setPrimary(event.target.value as MetricKey)}>{Object.entries(metricLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label className="text-xs text-[var(--muted)]">指标二<select className="input-select mt-1 block" value={secondary} onChange={event=>setSecondary(event.target.value as MetricKey)}>{Object.entries(metricLabels).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label></div><div className="overflow-x-auto"><svg className="min-w-[760px] cursor-crosshair select-none" viewBox="0 0 760 250" role="img" aria-label="逐秒互动曲线" onClick={seek} onMouseMove={event=>setHoverSecond(secondFromPointer(event)??null)} onMouseLeave={()=>setHoverSecond(null)}>
        {[0,1,2,3,4].map(index=>{const y=plot.bottom-(index/4)*(plot.bottom-plot.top);return <g key={index}><line x1={plot.left} y1={y} x2={plot.right} y2={y} stroke="#e5e5e1"/><text x={plot.left-9} y={y+4} textAnchor="end" fontSize="10" fill="#666">{Math.round(primaryMax*index/4)}</text><text x={plot.right+9} y={y+4} fontSize="10" fill="#666">{Math.round(secondaryMax*index/4)}</text></g>})}
        {xTicks.map(second=><g key={second}><line x1={xFor(second)} y1={plot.top} x2={xFor(second)} y2={plot.bottom} stroke="#efefec"/><text x={xFor(second)} y="222" textAnchor="middle" fontSize="10" fill="#666">{second}s</text></g>)}
        <text x={plot.left} y="12" fontSize="10" fill={metricColors[primary]}>{metricLabels[primary]}</text><text x={plot.right} y="12" textAnchor="end" fontSize="10" fill={metricColors[secondary]}>{metricLabels[secondary]}</text>
        <path d={line(primary,primaryMax)} fill="none" stroke={metricColors[primary]} strokeWidth="3"/><path d={line(secondary,secondaryMax)} fill="none" stroke={metricColors[secondary]} strokeWidth="3"/>
        <line x1={xFor(focusPoint.second)} y1={plot.top} x2={xFor(focusPoint.second)} y2={plot.bottom} stroke="#4b5563" strokeWidth="1.5" strokeDasharray="5 4"/>
        <line x1={plot.left} y1={yFor(focusPoint[primary],primaryMax)} x2={plot.right} y2={yFor(focusPoint[primary],primaryMax)} stroke={metricColors[primary]} strokeWidth="1.5" strokeDasharray="5 4" opacity=".8"/>
        <circle cx={xFor(focusPoint.second)} cy={yFor(focusPoint[primary],primaryMax)} r="5" fill="white" stroke={metricColors[primary]} strokeWidth="3"/><circle cx={xFor(focusPoint.second)} cy={yFor(focusPoint[secondary],secondaryMax)} r="5" fill="white" stroke={metricColors[secondary]} strokeWidth="3"/>
        <g transform={`translate(${xFor(focusPoint.second)>520?xFor(focusPoint.second)-174:xFor(focusPoint.second)+12},${plot.top+8})`} style={{pointerEvents:'none'}}><rect width="162" height="78" rx="5" fill="white" stroke="#d8d8d4"/><text x="12" y="20" fontSize="11" fontWeight="600" fill="#111">视频第 {focusPoint.second} 秒</text><line x1="10" y1="29" x2="152" y2="29" stroke="#ececea"/>{([primary,secondary] as MetricKey[]).map((key,index)=><g key={`${key}-${index}`} transform={`translate(0,${43+index*20})`}><circle cx="14" cy="0" r="3.5" fill={metricColors[key]}/><text x="24" y="4" fontSize="10" fill="#666">{metricLabels[key]}</text><text x="148" y="4" textAnchor="end" fontSize="10" fontWeight="600" fill="#111">{focusPoint[key]}</text></g>)}</g>
        <g transform={`translate(${Math.max(plot.left,Math.min(plot.right-70,xFor(focusPoint.second)-35))},228)`}><rect width="70" height="21" rx="3" fill="#4b5563"/><text x="35" y="14" textAnchor="middle" fontSize="10" fill="white">{focusPoint.second}s</text></g>
      </svg></div><div className="mt-2 flex gap-5 text-xs"><span style={{color:metricColors[primary]}}>● {metricLabels[primary]}（左轴）</span><span style={{color:metricColors[secondary]}}>● {metricLabels[secondary]}（右轴）</span></div><div className="mt-5 flex items-start gap-2 rounded bg-[#f6f5f2] p-3 text-xs text-[var(--muted)]"><ExternalLink size={14} className="mt-0.5 shrink-0"/><span>正式接入后，数据将按千川素材 ID、广告账户和日期范围查询；逐秒数据为 T+1，次日 9 点后稳定。</span></div></div></div>
    </section>
  </div>;
}
