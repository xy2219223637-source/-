import {useCallback,useEffect,useState} from 'react';
import {Download,Loader2,Rocket,Send} from 'lucide-react';
import {Link,useParams} from 'react-router-dom';
import {mediaUrl,productApi,videoLibraryApi,videoTaxonomyApi} from '../api/client';
import {VideoTaxonomyEditor} from '../components/VideoTaxonomyEditor';
import VideoLineage,{useSegmentPreview} from '../components/VideoLineage';
import VideoDetailOverview,{VideoDetailHeading} from '../components/VideoDetailOverview';
import QianchuanAnalysis from '../components/QianchuanAnalysis';
import QianchuanUploadModal from '../components/QianchuanUploadModal';
import QianchuanPlanModal from '../components/QianchuanPlanModal';
import type {QianchuanDemoPlan} from '../components/QianchuanPlanDemo';
import {useQianchuanUploads} from '../components/QianchuanUploadHistory';
import type {Product,VideoLibraryAsset,VideoTaxonomyCatalog} from '../types';

export default function FinalVideoDetail(){
  const {id}=useParams();
  const assetId=Number(id);
  const preview=useSegmentPreview(assetId);
  const [video,setVideo]=useState<VideoLibraryAsset|null>(null);
  const [product,setProduct]=useState<Product|null>(null);
  const [catalog,setCatalog]=useState<VideoTaxonomyCatalog|null>(null);
  const [editingTaxonomy,setEditingTaxonomy]=useState(false);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [summary,setSummary]=useState<{source_count:number;reference_count:number}>();
  const [pushNotice,setPushNotice]=useState(false);
  const [planOpen,setPlanOpen]=useState(false);
  const [mockPlans,setMockPlans]=useState<QianchuanDemoPlan[]>([]);
  const [uploadRevision,setUploadRevision]=useState(0);
  const {uploads,error:uploadError}=useQianchuanUploads(assetId,uploadRevision);

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
    setSummary(undefined);setEditingTaxonomy(false);setPushNotice(false);setPlanOpen(false);setMockPlans([]);
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

  const planCompleted=(plan:QianchuanDemoPlan)=>setMockPlans(current=>[plan,...current.filter(item=>item.id!==plan.id)]);

  return <>
    <div className="lineage-ui lineage-detail">
      <VideoDetailHeading title={product?.name||'成片详情'} asset={video} fallback="/final-videos" backLabel="返回成片库" sourceCount={summary?.source_count}/>
      <div className="flex flex-wrap items-center justify-end gap-3 pb-4">
        <div className="flex flex-wrap gap-2"><a className="btn-secondary" href={mediaUrl(video.object_url)} download><Download size={15}/>下载视频</a>
          <button className="btn-secondary" onClick={()=>setPushNotice(true)}><Send size={15}/>上传素材</button>
          <button className="primary inline-flex items-center gap-2" onClick={()=>setPlanOpen(true)}><Rocket size={15}/>推送投流</button></div>
      </div>
      <VideoDetailOverview key={video.id} asset={video} productName={product?.name||'未命名商品'} preview={preview} onEdit={()=>setEditingTaxonomy(true)} referenceCount={summary?.reference_count}/>
      <VideoLineage key={video.id} asset={video} onLocate={preview.locate} onSummary={setSummary} analytics={<QianchuanAnalysis uploads={uploads} error={uploadError} reload={()=>setUploadRevision(value=>value+1)} onManage={()=>setPushNotice(true)} onCreatePlan={()=>setPlanOpen(true)} plans={mockPlans}/>}/>
      <div className="bottom-note"><span>DIROVO / 视频资产与引用关系</span><span>千川素材数据需手动查询；被引用成片的投放估算仍为示例数据。</span></div>
    </div>
    {pushNotice&&<QianchuanUploadModal videoAssetId={video.id} filename={video.filename} onClose={()=>setPushNotice(false)} onUploaded={()=>setUploadRevision(value=>value+1)}/>}
    {planOpen&&<QianchuanPlanModal uploads={uploads} onClose={()=>setPlanOpen(false)} onPlanCompleted={planCompleted}/>}
    {editingTaxonomy&&catalog&&<VideoTaxonomyEditor asset={video} catalog={catalog} objectType="final_output" onClose={()=>setEditingTaxonomy(false)} onSaved={refreshVideo} onCatalogRefresh={refreshCatalog}/>}
  </>;
}
