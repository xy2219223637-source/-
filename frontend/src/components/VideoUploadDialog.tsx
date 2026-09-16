import {useEffect,useMemo,useState} from 'react';
import {Check,CheckCircle2,FileVideo,Loader2,Plus,RefreshCw,Search,Tag,Upload,X} from 'lucide-react';
import {videoLibraryApi,videoTaxonomyApi} from '../api/client';
import type {VideoTaxonomyCatalog,VideoTaxonomyTag} from '../types';

type LibraryType='semi_finished'|'finished';
type UploadStatus='waiting'|'uploading'|'success'|'error';
type UploadItem={id:string;file:File;status:UploadStatus;percent:number;error:string;assetId?:number};

type Props={
  libraryType:LibraryType;
  productId:number;
  productName:string;
  catalog:VideoTaxonomyCatalog;
  onClose:()=>void;
  onUploaded:()=>Promise<void>;
  onCatalogRefresh?:()=>Promise<void>;
};

const videoExtension=/\.(mp4|mov|m4v|webm|mkv|avi)$/i;

function formatSize(bytes:number){
  if(bytes>=1024**3)return`${(bytes/1024**3).toFixed(2)} GB`;
  if(bytes>=1024**2)return`${(bytes/1024**2).toFixed(1)} MB`;
  if(bytes>=1024)return`${(bytes/1024).toFixed(1)} KB`;
  return`${bytes} B`;
}

function messageFrom(error:unknown,fallback:string){
  const detail=(error as {response?:{data?:{detail?:unknown}}})?.response?.data?.detail;
  if(typeof detail==='string')return detail;
  if(detail&&typeof detail==='object'&&'message'in detail&&typeof detail.message==='string')return detail.message;
  return fallback;
}

function toggleId(values:number[],id:number){
  return values.includes(id)?values.filter(value=>value!==id):[...values,id];
}

function tagMatches(tag:VideoTaxonomyTag,search:string){
  return!search||tag.name.toLowerCase().includes(search.toLowerCase());
}

export default function VideoUploadDialog({libraryType,productId,productName,catalog,onClose,onUploaded,onCatalogRefresh}:Props){
  const [workingCatalog,setWorkingCatalog]=useState(catalog);
  const [items,setItems]=useState<UploadItem[]>([]);
  const [dragging,setDragging]=useState(false);
  const [selectionError,setSelectionError]=useState('');
  const [parentCategoryId,setParentCategoryId]=useState('');
  const [categoryId,setCategoryId]=useState('');
  const [activeGroupId,setActiveGroupId]=useState<number|null>(null);
  const [publicSearch,setPublicSearch]=useState('');
  const [personalSearch,setPersonalSearch]=useState('');
  const [selectedPublicTagIds,setSelectedPublicTagIds]=useState<number[]>([]);
  const [selectedPersonalTagIds,setSelectedPersonalTagIds]=useState<number[]>([]);
  const [addingPersonal,setAddingPersonal]=useState(false);
  const [personalName,setPersonalName]=useState('');
  const [creatingPersonal,setCreatingPersonal]=useState(false);
  const [uploading,setUploading]=useState(false);
  const [associating,setAssociating]=useState(false);
  const [overallPercent,setOverallPercent]=useState(0);
  const [error,setError]=useState('');
  const [assignmentError,setAssignmentError]=useState('');
  const [pendingAssignmentIds,setPendingAssignmentIds]=useState<number[]>([]);

  const parents=useMemo(()=>workingCatalog.categories.filter(item=>item.parent_id===null&&item.status==='active'),[workingCatalog.categories]);
  const children=useMemo(()=>workingCatalog.categories.filter(item=>item.parent_id===Number(parentCategoryId)&&item.status==='active'),[workingCatalog.categories,parentCategoryId]);
  const groups=useMemo(()=>workingCatalog.groups.filter(group=>group.status==='active'&&workingCatalog.public_tags.some(tag=>tag.group_id===group.id&&tag.status==='active')),[workingCatalog]);
  const activeGroup=groups.find(group=>group.id===activeGroupId)??null;
  const visiblePublicTags=workingCatalog.public_tags.filter(tag=>tag.group_id===activeGroup?.id&&tag.status==='active'&&tagMatches(tag,publicSearch));
  const visiblePersonalTags=workingCatalog.personal_tags.filter(tag=>tag.status==='active'&&tagMatches(tag,personalSearch));
  const selectedPublicTags=selectedPublicTagIds.map(id=>workingCatalog.public_tags.find(tag=>tag.id===id)).filter((tag):tag is VideoTaxonomyTag=>Boolean(tag));
  const selectedPersonalTags=selectedPersonalTagIds.map(id=>workingCatalog.personal_tags.find(tag=>tag.id===id)).filter((tag):tag is VideoTaxonomyTag=>Boolean(tag));
  const successCount=items.filter(item=>item.status==='success').length;
  const failureCount=items.filter(item=>item.status==='error').length;
  const hasStarted=items.some(item=>item.status!=='waiting');
  const busy=uploading||associating||creatingPersonal;
  const readyItems=items.filter(item=>item.status==='waiting');
  const failedItems=items.filter(item=>item.status==='error');

  useEffect(()=>setWorkingCatalog(catalog),[catalog]);
  useEffect(()=>{
    if(!groups.some(group=>group.id===activeGroupId))setActiveGroupId(groups[0]?.id??null);
  },[groups,activeGroupId]);
  useEffect(()=>{
    const previous=document.body.style.overflow;
    document.body.style.overflow='hidden';
    return()=>{document.body.style.overflow=previous};
  },[]);
  useEffect(()=>{
    if(!uploading)return;
    const preventClose=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue=''};
    window.addEventListener('beforeunload',preventClose);
    return()=>window.removeEventListener('beforeunload',preventClose);
  },[uploading]);
  useEffect(()=>{
    const closeOnEscape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!busy)requestClose()};
    window.addEventListener('keydown',closeOnEscape);
    return()=>window.removeEventListener('keydown',closeOnEscape);
  });

  function requestClose(){
    if(busy)return;
    if((failureCount>0||pendingAssignmentIds.length>0)&&!window.confirm('仍有失败项或待关联信息, 关闭后需在素材详情中继续处理。确定关闭吗?'))return;
    onClose();
  }

  function patchItem(id:string,patch:Partial<UploadItem>){
    setItems(current=>current.map(item=>item.id===id?{...item,...patch}:item));
  }

  function addFiles(incoming:File[]){
    if(hasStarted)return;
    const videos=incoming.filter(file=>file.type.startsWith('video/')||videoExtension.test(file.name));
    const merged=[...items];
    videos.forEach(file=>{
      const id=`${file.name}-${file.size}-${file.lastModified}`;
      if(!merged.some(item=>item.id===id))merged.push({id,file,status:'waiting',percent:0,error:''});
    });
    const messages:string[]=[];
    if(incoming.length-videos.length)messages.push(`已忽略 ${incoming.length-videos.length} 个非视频文件`);
    if(merged.length>30)messages.push('单批最多保留 30 条视频');
    setItems(merged.slice(0,30));
    setSelectionError(messages.join('; '));
  }

  function confirmPublicTagReset(){
    return!selectedPublicTagIds.length||window.confirm('切换视频分类将清空已选公共标签, 是否继续?');
  }

  function changeParent(next:string){
    if(next===parentCategoryId)return;
    if(categoryId&&!confirmPublicTagReset())return;
    setParentCategoryId(next);
    setCategoryId('');
    setSelectedPublicTagIds([]);
    setPublicSearch('');
  }

  function changeCategory(next:string){
    if(next===categoryId)return;
    if(categoryId&&!confirmPublicTagReset())return;
    setCategoryId(next);
    setSelectedPublicTagIds([]);
    setPublicSearch('');
  }

  async function createPersonalTag(){
    const name=personalName.trim();
    if(!name)return;
    setCreatingPersonal(true);setError('');
    try{
      const tag=await videoTaxonomyApi.createPersonalTag({name,applies_to:'material'});
      setWorkingCatalog(current=>({...current,personal_tags:[...current.personal_tags,tag]}));
      setSelectedPersonalTagIds(current=>[...current,tag.id]);
      setPersonalName('');setAddingPersonal(false);
      await onCatalogRefresh?.();
    }catch(errorValue){setError(messageFrom(errorValue,'个人标签创建失败'))}
    finally{setCreatingPersonal(false)}
  }

  async function applyTaxonomy(assetIds:number[]){
    await videoTaxonomyApi.updateAssets({
      asset_ids:assetIds,
      set_category:true,
      category_id:Number(categoryId),
      add_tag_ids:[...selectedPublicTagIds,...selectedPersonalTagIds],
    });
  }

  async function uploadTargets(targets:UploadItem[]){
    if(!categoryId){setError('请选择二级分类');return}
    if(!targets.length)return;
    setUploading(true);setError('');setAssignmentError('');setOverallPercent(0);
    const totalBytes=targets.reduce((total,item)=>total+item.file.size,0);
    let completedBytes=0;
    const uploadedIds:number[]=[];
    try{
      for(const item of targets){
        patchItem(item.id,{status:'uploading',percent:0,error:''});
        try{
          const rows=await videoLibraryApi.upload(
            productId,
            [item.file],
            'manual_upload',
            (loaded,total,percent)=>{
              patchItem(item.id,{percent});
              setOverallPercent(totalBytes?Math.round((completedBytes+Math.min(loaded,total))/totalBytes*100):0);
            },
            libraryType,
          );
          const asset=rows[0];
          if(!asset)throw new Error('上传完成后未返回素材信息');
          uploadedIds.push(asset.id);
          patchItem(item.id,{status:'success',percent:100,error:'',assetId:asset.id});
        }catch(errorValue){
          patchItem(item.id,{status:'error',percent:0,error:messageFrom(errorValue,'上传失败')});
        }
        completedBytes+=item.file.size;
        setOverallPercent(totalBytes?Math.round(completedBytes/totalBytes*100):100);
      }
      if(uploadedIds.length){
        try{
          await applyTaxonomy(uploadedIds);
        }catch(errorValue){
          setPendingAssignmentIds(current=>Array.from(new Set([...current,...uploadedIds])));
          setAssignmentError(messageFrom(errorValue,'视频已上传, 但分类与标签关联失败'));
        }
        await onUploaded();
      }
    }finally{setUploading(false)}
  }

  async function retryAssignment(){
    if(!pendingAssignmentIds.length)return;
    setAssociating(true);setError('');setAssignmentError('');
    try{
      await applyTaxonomy(pendingAssignmentIds);
      setPendingAssignmentIds([]);
      await onUploaded();
    }catch(errorValue){setAssignmentError(messageFrom(errorValue,'分类与标签关联仍然失败'))}
    finally{setAssociating(false)}
  }

  const uploadComplete=items.length>0&&successCount===items.length&&!pendingAssignmentIds.length;

  return <div className="fixed inset-0 z-50 bg-black/45 p-2 sm:p-4" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)requestClose()}}>
    <div className="mx-auto flex h-[calc(100vh-1rem)] w-full max-w-6xl flex-col overflow-hidden border border-[var(--border)] bg-white shadow-2xl sm:h-[calc(100vh-2rem)]" role="dialog" aria-modal="true" aria-labelledby="video-upload-title">
      <header className="flex shrink-0 items-center justify-between gap-4 border-b border-[var(--border)] px-4 py-4 sm:px-6">
        <div className="min-w-0"><h2 id="video-upload-title" className="text-lg font-medium">上传视频</h2><p className="mt-1 truncate text-xs text-[var(--muted)]">{libraryType==='finished'?'成品素材库':'半成品素材库'} · {productName}</p></div>
        <button type="button" className="grid h-9 w-9 shrink-0 place-items-center text-[var(--muted)] hover:text-black" onClick={requestClose} disabled={busy} aria-label="关闭上传页面"><X size={19}/></button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
        {(error||assignmentError)&&<div className="mb-5 border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700" role="alert">{error||assignmentError}</div>}
        {uploadComplete&&<div className="mb-5 flex items-center gap-2 border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800"><CheckCircle2 size={17}/>全部视频已上传并完成分类与标签关联</div>}

        <section aria-labelledby="upload-files-heading">
          <div className="mb-3 flex items-center justify-between gap-3"><h3 id="upload-files-heading" className="text-sm font-medium">1. 视频文件</h3><span className="text-xs text-[var(--muted)]">最多 30 条</span></div>
          <div onDragEnter={event=>{event.preventDefault();if(!hasStarted)setDragging(true)}} onDragOver={event=>{event.preventDefault();event.dataTransfer.dropEffect='copy';if(!hasStarted)setDragging(true)}} onDragLeave={event=>{event.preventDefault();if(!event.currentTarget.contains(event.relatedTarget as Node))setDragging(false)}} onDrop={event=>{event.preventDefault();setDragging(false);addFiles(Array.from(event.dataTransfer.files))}} className={`border-2 border-dashed px-4 py-7 text-center transition-colors ${dragging?'border-blue-500 bg-blue-50':'border-[var(--border)] bg-[#faf9f7]'} ${hasStarted?'opacity-60':''}`}>
            <Upload className="mx-auto text-[var(--muted)]" size={26}/><div className="mt-2 text-sm font-medium">{dragging?'松开以加入上传队列':'拖拽视频到这里'}</div>
            <label className={`btn-secondary mt-3 inline-flex ${hasStarted?'cursor-not-allowed':'cursor-pointer'}`}><FileVideo size={15}/>选择视频<input hidden multiple disabled={hasStarted} type="file" accept="video/*,.mp4,.mov,.m4v,.webm,.mkv,.avi" onChange={event=>{addFiles(Array.from(event.target.files||[]));event.currentTarget.value=''}}/></label>
          </div>
          {selectionError&&<p className="mt-2 text-xs text-amber-700">{selectionError}</p>}
          {items.length>0&&<div className="mt-3 overflow-hidden border border-[var(--border)]">
            <div className="flex items-center justify-between bg-[#f6f5f2] px-4 py-2.5 text-xs"><span>{items.length} 条视频 · {formatSize(items.reduce((total,item)=>total+item.file.size,0))}</span>{!hasStarted&&<button type="button" className="text-[var(--muted)] hover:text-black" onClick={()=>{setItems([]);setSelectionError('')}}>清空</button>}</div>
            <div className="max-h-52 divide-y divide-[var(--border)] overflow-y-auto">{items.map(item=><div key={item.id} className="grid min-h-14 grid-cols-[1fr_auto] items-center gap-3 px-4 py-2.5 text-xs">
              <div className="min-w-0"><div className="flex items-center gap-2"><span className="truncate font-medium">{item.file.name}</span>{item.status==='success'&&<CheckCircle2 className="shrink-0 text-emerald-600" size={14}/>} {item.status==='uploading'&&<Loader2 className="shrink-0 animate-spin text-blue-600" size={14}/>} {item.status==='error'&&<span className="shrink-0 text-red-600">失败</span>}</div><div className="mt-1 text-[var(--muted)]">{formatSize(item.file.size)}{item.error&&<span className="ml-2 text-red-600">{item.error}</span>}</div>{item.status==='uploading'&&<div className="mt-2 h-1.5 overflow-hidden bg-blue-100"><div className="h-full bg-blue-600 transition-[width]" style={{width:`${item.percent}%`}}/></div>}</div>
              {item.status==='waiting'&&<button type="button" className="grid h-8 w-8 place-items-center text-[var(--muted)] hover:text-black" onClick={()=>setItems(current=>current.filter(value=>value.id!==item.id))} aria-label={`移除 ${item.file.name}`}><X size={15}/></button>}
            </div>)}</div>
          </div>}
        </section>

        <section className="mt-7 border-t border-[var(--border)] pt-5" aria-labelledby="upload-category-heading">
          <div className="mb-3 flex items-center gap-2"><h3 id="upload-category-heading" className="text-sm font-medium">2. 视频分类</h3><span className="text-xs text-red-600">必填</span></div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="field"><span>一级分类</span><select className="input-select" value={parentCategoryId} disabled={hasStarted} onChange={event=>changeParent(event.target.value)}><option value="">请选择一级分类</option>{parents.map(parent=><option key={parent.id} value={parent.id}>{parent.name}</option>)}</select></label>
            <label className="field"><span>二级分类</span><select className="input-select" value={categoryId} disabled={!parentCategoryId||hasStarted} onChange={event=>changeCategory(event.target.value)}><option value="">请选择二级分类</option>{children.map(child=><option key={child.id} value={child.id}>{child.name}</option>)}</select></label>
          </div>
          {parentCategoryId&&!children.length&&<p className="mt-2 text-xs text-amber-700">该一级分类暂无可用二级分类</p>}
        </section>

        <section className="mt-7 border-t border-[var(--border)] pt-5" aria-labelledby="upload-tags-heading">
          <div className="mb-3 flex items-center justify-between gap-3"><div className="flex items-center gap-2"><h3 id="upload-tags-heading" className="text-sm font-medium">3. 标签信息</h3><span className="text-xs text-[var(--muted)]">选填</span></div><span className="text-xs text-[var(--muted)]">公共 {selectedPublicTagIds.length} · 个人 {selectedPersonalTagIds.length}</span></div>
          {!categoryId?<div className="border border-dashed border-[var(--border)] bg-[#faf9f7] px-4 py-8 text-center text-sm text-[var(--muted)]">选择二级分类后配置标签</div>:<>
            <div>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h4 className="text-xs font-medium">公共标签</h4><div className="relative w-full sm:w-64"><Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" size={14}/><input className="h-9 w-full border border-[var(--border)] pl-9 pr-3 text-xs" value={publicSearch} disabled={hasStarted} onChange={event=>setPublicSearch(event.target.value)} placeholder="搜索当前标签组"/></div></div>
              {selectedPublicTags.length>0&&<div className="mb-3 flex flex-wrap gap-2">{selectedPublicTags.map(tag=>{const group=groups.find(item=>item.id===tag.group_id);return <span key={tag.id} className="inline-flex min-h-8 items-center gap-1.5 border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs text-emerald-800"><span>{group?.name?`${group.name} / `:''}{tag.name}</span>{!hasStarted&&<button type="button" onClick={()=>setSelectedPublicTagIds(current=>current.filter(id=>id!==tag.id))} aria-label={`移除 ${tag.name}`}><X size={12}/></button>}</span>})}</div>}
              <div className="grid min-h-44 overflow-hidden border border-[var(--border)] md:grid-cols-[180px_1fr]">
                <div className="border-b border-[var(--border)] bg-[#f6f5f2] p-2 md:border-b-0 md:border-r"><div className="grid grid-cols-2 gap-1 md:grid-cols-1">{groups.map(group=><button type="button" key={group.id} disabled={hasStarted} onClick={()=>{setActiveGroupId(group.id);setPublicSearch('')}} className={`min-h-9 px-3 text-left text-xs ${activeGroupId===group.id?'bg-black text-white':'hover:bg-white'}`}>{group.name}</button>)}</div></div>
                <div className="p-4"><div className="flex flex-wrap gap-2">{visiblePublicTags.map(tag=>{const selected=selectedPublicTagIds.includes(tag.id);return <label key={tag.id} className={`inline-flex min-h-9 items-center gap-2 border px-3 py-1.5 text-xs ${hasStarted?'cursor-default opacity-70':'cursor-pointer'} ${selected?'border-black bg-black text-white':'border-[var(--border)] bg-white hover:border-black'}`}><input className="sr-only" type="checkbox" disabled={hasStarted} checked={selected} onChange={()=>setSelectedPublicTagIds(current=>toggleId(current,tag.id))}/>{selected&&<Check size={12}/>}<span>{tag.name}</span></label>})}{!visiblePublicTags.length&&<span className="py-2 text-xs text-[var(--muted)]">当前标签组暂无匹配标签</span>}</div></div>
              </div>
            </div>

            <div className="mt-6 border-t border-[var(--border)] pt-5">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h4 className="text-xs font-medium">个人标签</h4><div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">{addingPersonal?<><input autoFocus className="h-9 min-w-0 flex-1 border border-black px-3 text-xs sm:w-48" value={personalName} disabled={hasStarted} onChange={event=>setPersonalName(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();void createPersonalTag()}if(event.key==='Escape')setAddingPersonal(false)}} placeholder="个人标签名称"/><button type="button" className="grid h-9 w-9 place-items-center border border-black" onClick={()=>void createPersonalTag()} disabled={!personalName.trim()||creatingPersonal||hasStarted} aria-label="保存个人标签">{creatingPersonal?<Loader2 className="animate-spin" size={14}/>:<Check size={14}/>}</button><button type="button" className="grid h-9 w-9 place-items-center border border-[var(--border)]" onClick={()=>{setAddingPersonal(false);setPersonalName('')}} disabled={creatingPersonal} aria-label="取消创建"><X size={14}/></button></>:<button type="button" className="btn-secondary" disabled={hasStarted} onClick={()=>setAddingPersonal(true)}><Plus size={14}/>新建个人标签</button>}</div></div>
              <div className="relative mb-3 max-w-sm"><Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" size={14}/><input className="h-9 w-full border border-[var(--border)] pl-9 pr-3 text-xs" value={personalSearch} disabled={hasStarted} onChange={event=>setPersonalSearch(event.target.value)} placeholder="搜索个人标签"/></div>
              <div className="flex min-h-12 flex-wrap gap-2">{visiblePersonalTags.map(tag=>{const selected=selectedPersonalTagIds.includes(tag.id);return <label key={tag.id} className={`inline-flex min-h-9 items-center gap-2 border px-3 py-1.5 text-xs ${hasStarted?'cursor-default opacity-70':'cursor-pointer'} ${selected?'border-amber-500 bg-amber-50 text-amber-900':'border-[var(--border)] bg-white hover:border-black'}`}><input className="sr-only" type="checkbox" disabled={hasStarted} checked={selected} onChange={()=>setSelectedPersonalTagIds(current=>toggleId(current,tag.id))}/>{selected&&<Check size={12}/>}<span>{tag.name}</span></label>})}{!visiblePersonalTags.length&&<span className="py-2 text-xs text-[var(--muted)]">暂无匹配的个人标签</span>}</div>
              {selectedPersonalTags.length>0&&<div className="mt-3 flex flex-wrap gap-2">{selectedPersonalTags.map(tag=><span key={tag.id} className="inline-flex min-h-8 items-center gap-1.5 border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs text-amber-800"><Tag size={12}/><span>{tag.name}</span>{!hasStarted&&<button type="button" onClick={()=>setSelectedPersonalTagIds(current=>current.filter(id=>id!==tag.id))} aria-label={`移除 ${tag.name}`}><X size={12}/></button>}</span>)}</div>}
            </div>
          </>}
        </section>
      </div>

      <footer className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3 sm:px-6">
        <div className="text-xs text-[var(--muted)]">{uploading?`正在处理 ${overallPercent}%`:hasStarted?`成功 ${successCount} 条${failureCount?` · 失败 ${failureCount} 条`:''}`:`分类和标签将应用到本批全部视频`}</div>
        <div className="flex items-center gap-2"><button type="button" className="btn-secondary" onClick={requestClose} disabled={busy}>{hasStarted?'关闭':'取消'}</button>{pendingAssignmentIds.length>0?<button type="button" className="btn-primary" disabled={busy} onClick={()=>void retryAssignment()}>{associating?<Loader2 className="animate-spin" size={15}/>:<RefreshCw size={15}/>}重试关联信息</button>:failureCount>0?<button type="button" className="btn-primary" disabled={busy} onClick={()=>void uploadTargets(failedItems)}>{uploading?<Loader2 className="animate-spin" size={15}/>:<RefreshCw size={15}/>}重试失败项</button>:uploadComplete?<button type="button" className="btn-primary" onClick={requestClose}><CheckCircle2 size={15}/>完成</button>:<button type="button" className="btn-primary" disabled={busy||!readyItems.length||!categoryId} onClick={()=>void uploadTargets(readyItems)}>{uploading?<Loader2 className="animate-spin" size={15}/>:<Upload size={15}/>}开始上传</button>}</div>
      </footer>
    </div>
  </div>;
}
