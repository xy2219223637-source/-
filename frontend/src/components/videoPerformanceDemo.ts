// Replace this adapter with account/date-scoped reporting when the API is ready.
export const demoPeriod={start:'2026-09-11',end:'2026-09-17'};
export const money=(n:number)=>'¥'+n.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
export const num=(n:number)=>Math.round(n).toLocaleString('zh-CN');
export const pct=(a:number,b:number)=>b?`${(a/b*100).toFixed(2)}%`:'—';
export const emptyReport=()=>({spend:0,amount:0,coupon:0,subsidy:0,orders:0,impressions:0,clicks:0,plays:0,complete:0,three:0,effective:0,playSeconds:0,likes:0,comments:0,shares:0,follows:0});
export type PerformanceReport=ReturnType<typeof emptyReport>;
export function demoReport(id:number,start='',end=''){
  const i=id%17+1,total=emptyReport();
  for(let d=1;d<=17;d++){
    const date=`2026-09-${String(d).padStart(2,'0')}`;if((start&&date<start)||(end&&date>end))continue;
    const f=1+d%5*.12,spend=(92+i*23)*f,impressions=Math.round((8400+i*670)*f),plays=Math.round(impressions*.81);
    total.spend+=spend;total.amount+=spend*(2.3+i*.11);total.coupon+=spend*.09;total.subsidy+=spend*.04;
    total.orders+=Math.round(8+i*.8+d%4);total.impressions+=impressions;total.clicks+=Math.round(impressions*.0517);
    total.plays+=plays;total.complete+=Math.round(plays*.192);total.three+=Math.round(plays*.724);total.effective+=Math.round(plays*.486);total.playSeconds+=plays*8.4;
    total.likes+=Math.round(150*f+i*3);total.comments+=Math.round(12*f+i);total.shares+=Math.round(6*f);total.follows+=Math.round(28*f);
  }
  return total;
}
export function aggregateReports(ids:number[],start:string,end:string){
  const sum=emptyReport();for(const id of new Set(ids)){const report=demoReport(id,start,end);for(const key of Object.keys(sum) as (keyof PerformanceReport)[])sum[key]+=report[key]}return sum;
}
export function reportMetrics(d:PerformanceReport):[string,string][]{return [
  ['消耗',money(d.spend)],['ROI',d.spend?(d.amount/d.spend).toFixed(2):'—'],['成交金额',money(d.amount)],['智能优惠券',money(d.coupon)],['总成交金额',money(d.amount+d.coupon+d.subsidy)],['平台补贴金额',money(d.subsidy)],['转化数',num(d.orders)],['转化率',pct(d.orders,d.clicks)],['转化成本',d.orders?money(d.spend/d.orders):'—'],['展示数',num(d.impressions)],['平均千次展现费用',d.impressions?money(d.spend/d.impressions*1000):'—'],['点击数',num(d.clicks)],['点击率',pct(d.clicks,d.impressions)],['平均点击单价',d.clicks?money(d.spend/d.clicks):'—'],['播放量',num(d.plays)],['完播率',pct(d.complete,d.plays)],
]}
