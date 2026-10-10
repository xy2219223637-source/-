import {useEffect,useRef,useState} from 'react';
import {Loader2,RefreshCw,Search} from 'lucide-react';
import {
  qianchuanApi,
  type QianchuanAccount,
  type QianchuanAwemeOption,
  type QianchuanPlanCapabilities,
  type QianchuanProductOption,
  type QianchuanUpload,
} from '../api/client';
import {qianchuanError} from './QianchuanUploadModal';
import QianchuanPlanDemo from './QianchuanPlanDemo';
import type {QianchuanDemoPlan} from './QianchuanPlanDemo';

type RootState='checking'|'hidden'|'ready';
const DEMO_ENABLED=import.meta.env.DEV||import.meta.env.VITE_QIANCHUAN_PLAN_DEMO_ENABLED==='true';

export default function QianchuanPlanCatalog({uploads=[],onPlanCompleted}:{
  uploads?:QianchuanUpload[]; onPlanCompleted?:(plan:QianchuanDemoPlan)=>void;
}){
  const [rootState,setRootState]=useState<RootState>('checking');
  const [accounts,setAccounts]=useState<QianchuanAccount[]>([]);
  const [bindingId,setBindingId]=useState('');
  const [capabilities,setCapabilities]=useState<QianchuanPlanCapabilities|null>(null);
  const [awemes,setAwemes]=useState<QianchuanAwemeOption[]>([]);
  const [awemeId,setAwemeId]=useState('');
  const [products,setProducts]=useState<QianchuanProductOption[]>([]);
  const [productId,setProductId]=useState('');
  const [productCursor,setProductCursor]=useState<number|null>(null);
  const [hasMoreProducts,setHasMoreProducts]=useState(false);
  const [awemeKeyword,setAwemeKeyword]=useState('');
  const [productKeyword,setProductKeyword]=useState('');
  const [loadingAwemes,setLoadingAwemes]=useState(false);
  const [loadingProducts,setLoadingProducts]=useState(false);
  const [error,setError]=useState('');
  const [catalogMock,setCatalogMock]=useState(false);
  const awemeRequest=useRef(0);
  const productRequest=useRef(0);

  useEffect(()=>{
    let active=true;
    (async()=>{
      try{
        const gate=await qianchuanApi.planCapabilities();
        if(!active)return;
        if(!gate.enabled){setRootState('hidden');return;}
        const result=await qianchuanApi.accounts();
        if(!active)return;
        const rows=[...new Map(result.connections
          .filter(connection=>connection.status==='active')
          .flatMap(connection=>connection.accounts)
          .map(account=>[account.id,account])).values()];
        setAccounts(rows);
        setBindingId(rows[0]?.id||'');
        setRootState('ready');
      }catch{
        // This sidecar must not break the existing upload/report page when disabled,
        // unavailable, or deployed behind an older backend.
        if(active)setRootState('hidden');
      }
    })();
    return()=>{active=false};
  },[]);

  async function loadAwemes(selectedBinding=bindingId,keyword=awemeKeyword){
    if(!selectedBinding)return;
    const request=++awemeRequest.current;
    productRequest.current+=1;
    setLoadingAwemes(true);setError('');setAwemes([]);setAwemeId('');setProducts([]);setProductId('');
    setProductCursor(null);setHasMoreProducts(false);
    try{
      const [gate,page]=await Promise.all([
        qianchuanApi.planCapabilities(selectedBinding),
        qianchuanApi.awemes(selectedBinding,{keyword:keyword.trim(),page:1,page_size:100}),
      ]);
      if(request!==awemeRequest.current)return;
      setCapabilities(gate);setAwemes(page.items);
    }catch(err){if(request===awemeRequest.current)setError(qianchuanError(err));}
    finally{if(request===awemeRequest.current)setLoadingAwemes(false);}
  }

  useEffect(()=>{
    setCapabilities(null);setAwemeKeyword('');setProductKeyword('');
    setCatalogMock(false);
    if(bindingId)void loadAwemes(bindingId,'');
    else{setAwemes([]);setAwemeId('');setProducts([]);setProductId('');}
    // loadAwemes is intentionally driven only by the selected binding.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[bindingId]);

  async function loadProducts(selectedAweme=awemeId,keyword=productKeyword,cursor?:number){
    if(!bindingId||!selectedAweme)return;
    const request=++productRequest.current;
    setLoadingProducts(true);setError('');
    if(cursor===undefined){setProducts([]);setProductId('');setProductCursor(null);setHasMoreProducts(false);}
    try{
      const page=await qianchuanApi.products(bindingId,{
        aweme_id:selectedAweme,keyword:keyword.trim(),cursor,page_size:50,
      });
      if(request!==productRequest.current)return;
      setProducts(current=>cursor===undefined?page.items:[...current,...page.items]);
      setProductCursor(page.cursor);setHasMoreProducts(page.has_more);
    }catch(err){if(request===productRequest.current)setError(qianchuanError(err));}
    finally{if(request===productRequest.current)setLoadingProducts(false);}
  }

  function selectAweme(next:string){
    setAwemeId(next);setProductKeyword('');setProducts([]);setProductId('');
    setProductCursor(null);setHasMoreProducts(false);
    productRequest.current+=1;
    if(next)void loadProducts(next,'');
  }

  function loadDemoCatalog(){
    const demoAweme:QianchuanAwemeOption={
      id:'9000000000000000001',name:'Mock 授权抖音号',show_id:'demo_aweme',avatar_url:'',
      status:'MOCK_NORMAL',has_live_permission:true,has_video_permission:true,
      has_publish_permission:true,bind_types:['MOCK'],
    };
    const demoProduct:QianchuanProductOption={
      id:'9000000000000000002',name:'Mock 可投商品｜蒂洛薇素颜精华霜',image_url:'',
      category_name:'美妆护肤',channel_type:'MOCK',
    };
    awemeRequest.current+=1;productRequest.current+=1;
    setLoadingAwemes(false);setLoadingProducts(false);setError('');setCatalogMock(true);
    setAwemes([demoAweme]);setAwemeId(demoAweme.id);setProducts([demoProduct]);setProductId(demoProduct.id);
    setProductCursor(null);setHasMoreProducts(false);
  }

  if(rootState!=='ready')return null;
  const account=accounts.find(row=>row.id===bindingId);
  const selectedAweme=awemes.find(row=>row.id===awemeId);
  const selectedProduct=products.find(row=>row.id===productId);
  // Historical uploads can carry the connection id while the catalog exposes
  // an account binding id. advertiser_id is the stable identity shared by both.
  const demoUpload=uploads.find(row=>(row.binding_id===bindingId||row.advertiser_id===account?.advertiser_id)
    &&row.status==='succeeded'&&Boolean(row.material_id));

  return <section aria-label="千川计划目录测试" className="rounded border border-[var(--border)] p-5">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div><h3 className="font-medium">千川计划目录（只读测试）</h3>
        <p className="mt-1 text-xs text-[var(--muted)]">只读取本人账号、授权抖音号和可投商品，不会创建或修改投放计划。</p></div>
      <span className="rounded bg-amber-50 px-3 py-1 text-xs text-amber-800">写入与 worker 均关闭</span>
    </div>
    {error&&<div className="mt-4 flex flex-wrap items-center gap-3"><p role="alert" className="text-sm text-red-600">{error}</p>
      {DEMO_ENABLED&&<button className="btn-secondary" onClick={loadDemoCatalog}>使用 Mock 目录继续演示</button>}</div>}
    {!accounts.length?<p className="mt-4 text-sm text-[var(--muted)]">当前没有本人可用的千川账号，请先绑定或同步账号。</p>:<>
      <div className="mt-5 grid gap-4 lg:grid-cols-3">
        <label className="text-sm">1. 投放账号
          <select aria-label="计划投放账号" className="input-select mt-2 w-full" value={bindingId}
            onChange={event=>setBindingId(event.target.value)}>
            {accounts.map(row=><option key={row.id} value={row.id}>{row.advertiser_name||'千川账户'} · {row.advertiser_id}</option>)}
          </select>
        </label>
        <form className="text-sm" onSubmit={event=>{event.preventDefault();void loadAwemes();}}>
          <label htmlFor="qianchuan-aweme-keyword">2. 搜索授权抖音号</label>
          <div className="mt-2 flex gap-2"><input id="qianchuan-aweme-keyword" className="min-w-0 flex-1 rounded border border-[var(--border)] p-2"
            maxLength={100} value={awemeKeyword} onChange={event=>setAwemeKeyword(event.target.value)} placeholder="名称或展示抖音号"/>
            <button className="btn-secondary" aria-label="搜索授权抖音号" disabled={loadingAwemes}><Search size={15}/></button></div>
        </form>
        <label className="text-sm">3. 授权抖音号
          <select aria-label="授权抖音号" className="input-select mt-2 w-full" value={awemeId}
            onChange={event=>selectAweme(event.target.value)} disabled={loadingAwemes||!awemes.length}>
            <option value="">{loadingAwemes?'正在读取':awemes.length?'请选择':'没有可用抖音号'}</option>
            {awemes.map(row=><option key={row.id} value={row.id}>{row.name||'未命名抖音号'}{row.show_id?` · ${row.show_id}`:''}</option>)}
          </select>
        </label>
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_1fr_auto]">
        <form className="text-sm" onSubmit={event=>{event.preventDefault();void loadProducts();}}>
          <label htmlFor="qianchuan-product-keyword">4. 搜索可投商品</label>
          <div className="mt-2 flex gap-2"><input id="qianchuan-product-keyword" className="min-w-0 flex-1 rounded border border-[var(--border)] p-2"
            maxLength={100} value={productKeyword} onChange={event=>setProductKeyword(event.target.value)} placeholder="商品名称" disabled={!awemeId}/>
            <button className="btn-secondary" aria-label="搜索可投商品" disabled={!awemeId||loadingProducts}><Search size={15}/></button></div>
        </form>
        <label className="text-sm">5. 可投商品
          <select aria-label="可投商品" className="input-select mt-2 w-full" value={productId}
            onChange={event=>setProductId(event.target.value)} disabled={!awemeId||loadingProducts||!products.length}>
            <option value="">{loadingProducts?'正在读取':products.length?'请选择':'没有可投商品'}</option>
            {products.map(row=><option key={row.id} value={row.id}>{row.name||'未命名商品'} · {row.id}</option>)}
          </select>
        </label>
        <button className="btn-secondary self-end" disabled={loadingAwemes||loadingProducts}
          onClick={()=>void loadAwemes()}><RefreshCw size={15}/>刷新目录</button>
      </div>
      {(loadingAwemes||loadingProducts)&&<p className="mt-4 flex items-center gap-2 text-sm text-[var(--muted)]"><Loader2 size={15} className="animate-spin"/>正在读取千川目录</p>}
      <div className="mt-4 rounded bg-[var(--bg)] p-4 text-xs text-[var(--muted)]">
        <p>账号：{account?.advertiser_name||'—'} · {account?.advertiser_id||'—'}</p>
        <p className="mt-1">抖音号：{selectedAweme?`${selectedAweme.name||'未命名'} · ${selectedAweme.id}`:'尚未选择'}</p>
        <p className="mt-1">商品：{selectedProduct?`${selectedProduct.name||'未命名'} · ${selectedProduct.id}`:'尚未选择'}</p>
        <p className="mt-2">创建能力：{catalogMock?'Mock 演示目录已就绪':capabilities?.can_create?'能力探测已满足，但本页面仍禁止写入':'尚未通过能力门，禁止创建计划'}</p>
      </div>
      {selectedAweme&&selectedProduct&&demoUpload&&DEMO_ENABLED&&
        <QianchuanPlanDemo key={`${bindingId}-${selectedAweme.id}-${selectedProduct.id}-${demoUpload.id}`}
          account={account!} aweme={selectedAweme} product={selectedProduct} upload={demoUpload}
          catalogMock={catalogMock} onCompleted={onPlanCompleted}/>} 
      {selectedAweme&&selectedProduct&&!demoUpload&&<p className="mt-4 rounded bg-amber-50 p-4 text-sm text-amber-900">当前账号还没有核验成功的视频素材，完成素材上传后即可进入 Mock 计划演示。</p>}
      {hasMoreProducts&&productCursor!==null&&<button className="btn-secondary mt-4" disabled={loadingProducts}
        onClick={()=>void loadProducts(awemeId,productKeyword,productCursor)}>加载更多商品</button>}
    </>}
  </section>;
}
