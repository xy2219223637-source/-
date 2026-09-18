import {useCallback,useEffect,useState} from 'react';
import {Download,Loader2,Send} from 'lucide-react';
import {Link,useParams} from 'react-router-dom';
import {mediaUrl,productApi,videoLibraryApi,videoTaxonomyApi} from '../api/client';
import {VideoTaxonomyEditor} from '../components/VideoTaxonomyEditor';
import VideoLineage,{useSegmentPreview} from '../components/VideoLineage';
import VideoDetailOverview,{VideoDetailHeading} from '../components/VideoDetailOverview';
import VideoPerformanceAnalytics from '../components/VideoPerformanceAnalytics';
import QianchuanReportPanel from '../components/QianchuanReportPanel';
import QianchuanUploadModal from '../components/QianchuanUploadModal';
import QianchuanUploadHistory,{useQianchuanUploads,uploadStatus} from '../components/QianchuanUploadHistory';
import type {Product,VideoLibraryAsset,VideoTaxonomyCatalog} from '../types';

export default function FinalVideoDetail(){
  const {id}=useParams();
  const assetId=Number(id);
  const preview=useSegmentPreview(assetId);
  const {videoRef}=preview;
  const [video,setVideo]=useState<VideoLibraryAsset|null>(null);
  const [product,setProduct]=useState<Product|null>(null);
  const [catalog,setCatalog]=useState<VideoTaxonomyCatalog|null>(null);
  const [editingTaxonomy,setEditingTaxonomy]=useState(false);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [currentSecond,setCurrentSecond]=useState(0);
  const [summary,setSummary]=useState<{source_count:number;reference_count:number}>();
  const [pushNotice,setPushNotice]=useState(false);
  const [uploadRevision,setUploadRevision]=useState(0);
  const {uploads,error:uploadError}=useQianchuanUploads(assetId,uploadRevision);
  const [reportUploadId,setReportUploadId]=useState('');
  const latestUpload=uploads[0];
  const reportUploads=uploads.filter(item=>item.status==='succeeded'&&!!item.material_id);
  const reportUpload=reportUploads.find(item=>item.id===reportUploadId)||reportUploads[0];

  const refreshVideo=useCallback(async()=>{
    if(!Number.isInteger(assetId)||assetId<=0)throw new Error('not-found');
    const selected=await videoLibraryApi.get(assetId);
    if(selected.asset_kind!=='final_output')throw new Error('not-found');
    setVideo(selected);
  },[assetId]);

  const refreshCatalog=useCallback(async()=>{
    setCatalog(await videoTaxonomyApi.catalog('final_output'));
  },[]);

  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');setVideo(null);setProduct(null);setCatalog(null);
    setSummary(undefined);setCurrentSecond(0);setEditingTaxonomy(false);setPushNotice(false);setReportUploadId('');
    (async()=>{try{
      if(!Number.isInteger(assetId)||assetId<=0)throw new Error('not-found');
      const [selected,nextCatalog]=await Promise.all([videoLibraryApi.get(assetId),videoTaxonomyApi.catalog('final_output')]);
      if(selected.asset_kind!=='final_output')throw new Error('not-found');
      const selectedProduct=await productApi.get(selected.product_id);
      if(active){setVideo(selected);setProduct(selectedProduct);setCatalog(nextCatalog)}
    }catch{if(active)setError('没有找到这条成片')}finally{if(active)setLoading(false)}})();
    return()=>{active=false};
  },[assetId]);

  if(loading)return <div className="grid min-h-80 place-items-center"><Loader2 className="animate-spin"/></div>;
  if(error||!video)return <div className="panel p-10 text-center"><p>{error}</p><Link className="btn-secondary mt-5" to="/final-videos">返回成片库</Link></div>;

  const seek=(second:number)=>{if(videoRef.current){videoRef.current.pause();videoRef.current.currentTime=second}setCurrentSecond(second)};
  return <>
    <div className="lineage-ui lineage-detail">
      <VideoDetailHeading title={product?.name||'成片详情'} asset={video} fallback="/final-videos" backLabel="返回成片库" sourceCount={summary?.source_count}/>
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4">
        <div className="text-sm text-[var(--muted)]">推送状态：{uploadError?'上传状态读取失败':uploadStatus(latestUpload)}{reportUpload?.material_id&&` · 千川素材 ID：${reportUpload.material_id}`}</div>
        <div className="flex gap-2"><a className="btn-secondary" href={mediaUrl(video.object_url)} download><Download size={15}/>下载视频</a><button className="primary inline-flex items-center gap-2" onClick={()=>setPushNotice(true)}><Send size={15}/>推送到千川</button></div>
      </div>
      <VideoDetailOverview key={video.id} asset={video} productName={product?.name||'未命名商品'} preview={preview} onTimeChange={setCurrentSecond} onEdit={()=>setEditingTaxonomy(true)} referenceCount={summary?.reference_count}/>
      <QianchuanUploadHistory uploads={uploads} error={uploadError} reload={()=>setUploadRevision(value=>value+1)}/>
      <VideoLineage key={video.id} asset={video} onLocate={preview.locate} onSummary={setSummary} analytics={<VideoPerformanceAnalytics asset={video} currentSecond={currentSecond} onSeek={seek}/>}/>
      {reportUploads.length>1&&<label className="block text-sm">查询投放账户<select aria-label="查询投放账户" className="input-select mt-2 w-full" value={reportUpload?.id||''} onChange={event=>setReportUploadId(event.target.value)}>{reportUploads.map(item=><option key={item.id} value={item.id}>{item.advertiser_name||'千川账户'} · {item.advertiser_id} · 素材 {item.material_id}</option>)}</select></label>}
      <QianchuanReportPanel key={reportUpload?.id||'no-upload'} upload={reportUpload}/>
      <div className="bottom-note"><span>DIROVO / 视频资产与引用关系</span><span>引用关系为实际记录，投放分析为示例数据；千川素材数据需手动查询。</span></div>
    </div>
    {pushNotice&&<QianchuanUploadModal videoAssetId={video.id} filename={video.filename} onClose={()=>setPushNotice(false)} onUploaded={()=>setUploadRevision(value=>value+1)}/>}
    {editingTaxonomy&&catalog&&<VideoTaxonomyEditor asset={video} catalog={catalog} objectType="final_output" onClose={()=>setEditingTaxonomy(false)} onSaved={refreshVideo} onCatalogRefresh={refreshCatalog}/>}
  </>;
}
