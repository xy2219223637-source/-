import {useEffect,useRef,useState} from 'react';
import {mediaUrl} from '../api/client';
import type {VideoLineageAsset} from '../types';
import {MetricCards,timecode} from './VideoLineage';
import {demoReport,money,num,pct} from './videoPerformanceDemo';

const defs={click:['点击数','#6d5dfc'],follow:['关注数','#168a5b'],comment:['评论数','#d97706'],like:['点赞数','#db2777'],lose:['流失数','#e05252']} as const;
type Key=keyof typeof defs;
const point=(second:number,f:number)=>({second,click:Math.round((22+Math.sin(second*.7)*8+(second<4?28:0)+(second===17?22:0))*f),follow:Math.round((3+Math.max(0,Math.sin(second*.5))*4)*f),comment:Math.round((2+Math.max(0,Math.cos(second*.8))*3)*f),like:Math.round((9+Math.max(0,Math.sin(second*.55))*11+(second===8?12:0))*f),lose:Math.round((12+second*.7+(second===4?24:0)+(second===21?18:0))*f)});
export default function VideoPerformanceAnalytics({asset,currentSecond,onSeek}:{asset:VideoLineageAsset;currentSecond:number;onSeek:(n:number)=>void}){
  const [tab,setTab]=useState('overview'),[days,setDays]=useState(7),[primary,setPrimary]=useState<Key>('click'),[secondary,setSecondary]=useState<Key>('lose'),[hover,setHover]=useState<number|null>(null);
  const preview=useRef<HTMLVideoElement>(null);
  useEffect(()=>{if(preview.current&&Math.abs(preview.current.currentTime-currentSecond)>.15)preview.current.currentTime=currentSecond},[currentSecond]);
  const d=demoReport(asset.id,`2026-09-${String(Math.max(1,18-days)).padStart(2,'0')}`,'2026-09-17');
  const last=Math.max(1,Math.floor(asset.duration_seconds)),points=Array.from({length:last+1},(_,n)=>point(n,days===30?1.7:1));
  const second=Math.max(0,Math.min(last,Math.round(hover??currentSecond))),p=points[second];
  const x=(n:number)=>52+n/last*651,max=(k:Key)=>Math.max(10,Math.ceil(Math.max(...points.map(p=>p[k]))/10)*10),y=(v:number,m:number)=>225-v/m*193,m1=max(primary),m2=max(secondary);
  const pointer=(event:React.MouseEvent<SVGSVGElement>,seek=false)=>{const matrix=event.currentTarget.getScreenCTM();if(!matrix)return;const point=event.currentTarget.createSVGPoint();point.x=event.clientX;point.y=event.clientY;const n=Math.round(Math.max(0,Math.min(last,(point.matrixTransform(matrix.inverse()).x-52)/651*last)));if(seek){setHover(null);onSeek(n)}else setHover(n)};
  return <><div className="section-head"><div><h2>投放分析</h2><p>集中查看投放与互动表现.</p></div><div className="analysis-controls"><span className="demo">示例数据 · 尚未接入千川</span><select aria-label="投放分析时间范围" value={days} onChange={e=>setDays(Number(e.target.value))}><option value={7}>近 7 天</option><option value={30}>近 30 天</option></select></div></div>
    <div className="analytics-tabs">{[['overview','投放概览'],['interaction','互动表现'],['seconds','逐秒分析']].map(([key,label])=><button key={key} className={tab===key?'selected':''} aria-pressed={tab===key} onClick={()=>{setTab(key);setHover(null)}}>{label}</button>)}</div>
    {tab==='overview'?<><div className="metrics"><MetricCards values={[
      ['消耗',money(d.spend)],['成交金额',money(d.amount)],['成交订单',num(d.orders)],['支付 ROI',d.spend?(d.amount/d.spend).toFixed(2):'—'],['展示次数',num(d.impressions)],['点击次数',num(d.clicks)],['点击率',pct(d.clicks,d.impressions)],['千次展现费用',d.impressions?money(d.spend/d.impressions*1000):'—'],
    ]}/></div><div className="section-head quality-head"><h3>视频播放质量</h3><small>数据更新时间: 2026-09-17 09:00 · 演示</small></div><div className="metrics quality-metrics"><MetricCards values={[
      ['播放数',num(d.plays)],['3 秒播放率',pct(d.three,d.plays)],['有效播放率',pct(d.effective,d.plays)],['完播率',pct(d.complete,d.plays)],['平均播放时长',d.plays?(d.playSeconds/d.plays).toFixed(1)+' 秒':'—'],
    ]}/></div></>:tab==='interaction'?<><div className="metrics"><MetricCards values={[["点赞",num(d.likes)],["评论",num(d.comments)],["分享",num(d.shares)],["新增关注",num(d.follows)]]}/></div><div className="footnote">当前所选时间范围内的互动汇总 · 示例数据.</div></>:<>
      <div className="section-head"><div><h3>逐秒互动与流失</h3><p>移动鼠标查看每秒数据, 点击曲线定位视频. 展示事件计数, 不是点击率或流失率.</p></div></div><div className="seconds-layout"><div className="seconds-preview"><video ref={preview} controls muted playsInline src={mediaUrl(asset.object_url)} onLoadedMetadata={e=>{e.currentTarget.currentTime=currentSecond}} onTimeUpdate={e=>{if(!e.currentTarget.paused)onSeek(e.currentTarget.currentTime)}}/><div className="seconds-position">当前位置 {timecode(currentSecond).slice(0,5)}</div><input type="range" aria-label="逐秒分析进度" min="0" max={asset.duration_seconds} step="0.1" value={Math.min(currentSecond,asset.duration_seconds)} onChange={e=>onSeek(Number(e.target.value))}/></div><div className="seconds-data"><div className="metric-selects">{[[primary,setPrimary,'指标一'],[secondary,setSecondary,'指标二']].map(([value,setter,label])=><label key={String(label)}>{String(label)}<select value={String(value)} onChange={e=>(setter as (k:Key)=>void)(e.target.value as Key)}>{Object.entries(defs).map(([key,[text]])=><option key={key} value={key}>{text}</option>)}</select></label>)}</div><div className="chart-scroll"><svg className="seconds-chart" viewBox="0 0 760 290" role="img" aria-label="逐秒互动与流失双轴曲线" onMouseMove={e=>pointer(e)} onMouseLeave={()=>setHover(null)} onClick={e=>pointer(e,true)}>
        {[0,1,2,3,4].map(i=><g key={i}><line x1={52} y1={225-i/4*193} x2={703} y2={225-i/4*193} stroke="#e9e9e5"/><text x={42} y={229-i/4*193} textAnchor="end">{Math.round(m1*i/4)}</text><text x={713} y={229-i/4*193}>{Math.round(m2*i/4)}</text></g>)}
        {[0,1,2,3,4,5,6].map(i=><text key={i} x={x(last*i/6)} y={245} textAnchor="middle">{Math.round(last*i/6)}s</text>)}
        {([primary,secondary] as Key[]).map((k,i)=><path key={i} d={points.map((p,j)=>`${j?'L':'M'}${x(j)} ${y(p[k],i===0?m1:m2)}`).join(' ')} fill="none" stroke={defs[k][1]} strokeWidth={2.5}/>)}
        <text x={52} y={16} style={{fill:defs[primary][1]}}>{defs[primary][0]}</text><text x={703} y={16} textAnchor="end" style={{fill:defs[secondary][1]}}>{defs[secondary][0]}</text>
        <line x1={x(second)} y1={32} x2={x(second)} y2={225} stroke="#777" strokeDasharray="5 4"/><line x1={52} y1={y(p[primary],m1)} x2={703} y2={y(p[primary],m1)} stroke={defs[primary][1]} strokeDasharray="5 4" opacity={.5}/>
        {([primary,secondary] as Key[]).map((k,i)=><circle key={i} cx={x(second)} cy={y(p[k],i===0?m1:m2)} r={4} fill="white" stroke={defs[k][1]} strokeWidth={2}/>)}
        <g transform={`translate(${x(second)>490?x(second)-167:x(second)+12},40)`} style={{pointerEvents:'none'}}><rect width={154} height={126} rx={4} fill="white" stroke="#d8d8d4"/><text x={12} y={20} style={{fill:'#222',fontWeight:600}}>视频第 {second} 秒</text>{Object.entries(defs).map(([key,[label,color]],i)=><g key={key}><circle cx={13} cy={38+i*18} r={3} fill={color}/><text x={24} y={42+i*18}>{label}</text><text x={140} y={42+i*18} textAnchor="end">{p[key as Key]}</text></g>)}</g>
        <g transform={`translate(${Math.min(660,Math.max(52,x(second)-25))},258)`}><rect width={50} height={21} rx={3} fill="#505754"/><text x={25} y={14} textAnchor="middle" style={{fill:'white'}}>{second}s</text></g>
      </svg></div><div className="chart-legend"><span style={{color:defs[primary][1]}}>● {defs[primary][0]} (左轴)</span><span style={{color:defs[secondary][1]}}>● {defs[secondary][0]} (右轴)</span></div><div className="second-values" aria-live="polite">第 {second} 秒 · {defs[primary][0]} {p[primary]} · {defs[secondary][0]} {p[secondary]}</div><div className="footnote">正式接入后按千川素材 ID, 广告账户和日期范围查询. 逐秒数据为 T+1, 次日 9 点后稳定. 当前演示使用生成的示例曲线.</div></div></div>
    </>}
  </>;
}
