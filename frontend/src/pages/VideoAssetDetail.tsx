import {useCallback,useEffect,useState} from 'react';
import {Loader2} from 'lucide-react';
import {Link,useLocation,useParams} from 'react-router-dom';
import {productApi,videoLibraryApi,videoTaxonomyApi} from '../api/client';
import {VideoTaxonomyEditor} from '../components/VideoTaxonomyEditor';
import VideoLineage,{useSegmentPreview} from '../components/VideoLineage';
import VideoDetailOverview,{VideoDetailHeading} from '../components/VideoDetailOverview';
import type {Product,VideoLibraryAsset,VideoTaxonomyCatalog} from '../types';

type LibraryType='semi_finished'|'finished';
type Props={libraryType:LibraryType;title:string};

export default function VideoAssetDetail({libraryType,title}:Props){
  const {id}=useParams();
  const location=useLocation();
  const assetId=Number(id);
  const preview=useSegmentPreview(assetId);
  const libraryPath=libraryType==='finished'?'/finished-video-assets':'/semi-finished-assets';
  const libraryReturn=location.state?.libraryReturn;
  const returnPath=typeof libraryReturn?.url==='string'&&libraryReturn.url.startsWith(libraryPath)?libraryReturn.url:libraryPath;
  const [asset,setAsset]=useState<VideoLibraryAsset|null>(null);
  const [product,setProduct]=useState<Product|null>(null);
  const [catalog,setCatalog]=useState<VideoTaxonomyCatalog|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [editing,setEditing]=useState(false);
  const [summary,setSummary]=useState<{source_count:number;reference_count:number}>();

  const refreshAsset=useCallback(async()=>{
    if(!Number.isInteger(assetId)||assetId<=0)throw new Error('not-found');
    const next=await videoLibraryApi.get(assetId);
    if(next.asset_kind!=='material'||next.library_type!==libraryType)throw new Error('not-found');
    setAsset(next);
  },[assetId,libraryType]);

  const refreshCatalog=useCallback(async()=>{
    setCatalog(await videoTaxonomyApi.catalog('material'));
  },[]);

  useEffect(()=>{
    let active=true;
    setLoading(true);setError('');setSummary(undefined);
    if(!Number.isInteger(assetId)||assetId<=0){
      setError('没有找到这条素材');
      setLoading(false);
      return()=>{active=false};
    }
    Promise.all([videoLibraryApi.get(assetId),videoTaxonomyApi.catalog('material')]).then(async([nextAsset,nextCatalog])=>{
      if(nextAsset.asset_kind!=='material'||nextAsset.library_type!==libraryType)throw new Error('not-found');
      const nextProduct=await productApi.get(nextAsset.product_id);
      if(active){setAsset(nextAsset);setCatalog(nextCatalog);setProduct(nextProduct)}
    }).catch(()=>{if(active)setError('没有找到这条素材')}).finally(()=>{if(active)setLoading(false)});
    return()=>{active=false};
  },[assetId,libraryType]);

  if(loading)return <div className="grid min-h-80 place-items-center text-sm text-[var(--muted)]"><div className="flex items-center gap-2"><Loader2 size={17} className="animate-spin"/>素材加载中</div></div>;
  if(error||!asset)return <div className="panel p-10 text-center"><p>{error||'没有找到这条素材'}</p><Link className="btn-secondary mt-5" to={returnPath}>返回{title}</Link></div>;

  return <><div className="lineage-ui lineage-detail">
    <VideoDetailHeading title={product?.name||title} asset={asset} fallback={returnPath} backLabel={`返回${title}`}/>
    <VideoDetailOverview key={asset.id} asset={asset} productName={product?.name||'未命名商品'} preview={preview} onEdit={()=>setEditing(true)} referenceCount={summary?.reference_count}/>
    <VideoLineage key={asset.id} asset={asset} onLocate={preview.locate} onSummary={setSummary}/>
    <div className="bottom-note"><span>DIROVO / 视频资产与引用关系</span><span>引用关系为实际记录, 投放表现为示例数据.</span></div>
  </div>
    {editing&&catalog&&<VideoTaxonomyEditor asset={asset} catalog={catalog} objectType="material" onClose={()=>setEditing(false)} onSaved={refreshAsset} onCatalogRefresh={refreshCatalog}/>}
  </>;
}
