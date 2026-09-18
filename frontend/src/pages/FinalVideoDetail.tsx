import {useCallback,useEffect,useState} from 'react';
import {Loader2} from 'lucide-react';
import {Link,useParams} from 'react-router-dom';
import {productApi,videoLibraryApi,videoTaxonomyApi} from '../api/client';
import {VideoTaxonomyEditor} from '../components/VideoTaxonomyEditor';
import VideoLineage,{useSegmentPreview} from '../components/VideoLineage';
import VideoDetailOverview,{VideoDetailHeading} from '../components/VideoDetailOverview';
import VideoPerformanceAnalytics from '../components/VideoPerformanceAnalytics';
import type {Product,VideoLibraryAsset,VideoTaxonomyCatalog} from '../types';

export default function FinalVideoDetail(){
  const {id}=useParams();
  const assetId=Number(id);
  const preview=useSegmentPreview(assetId);
  const {videoRef}=preview;
  const [video,setVideo]=useState<VideoLibraryAsset|null>(null);
  const [product,setProduct]=useState<Product|null>(null);
  const [catalog,setCatalog]=useState<VideoTaxonomyCatalog|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [editingTaxonomy,setEditingTaxonomy]=useState(false);
  const [currentSecond,setCurrentSecond]=useState(0);
  const [summary,setSummary]=useState<{source_count:number;reference_count:number}>();

  const refreshVideo=useCallback(async()=>{
    if(!Number.isInteger(assetId)||assetId<=0)throw new Error('not-found');
    const selected=await videoLibraryApi.get(assetId);
    if(selected.asset_kind!=='final_output')throw new Error('not-found');
    setVideo(selected);
  },[assetId]);

  const refreshCatalog=useCallback(async()=>{
    setCatalog(await videoTaxonomyApi.catalog('final_output'));
  },[]);

  useEffect(()=>{let active=true;setLoading(true);setError('');setVideo(null);setSummary(undefined);setCurrentSecond(0);(async()=>{try{
    if(!Number.isInteger(assetId)||assetId<=0)throw new Error('not-found');
    const [selected,nextCatalog]=await Promise.all([videoLibraryApi.get(assetId),videoTaxonomyApi.catalog('final_output')]);
    if(selected.asset_kind!=='final_output')throw new Error('not-found');
    const selectedProduct=await productApi.get(selected.product_id);
    if(active){setVideo(selected);setProduct(selectedProduct);setCatalog(nextCatalog)}
  }catch{if(active)setError('没有找到这条成片')}finally{if(active)setLoading(false)}})();return()=>{active=false}},[assetId]);

  if(loading)return <div className="grid min-h-80 place-items-center"><Loader2 className="animate-spin"/></div>;
  if(error||!video)return <div className="panel p-10 text-center"><p>{error}</p><Link className="btn-secondary mt-5" to="/final-videos">返回成片库</Link></div>;
  const seek=(second:number)=>{if(videoRef.current){videoRef.current.pause();videoRef.current.currentTime=second}setCurrentSecond(second)};
  return <><div className="lineage-ui lineage-detail">
    <VideoDetailHeading title={product?.name||'成片详情'} asset={video} fallback="/final-videos" backLabel="返回成片库" sourceCount={summary?.source_count}/>
    <VideoDetailOverview key={video.id} asset={video} productName={product?.name||'未命名商品'} preview={preview} onTimeChange={setCurrentSecond} onEdit={()=>setEditingTaxonomy(true)} referenceCount={summary?.reference_count}/>
    <VideoLineage key={video.id} asset={video} onLocate={preview.locate} onSummary={setSummary} analytics={<VideoPerformanceAnalytics asset={video} currentSecond={currentSecond} onSeek={seek}/>}/>
    <div className="bottom-note"><span>DIROVO / 视频资产与引用关系</span><span>引用关系为实际记录, 投放表现为示例数据.</span></div>
  </div>
    {editingTaxonomy&&catalog&&<VideoTaxonomyEditor asset={video} catalog={catalog} objectType="final_output" onClose={()=>setEditingTaxonomy(false)} onSaved={refreshVideo} onCatalogRefresh={refreshCatalog}/>}
  </>;
}
