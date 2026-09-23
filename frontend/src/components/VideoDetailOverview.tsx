import {useState} from 'react';
import {useLocation,useSearchParams} from 'react-router-dom';
import {mediaUrl} from '../api/client';
import type {VideoLibraryAsset} from '../types';
import {LineageBack,timecode,useSegmentPreview} from './VideoLineage';

export function VideoDetailHeading({title,asset,fallback,backLabel,sourceCount}:{title:string;asset:VideoLibraryAsset;fallback:string;backLabel:string;sourceCount?:number}){
  return <><div className="topline"><LineageBack fallback={fallback} label={backLabel}/></div><header className="heading"><div><h1>{title}</h1><p>{asset.filename}</p></div>{asset.asset_kind==='final_output'&&<div className="actions"><span className={`badge ${sourceCount?'verified':''}`}>{sourceCount===undefined?'来源记录加载中':sourceCount?'已建立来源关系':'暂无来源记录'}</span></div>}</header></>;
}

export default function VideoDetailOverview({asset,productName,preview,onEdit,referenceCount}:{asset:VideoLibraryAsset;productName:string;preview:ReturnType<typeof useSegmentPreview>;onEdit:()=>void;referenceCount?:number}){
  const [playing,setPlaying]=useState(false),[position,setPosition]=useState(0),[error,setError]=useState('');
  const [,setParams]=useSearchParams(),location=useLocation();
  const fmt=(n:number)=>timecode(n).slice(0,5);
  const openUsed=()=>{setParams(p=>{const next=new URLSearchParams(p);next.set('tab','used');return next},{replace:true,state:location.state});document.getElementById('video-lineage-workspace')?.scrollIntoView({behavior:'smooth',block:'start'})};
  const tags=[...(asset.category?[`${asset.category.path.join(' / ')}${asset.category.status==='inactive'?'（已停用）':''}`]:[]),...asset.public_tags.map(t=>`${t.name}${t.status==='inactive'?'（已停用）':''}`),...asset.personal_tags.map(t=>`${t.name}${t.status==='inactive'?'（已停用）':''}`)];
  return <section className="overview panel"><div className="preview"><span className="preview-label">画面预览</span><video key={asset.id} ref={preview.videoRef} playsInline tabIndex={0} aria-label="详情视频" src={mediaUrl(asset.object_url)}
    onLoadedMetadata={preview.onLoadedMetadata} onSeeking={preview.onSeeking} onTimeUpdate={e=>{preview.onTimeUpdate(e);setPosition(e.currentTarget.currentTime)}}
    onPlay={()=>setPlaying(true)} onPause={()=>setPlaying(false)} onError={()=>setError('视频暂时无法播放')}/>
    <button className={`play ${playing?'playing':''}`} aria-label={playing?'暂停视频':'播放视频'} disabled={!!error} onClick={()=>{const v=preview.videoRef.current;if(v){if(v.paused)void v.play().catch(()=>setError('视频暂时无法播放'));else v.pause()}}}>{playing?'Ⅱ':'▶'}</button>
    <div className="playerbar"><span>{fmt(position)}</span><input type="range" aria-label="视频播放进度" min="0" max={asset.duration_seconds} step="0.01" value={Math.min(position,asset.duration_seconds)} onChange={e=>{const n=Number(e.target.value);if(preview.videoRef.current)preview.videoRef.current.currentTime=n;setPosition(n)}}/><span>{fmt(asset.duration_seconds)}</span></div>{error&&<p role="alert" className="preview-error">{error}</p>}
    </div><div className="metadata"><div className="meta-top"><h3>视频信息</h3><span className="badge">{asset.asset_kind==='final_output'?'本地成片':'原始素材'}</span></div><dl className="kv"><dt>商品</dt><dd>{productName}</dd><dt>视频 ID</dt><dd>{asset.asset_id}</dd><dt>画面规格</dt><dd>{asset.width} × {asset.height} · {asset.height>asset.width?'竖版':asset.height===asset.width?'方形':'横版'}</dd><dt>创建时间</dt><dd>{new Date(asset.created_at).toLocaleString('zh-CN',{hour12:false})}</dd><dt>引用次数</dt><dd><button className="count-link" aria-label={`引用次数 ${referenceCount??asset.reference_count??0}`} onClick={openUsed}>{referenceCount??asset.reference_count??0}</button></dd></dl>
      <div className="tags-row"><span className="muted">分类与标签</span>{tags.length?tags.map((tag,i)=><span className="tag" key={`${tag}-${i}`}>{tag}</span>):<span className="muted">暂未设置</span>}<button className="text-button" onClick={onEdit}>编辑</button></div>
    </div></section>;
}
