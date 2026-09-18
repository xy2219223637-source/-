import {useEffect,useRef,useState} from 'react';
import type {ComponentProps,ReactNode,SyntheticEvent} from 'react';
import {createPortal} from 'react-dom';
import {ArrowLeft,ChevronLeft,ChevronRight,Loader2,X} from 'lucide-react';
import {Link,useLocation,useNavigate,useSearchParams} from 'react-router-dom';
import {mediaUrl,videoLibraryApi} from '../api/client';
import type {VideoLineageAsset,VideoLineageRelation,VideoLineageResult} from '../types';
import LazyVideo from './LazyVideo';
import {aggregateReports,demoPeriod,demoReport,money,num,pct,reportMetrics} from './videoPerformanceDemo';
import './VideoLineage.css';

export function videoDetailPath(asset:Pick<VideoLineageAsset,'id'|'asset_kind'|'library_type'>){
  return `/${asset.asset_kind==='final_output'?'final-videos':asset.library_type==='finished'?'finished-video-assets':'semi-finished-assets'}/${asset.id}`;
}
export function timecode(seconds:number){
  const milliseconds=Math.round(Math.max(0,seconds)*1000);
  return `${Math.floor(milliseconds/60000).toString().padStart(2,'0')}:${(Math.floor(milliseconds/1000)%60).toString().padStart(2,'0')}.${(milliseconds%1000).toString().padStart(3,'0')}`;
}
const range=(start:number,end:number)=>`${timecode(start)} - ${timecode(end)}`;
const dateLabel=(value:string)=>new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});

function RelatedVideoLink(props:Omit<ComponentProps<typeof Link>,'state'>){
  const location=useLocation();
  const navigate=useNavigate();
  return <Link {...props} onClick={event=>{
    if(event.button!==0||event.metaKey||event.ctrlKey||event.altKey||event.shiftKey)return;
    event.preventDefault();
    navigate(props.to,{state:{lineageReturn:{url:location.pathname+location.search,state:location.state,scroll:window.scrollY}}});
  }}/>;
}

export function LineageBack({fallback,label}:{fallback:string;label:string}){
  const location=useLocation();
  const previous=location.state?.lineageReturn;
  const valid=typeof previous?.url==='string'&&/^\/(final-videos|finished-video-assets|semi-finished-assets)\/\d+(\?|$)/.test(previous.url);
  return <Link to={valid?previous.url:fallback} state={valid?{...previous.state,restoreScroll:previous.scroll}:undefined} className="back"><ArrowLeft size={15}/>{valid?'返回上一条视频':label}</Link>;
}

export function useSegmentPreview(assetId:number){
  const videoRef=useRef<HTMLVideoElement>(null);
  const activeRange=useRef<[number,number]|null>(null);
  useEffect(()=>{activeRange.current=null},[assetId]);
  const position=()=>{if(videoRef.current&&activeRange.current)videoRef.current.currentTime=activeRange.current[0]};
  const locate=(start:number,end:number)=>{
    activeRange.current=[start,end];
    const video=videoRef.current;
    if(video){video.pause();if(video.readyState>=1)position();video.scrollIntoView({behavior:'smooth',block:'center'});video.focus()}
  };
  const onTimeUpdate=(event:SyntheticEvent<HTMLVideoElement>)=>{
    const video=event.currentTarget;
    if(activeRange.current&&video.currentTime>=activeRange.current[1]){video.pause();activeRange.current=null}
  };
  const onSeeking=(event:SyntheticEvent<HTMLVideoElement>)=>{
    const current=event.currentTarget.currentTime;
    if(activeRange.current&&(current<activeRange.current[0]-.05||current>activeRange.current[1]+.05))activeRange.current=null;
  };
  return {videoRef,locate,onTimeUpdate,onSeeking,onLoadedMetadata:position};
}

export function MetricCards({values}:{values:[string,string][]}){return <>{values.map(([label,value])=><div className="metric" key={label}><small>{label}</small><strong>{value}</strong></div>)}</>}
const displayTime=(seconds:number)=>timecode(seconds).replace(/\.000$/,'');
const displayRange=(a:number,b:number)=>`${displayTime(a)} - ${displayTime(b)}`;
const durationLabel=(n:number)=>Number(n.toFixed(3)).toString();

function SegmentPlayer({asset,start,end,label}:{asset:VideoLineageAsset;start:number;end:number;label:string}){
  const ref=useRef<HTMLVideoElement>(null);
  const [error,setError]=useState(false);
  return <div><div className="compare-label"><span>{label}</span><span className="muted">{label==='来源视频'?(asset.asset_kind==='material'?'原素材':'成片素材'):'成片'}</span></div><div className="compare-preview"><video ref={ref} controls playsInline preload="metadata" src={mediaUrl(asset.object_url)}
    onLoadedMetadata={event=>{event.currentTarget.currentTime=start}}
    onPlay={event=>{const v=event.currentTarget;if(v.currentTime<start||v.currentTime>=end)v.currentTime=start}}
    onTimeUpdate={event=>{const v=event.currentTarget;if(v.currentTime>=end&&!v.paused){v.pause();v.currentTime=end}}}
    onError={()=>setError(true)}/></div>
    {error&&<p role="alert" className="range-error">视频暂时无法播放, 请检查文件是否可访问.</p>}
    <div className="compare-title">{asset.filename}</div><div className="range-box"><span>{label==='来源视频'?'来源片段':'成片位置'}</span><strong>{displayRange(start,end)}</strong></div>
    <button className="compare-play" disabled={error} onClick={()=>{const v=ref.current;if(v){v.currentTime=start;void v.play().catch(()=>setError(true))}}}>播放此片段</button>
  </div>;
}

function SegmentDrawer({relation,initialIndex,direction,onClose,onLocate,spendStart,spendEnd}:{relation:VideoLineageRelation;initialIndex:number;direction:'sources'|'used';onClose:()=>void;onLocate:(start:number,end:number)=>void;spendStart:string;spendEnd:string}){
  const dialog=useRef<HTMLDialogElement>(null);
  const [index,setIndex]=useState(initialIndex);
  const segment=relation.segments[index];
  const duration=segment.target_end-segment.target_start,ratio=relation.target.duration_seconds?duration/relation.target.duration_seconds:0;
  const report=demoReport(relation.target.id,spendStart,spendEnd);
  useEffect(()=>{
    const element=dialog.current,focused=document.activeElement as HTMLElement|null,overflow=document.body.style.overflow;
    document.body.style.overflow='hidden';element?.showModal();
    return()=>{element?.close();document.body.style.overflow=overflow;focused?.focus()};
  },[]);
  return createPortal(<dialog ref={dialog} aria-labelledby="lineage-drawer-title" onCancel={event=>{event.preventDefault();onClose()}} onClick={event=>{if(event.target===event.currentTarget)onClose()}} className="lineage-ui">
    <header className="drawer-head"><div><h2 id="lineage-drawer-title">引用片段对照</h2><p>{relation.source.asset_id} → {relation.target.asset_id}</p></div><button autoFocus aria-label="关闭片段对照" onClick={onClose}><X size={16}/></button></header>
    <div className="drawer-body"><span className="badge verified">来源依据: 剪辑记录</span><div className="segments">{relation.segments.map((item,i)=><button key={item.id} className={i===index?'selected':''} aria-label={`片段 ${i+1}`} aria-pressed={i===index} onClick={()=>setIndex(i)}>片段 {i+1} · {durationLabel(item.target_end-item.target_start)} 秒</button>)}</div>
      <div className="compare"><SegmentPlayer key={`source-${segment.id}`} asset={relation.source} start={segment.source_start} end={segment.source_end} label="来源视频"/><SegmentPlayer key={`target-${segment.id}`} asset={relation.target} start={segment.target_start} end={segment.target_end} label="引用后的成片"/></div>
      <section className="drawer-summary"><h3>本段画面使用 {durationLabel(duration)} 秒 · 共 {relation.segments.length} 段引用</h3><p>双方时间位置来自对应剪辑记录. 同一条成片引用多个片段时, 引用次数仍计为 1.</p><p>引用时间 {dateLabel(relation.created_at)}</p></section>
      <section className="drawer-summary"><h3>引用后成片的表现</h3><p>{spendStart||'不限开始'} 至 {spendEnd||'不限结束'} · <span className="demo">示例数据</span></p><div className="metrics compact"><MetricCards values={[
        ['成片消耗',money(report.spend)],['成片 ROI',report.spend?(report.amount/report.spend).toFixed(2):'—'],['成交金额',money(report.amount)],['转化数',num(report.orders)],
      ]}/></div><h3 style={{marginTop:20}}>当前片段的时长分摊估算</h3><p>按片段时长占成片 {pct(duration,relation.target.duration_seconds)} 进行分摊, 不代表该镜头的独立转化贡献.</p><div className="metrics compact"><MetricCards values={[
        ['消耗预估',money(report.spend*ratio)],['成交金额预估',money(report.amount*ratio)],['转化数预估',num(report.orders*ratio)],['时长占比',pct(duration,relation.target.duration_seconds)],
      ]}/></div></section>
    </div><footer className="drawer-footer"><button onClick={onClose}>关闭</button><button className="primary" aria-label="定位到当前视频片段" onClick={()=>{onClose();onLocate(direction==='sources'?segment.target_start:segment.source_start,direction==='sources'?segment.target_end:segment.source_end)}}>定位到详情视频</button></footer>
  </dialog>,document.body);
}

export default function VideoLineage({asset,onLocate,analytics,onSummary}:{asset:VideoLineageAsset;onLocate:(start:number,end:number)=>void;analytics?:ReactNode;onSummary?:(counts:{source_count:number;reference_count:number})=>void}){
  const [params,setParams]=useSearchParams();const location=useLocation();
  const isFinal=asset.asset_kind==='final_output',requested=params.get('tab');
  const tab=!isFinal?'used':requested==='used'||requested==='analytics'?requested:'sources';
  const direction=tab==='sources'?'sources':'used';
  const q=params.get('lineage_q')||'',sort=params.get('lineage_sort')||'recent',start=params.get('lineage_start')||'',end=params.get('lineage_end')||'';
  const spendStart=params.get('spend_start')??demoPeriod.start,spendEnd=params.get('spend_end')??demoPeriod.end;
  const page=Math.max(1,Number(params.get('lineage_page'))||1);
  const [data,setData]=useState<VideoLineageResult|null>(null),[counts,setCounts]=useState<{source_count:number;reference_count:number}|null>(null);
  const [loading,setLoading]=useState(true),[error,setError]=useState(''),[retry,setRetry]=useState(0);
  const [drawer,setDrawer]=useState<{relation:VideoLineageRelation;index:number}|null>(null);
  const restored=useRef(''),summaryRef=useRef<HTMLDivElement>(null),onSummaryRef=useRef(onSummary);onSummaryRef.current=onSummary;
  const invalidDates=!!(start&&end&&start>end),invalidSpend=!!(spendStart&&spendEnd&&spendStart>spendEnd);
  const setQuery=(values:Record<string,string>,reset=true)=>{
    setParams(current=>{const next=new URLSearchParams(current);if(reset)next.delete('lineage_page');Object.entries(values).forEach(([key,value])=>{if(value||key.startsWith('spend_'))next.set(key,value);else next.delete(key)});return next},{replace:true,state:location.state?{...location.state,restoreScroll:undefined}:undefined});
  };
  useEffect(()=>{
    let active=true;setData(null);setError('');setLoading(true);setDrawer(null);
    if(tab!=='analytics'&&direction==='used'&&invalidDates){setLoading(false);return}
    const timer=window.setTimeout(()=>{
      videoLibraryApi.lineage(asset.id,{direction:tab==='analytics'?'sources':direction,q:direction==='used'&&tab!=='analytics'?q:undefined,sort:['recent','old','name'].includes(sort)?sort:'recent',
        start_date:direction==='used'&&tab!=='analytics'?start||undefined:undefined,end_date:direction==='used'&&tab!=='analytics'?end||undefined:undefined,
        page:direction==='sources'||tab==='analytics'?1:page,page_size:direction==='sources'?100:6})
        .then(result=>{if(active){setData(result);const summary={source_count:result.source_count,reference_count:result.reference_count};setCounts(summary);onSummaryRef.current?.(summary)}}).catch(()=>{if(active)setError('引用记录加载失败, 请重试.')}).finally(()=>{if(active)setLoading(false)});
    },150);
    return()=>{active=false;window.clearTimeout(timer)};
  },[asset.id,tab,direction,q,sort,start,end,page,invalidDates,retry]);
  useEffect(()=>{
    if(!loading&&location.state?.restoreScroll!==undefined&&restored.current!==location.key){restored.current=location.key;const frame=requestAnimationFrame(()=>window.scrollTo(0,location.state.restoreScroll));return()=>cancelAnimationFrame(frame)}
  },[loading,location]);
  const tabs=isFinal?[['sources','镜头溯源'],['used','被引用后出片'],['analytics','投放分析']]:[['used','被引用后出片']];
  const pieces=(data?.items||[]).flatMap(relation=>relation.segments.map((segment,index)=>({relation,segment,index}))).sort((a,b)=>a.segment.target_start-b.segment.target_start);
  const openDrawer=(relation:VideoLineageRelation,index=0)=>{document.querySelectorAll('video').forEach(video=>video.pause());setDrawer({relation,index})};
  const report=aggregateReports(data?.matched_asset_ids??data?.items.map(r=>r.target.id)??[],spendStart,spendEnd);
  return <section className="lineage-ui" id="video-lineage-workspace"><div className="workspace panel">
    <div role="tablist" aria-label="视频关联信息" className="tabs">{tabs.map(([key,label],index)=><button key={key} role="tab" id={`lineage-tab-${key}`} aria-selected={tab===key} aria-controls={`lineage-panel-${key}`} tabIndex={tab===key?0:-1} onClick={()=>setQuery({tab:key},false)} onKeyDown={event=>{
      let next=index;if(event.key==='ArrowRight')next=(index+1)%tabs.length;else if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=tabs.length-1;else return;
      event.preventDefault();setQuery({tab:tabs[next][0]},false);document.getElementById(`lineage-tab-${tabs[next][0]}`)?.focus();
    }} className="tab">{label}{key==='used'?<> <b>{counts?.reference_count??asset.reference_count??0}</b></>:key==='sources'&&counts?<> <b>{counts.source_count}</b></>:null}</button>)}</div>
    <div id={`lineage-panel-${tab}`} role="tabpanel" aria-labelledby={`lineage-tab-${tab}`} className="pane">
      {tab==='analytics'?analytics:<>
        <div className="section-head"><div><h2>{direction==='sources'?'这条视频的镜头来源':'使用了当前视频的成片'}</h2><p>{direction==='sources'?'按成片顺序查看原始素材, 点击片段可对照双方的时间位置.':`共 ${counts?.reference_count??asset.reference_count??0} 条直接引用成片, 同一成片的多个引用片段合并展示.`}</p></div>{direction==='sources'&&<div className="legend"><span><i className="dot"/>剪辑记录关联</span></div>}</div>
        {direction==='used'&&<div className="filters used-filters">
          <label>搜索成片<input type="search" value={q} placeholder="搜索成片名称或 ID" onChange={event=>setQuery({lineage_q:event.target.value})}/></label>
          <label>排序方式<select value={sort} onChange={event=>setQuery({lineage_sort:event.target.value})}><option value="recent">关联时间从新到旧</option><option value="old">关联时间从旧到新</option><option value="name">按名称排序</option></select></label>
          <button onClick={()=>setQuery({lineage_q:'',lineage_start:'',lineage_end:'',lineage_sort:'',spend_start:demoPeriod.start,spend_end:demoPeriod.end})}>重置</button>
          <label className="date-filter">关联时间<div className="date-inputs"><input aria-label="关联起始日期" type="date" value={start} onChange={event=>setQuery({lineage_start:event.target.value})}/><span>至</span><input aria-label="关联结束日期" type="date" value={end} onChange={event=>setQuery({lineage_end:event.target.value})}/></div></label>
          <label className="date-filter">消耗时间<div className="date-inputs"><input aria-label="消耗起始日期" type="date" value={spendStart} onChange={event=>setQuery({spend_start:event.target.value},false)}/><span>至</span><input aria-label="消耗结束日期" type="date" value={spendEnd} onChange={event=>setQuery({spend_end:event.target.value},false)}/></div></label>
        </div>}
        {(invalidDates||invalidSpend)&&direction==='used'?<p role="alert" className="range-error">开始日期不能晚于结束日期.</p>:loading?<div role="status" className="empty"><Loader2 size={18} className="mx-auto animate-spin"/><p>加载引用记录</p></div>:error?<div role="alert" className="empty"><p>{error}</p><button onClick={()=>setRetry(n=>n+1)}>重试</button></div>:<>
          {direction==='used'&&<section className="summary-section"><div className="section-head"><div><h3>关联成片数据汇总</h3><p>汇总当前筛选的 {data?.total??0} 条成片, 不受分页影响. 消耗时间影响汇总和卡片投放数据.</p></div><div className="summary-controls"><span className="demo">示例数据</span><button aria-label="向左查看更多指标" onClick={()=>summaryRef.current?.scrollBy({left:-450,behavior:'smooth'})}>←</button><button aria-label="向右查看更多指标" onClick={()=>summaryRef.current?.scrollBy({left:450,behavior:'smooth'})}>→</button></div></div><div ref={summaryRef} className="summary-scroll"><MetricCards values={reportMetrics(report)}/></div><p className="footnote">此处汇总引用后成片的整体表现, 不代表原素材或某个镜头独立产生的效果. 片段时长分摊估算在片段对照中单独展示.</p></section>}
          {!data?.items.length?<div className="empty"><p>{direction==='sources'?'暂无来源记录':q||start||end?'当前筛选条件下没有引用成片':'暂无引用成片'}</p>{direction==='sources'&&<small>历史或外部上传视频没有剪辑来源记录时, 不推断其来源.</small>}</div>:direction==='sources'?<>
            <div className="timeline" aria-label="成片镜头时间轴">{pieces.map(({relation,segment,index})=><button key={segment.id} aria-label={`${relation.source.filename}, ${range(segment.target_start,segment.target_end)}`} title={`${relation.source.filename} / ${displayRange(segment.target_start,segment.target_end)}`} onClick={()=>openDrawer(relation,index)} style={{left:`${segment.target_start/Math.max(asset.duration_seconds,.001)*100}%`,width:`${(segment.target_end-segment.target_start)/Math.max(asset.duration_seconds,.001)*100}%`}}>{relation.source.filename.replace(/\.[^.]+$/,'')}</button>)}</div><div className="ticks"><span>00:00</span><span>成片画面时间线</span><span>{displayTime(asset.duration_seconds)}</span></div>
            <table className="source-table"><thead><tr><th>来源素材</th><th>成片位置</th><th className="mobile-hide">原素材片段</th><th className="optional">使用时长</th><th/></tr></thead><tbody>{pieces.map(({relation,segment,index})=><tr key={segment.id}><td><div className="source-name"><LazyVideo className="thumb" src={`${mediaUrl(relation.source.object_url)}#t=${segment.source_start}`} muted playsInline/><div><RelatedVideoLink to={videoDetailPath(relation.source)} title={relation.source.filename}>{relation.source.filename}</RelatedVideoLink><p>{relation.source.asset_id} · 剪辑记录</p></div></div></td><td className="time">{displayRange(segment.target_start,segment.target_end)}</td><td className="time mobile-hide">{displayRange(segment.source_start,segment.source_end)}</td><td className="optional">{durationLabel(segment.target_end-segment.target_start)} 秒</td><td><button aria-label="对照片段" onClick={()=>openDrawer(relation,index)}>片段对照 ↗</button></td></tr>)}</tbody></table><div className="footnote">当前展示画面引用关系, 音频来源不计入镜头引用次数. 时间轴空白区间表示暂无来源记录.</div>
          </>:<><div className="grid">{data.items.map(relation=>{const d=demoReport(relation.target.id,spendStart,spendEnd);return <article key={relation.target.id} className="video-card"><div className="card-relation">↳ 关联于 {dateLabel(relation.created_at)}</div><div style={{position:'relative'}}><RelatedVideoLink className="card-cover" to={videoDetailPath(relation.target)} aria-label={`查看 ${relation.target.filename}`}><LazyVideo src={`${mediaUrl(relation.target.object_url)}#t=0.1`} muted playsInline/><span className="card-badge">成片</span><span className="card-id">{relation.target.asset_id}</span><span className="card-duration">{displayTime(relation.target.duration_seconds)}</span></RelatedVideoLink><RelatedVideoLink className="counter" to={`${videoDetailPath(relation.target)}?tab=used`}>引用次数 {relation.target.reference_count}</RelatedVideoLink></div><div className="card-body"><h3><RelatedVideoLink to={videoDetailPath(relation.target)} title={relation.target.filename}>{relation.target.filename}</RelatedVideoLink></h3><p>引用后成片 · <span>示例投放数据</span></p><div className="card-performance"><div><strong>{money(d.spend)}</strong><small>消耗</small></div><div><strong>{d.spend?(d.amount/d.spend).toFixed(2):'—'}</strong><small>ROI</small></div><div><strong>{money(d.amount)}</strong><small>成交金额</small></div></div><div className="card-bottom"><span>{relation.segments.length} 个引用片段</span><button aria-label="查看引用片段" onClick={()=>openDrawer(relation)}>查看引用片段 ↗</button></div></div></article>})}</div>
            <div className="paging"><span>共 {data.total} 条 · 每页 6 条</span><button aria-label="上一页" disabled={data.page<=1} onClick={()=>setQuery({lineage_page:String(data.page-1)},false)}><ChevronLeft size={14}/></button><span>{data.page} / {Math.max(1,Math.ceil(data.total/data.page_size))}</span><button aria-label="下一页" disabled={data.page*data.page_size>=data.total} onClick={()=>setQuery({lineage_page:String(data.page+1)},false)}><ChevronRight size={14}/></button></div>
          </>}
        </>}
      </>}
    </div></div>
    {drawer&&<SegmentDrawer relation={drawer.relation} initialIndex={drawer.index} direction={direction} onClose={()=>setDrawer(null)} onLocate={onLocate} spendStart={direction==='used'?spendStart:demoPeriod.start} spendEnd={direction==='used'?spendEnd:demoPeriod.end}/>}
  </section>;
}
