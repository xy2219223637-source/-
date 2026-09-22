import {useEffect,useMemo,useState,type FormEvent} from 'react';
import {Check,ChevronRight,FolderTree,GripVertical,Loader2,Pencil,Plus,Search,ShieldAlert,Tags,Trash2,X} from 'lucide-react';
import {Link} from 'react-router-dom';
import {authApi,videoTaxonomyApi} from '../api/client';
import type {VideoCategoryDefinition,VideoObjectType,VideoTagAppliesTo,VideoTagGroupDefinition,VideoTaxonomyCatalog,VideoTaxonomyTag} from '../types';

type EditorKind='category'|'group'|'tag';
type EditorState={kind:EditorKind;item?:VideoCategoryDefinition|VideoTagGroupDefinition|VideoTaxonomyTag;level?:1|2};
type Draft={name:string;code:string;parent_id:number|null;group_id:number|null;applies_to:VideoTagAppliesTo;inherit_to_final:boolean;sort_order:number;status:'active'|'inactive'};
type SortKind='parents'|'children'|'groups'|'tags';
type SortableEntity={id:number;name:string;sort_order:number;status:'active'|'inactive'};
type TaxonomyListProps<T extends SortableEntity>={
  title:string;
  items:T[];
  selectedId?:number|null;
  emptyText:string;
  meta:(item:T)=>string;
  busy:boolean;
  onAdd?:()=>void;
  onSelect?:(item:T)=>void;
  onEdit:(item:T)=>void;
  onDelete:(item:T)=>void;
  deleteBusy:boolean;
  deletingId?:number|null;
  onReorder:(sourceId:number,targetId:number)=>Promise<void>;
  onMove:(itemId:number,direction:-1|1)=>Promise<void>;
};

const emptyDraft:Draft={name:'',code:'',parent_id:null,group_id:null,applies_to:'both',inherit_to_final:true,sort_order:0,status:'active'};
const appliesToText:Record<VideoTagAppliesTo,string>={material:'视频素材',final_output:'成片',both:'素材和成片'};

function errorMessage(error:any,fallback:string){
  const detail=error?.response?.data?.detail;
  return typeof detail==='string'?detail:detail?.message||fallback;
}

function bySortOrder<T extends SortableEntity>(left:T,right:T){
  return left.sort_order-right.sort_order||left.id-right.id;
}

function TaxonomyList<T extends SortableEntity>({title,items,selectedId,emptyText,meta,busy,onAdd,onSelect,onEdit,onDelete,deleteBusy,deletingId=null,onReorder,onMove}:TaxonomyListProps<T>){
  const [search,setSearch]=useState('');
  const [draggingId,setDraggingId]=useState<number|null>(null);
  const [dropTargetId,setDropTargetId]=useState<number|null>(null);
  const normalizedSearch=search.trim().toLowerCase();
  const visibleItems=items.filter(item=>!normalizedSearch||item.name.toLowerCase().includes(normalizedSearch));
  const sortingDisabled=busy||Boolean(normalizedSearch);

  return <section className="overflow-hidden border border-[var(--border)] bg-white">
    <header className="flex min-h-12 items-center gap-3 border-b border-[var(--border)] bg-[var(--bg-elevated)] px-3">
      <h3 className="min-w-0 flex-1 text-sm font-medium">{title}<span className="ml-2 text-xs font-normal text-[var(--muted)]">{items.length}</span></h3>
      {busy?<Loader2 size={15} className="shrink-0 animate-spin text-[var(--muted)]"/>:onAdd&&<button type="button" className="tax-icon-button p-2" onClick={onAdd} aria-label={`新建${title}`} title={`新建${title}`}><Plus size={15}/></button>}
    </header>
    {items.length>20&&<label className="relative block border-b border-[var(--border)] p-3">
      <Search size={14} className="pointer-events-none absolute left-6 top-6 text-[var(--muted)]"/>
      <input className="h-10 w-full border border-[var(--border)] bg-white pl-9 pr-3 text-sm outline-none focus:border-black" value={search} onChange={event=>setSearch(event.target.value)} placeholder={`搜索${title}`}/>
    </label>}
    <div className="h-80 overflow-y-auto" role="list" aria-label={title}>
      {visibleItems.map(item=>{
        const selected=selectedId===item.id;
        const dragging=draggingId===item.id;
        const dropTarget=dropTargetId===item.id&&draggingId!==item.id;
        const deleting=deletingId===item.id;
        return <div
          key={item.id}
          role="listitem"
          onDragEnter={()=>{if(draggingId!==null)setDropTargetId(item.id)}}
          onDragOver={event=>{if(draggingId!==null){event.preventDefault();event.dataTransfer.dropEffect='move'}}}
          onDrop={event=>{
            event.preventDefault();
            const sourceId=draggingId;
            setDraggingId(null);setDropTargetId(null);
            if(sourceId!==null&&sourceId!==item.id)void onReorder(sourceId,item.id);
          }}
          className={`group flex min-h-12 items-center border-b border-[var(--border)] last:border-b-0 ${selected?'border-l-2 border-l-black bg-[var(--bg-elevated)]':'border-l-2 border-l-transparent'} ${dropTarget?'bg-neutral-100':''} ${dragging?'opacity-40':''} ${item.status==='inactive'?'text-[var(--muted)]':''}`}
        >
          <button
            type="button"
            draggable={!sortingDisabled}
            onDragStart={event=>{
              if(sortingDisabled){event.preventDefault();return}
              setDraggingId(item.id);setDropTargetId(null);
              event.dataTransfer.effectAllowed='move';
              event.dataTransfer.setData('text/plain',String(item.id));
            }}
            onDragEnd={()=>{setDraggingId(null);setDropTargetId(null)}}
            onKeyDown={event=>{
              if(sortingDisabled)return;
              if(event.key==='ArrowUp'){event.preventDefault();void onMove(item.id,-1)}
              if(event.key==='ArrowDown'){event.preventDefault();void onMove(item.id,1)}
            }}
            className="grid h-12 w-10 shrink-0 cursor-grab place-items-center text-[var(--muted)] opacity-40 hover:text-black hover:opacity-100 focus:opacity-100 active:cursor-grabbing disabled:cursor-not-allowed"
            aria-label={`调整${item.name}排序`}
            title={normalizedSearch?'清除搜索后可调整排序':'拖拽调整排序'}
          ><GripVertical size={15}/></button>
          {onSelect?<button type="button" aria-pressed={selected} className="min-w-0 flex-1 self-stretch px-2 text-left text-sm" onClick={()=>onSelect(item)}><span className="block truncate">{item.name}</span></button>:<div className="min-w-0 flex-1 px-2 text-sm"><span className="block truncate">{item.name}</span></div>}
          <span className="shrink-0 px-2 text-[11px] text-[var(--muted)]">{meta(item)}</span>
          {item.status==='inactive'&&<span className="shrink-0 border border-[var(--border)] px-2 py-1 text-[10px]">已停用</span>}
          <button type="button" className="tax-icon-button m-1 p-2 text-[var(--muted)] hover:text-black" onClick={()=>onEdit(item)} aria-label={`编辑 ${item.name}`} title={`编辑 ${item.name}`}><Pencil size={14}/></button>
          <button type="button" className="tax-icon-button mr-1 p-2 text-[var(--muted)] hover:text-[var(--danger)]" onClick={()=>onDelete(item)} disabled={busy||deleteBusy} aria-label={`删除 ${item.name}`} title={`删除 ${item.name}`}>{deleting?<Loader2 size={14} className="animate-spin"/>:<Trash2 size={14}/>}</button>
        </div>;
      })}
      {!visibleItems.length&&<div className="grid h-full place-items-center px-4 text-center text-xs text-[var(--muted)]">{normalizedSearch?'没有匹配结果':emptyText}</div>}
    </div>
  </section>;
}

export default function VideoTaxonomyPage(){
  const [access,setAccess]=useState<'loading'|'allowed'|'denied'>('loading');
  const [objectType,setObjectType]=useState<VideoObjectType>('material');
  const [catalog,setCatalog]=useState<VideoTaxonomyCatalog|null>(null);
  const [selectedParentId,setSelectedParentId]=useState<number|null>(null);
  const [selectedGroupId,setSelectedGroupId]=useState<number|null>(null);
  const [showInactive,setShowInactive]=useState(true);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState('');
  const [editor,setEditor]=useState<EditorState|null>(null);
  const [draft,setDraft]=useState<Draft>(emptyDraft);
  const [mergeTargetId,setMergeTargetId]=useState('');
  const [saving,setSaving]=useState(false);
  const [deleting,setDeleting]=useState<{kind:EditorKind;id:number}|null>(null);
  const [reordering,setReordering]=useState<SortKind|null>(null);

  async function load(type=objectType){
    setLoading(true);setError('');
    try{
      const data=await videoTaxonomyApi.catalog(type,true);setCatalog(data);
      setSelectedParentId(current=>data.categories.some(item=>item.id===current&&item.parent_id===null)?current:data.categories.find(item=>item.parent_id===null)?.id??null);
      setSelectedGroupId(current=>data.groups.some(item=>item.id===current)?current:data.groups[0]?.id??null);
    }catch(err:any){setError(errorMessage(err,'分类与标签加载失败'))}finally{setLoading(false)}
  }
  useEffect(()=>{
    let active=true;
    authApi.me().then(result=>{
      if(active)setAccess(result.role==='owner'||result.role==='admin'?'allowed':'denied');
    }).catch(()=>{if(active)setAccess('denied')});
    return()=>{active=false};
  },[]);
  useEffect(()=>{if(access==='allowed')void load(objectType)},[access,objectType]);

  const categories=useMemo(()=>[...(catalog?.categories.filter(item=>showInactive||item.status==='active')||[])].sort(bySortOrder),[catalog,showInactive]);
  const parents=categories.filter(item=>item.parent_id===null);
  const children=categories.filter(item=>item.parent_id===selectedParentId);
  const groups=useMemo(()=>[...(catalog?.groups.filter(item=>showInactive||item.status==='active')||[])].sort(bySortOrder),[catalog,showInactive]);
  const selectedGroup=groups.find(item=>item.id===selectedGroupId)??null;
  const groupTags=useMemo(()=>[...(catalog?.public_tags.filter(item=>item.group_id===selectedGroupId&&(showInactive||item.status==='active'))||[])].sort(bySortOrder),[catalog,selectedGroupId,showInactive]);
  const activeCategories=catalog?.categories.filter(item=>item.status==='active').length||0;
  const activeTags=catalog?.public_tags.filter(item=>item.status==='active').length||0;

  function openCategory(level:1|2,item?:VideoCategoryDefinition){
    setEditor({kind:'category',item,level});setMergeTargetId('');
    setDraft(item?{...emptyDraft,name:item.name,code:item.code,parent_id:item.parent_id,sort_order:item.sort_order,status:item.status}:{...emptyDraft,parent_id:level===2?selectedParentId:null,sort_order:level===1?parents.length:children.length});
  }
  function openGroup(item?:VideoTagGroupDefinition){
    setEditor({kind:'group',item});setMergeTargetId('');
    setDraft(item?{...emptyDraft,name:item.name,code:item.code,applies_to:item.applies_to,inherit_to_final:item.inherit_to_final,sort_order:item.sort_order,status:item.status}:{...emptyDraft,applies_to:'both',sort_order:groups.length});
  }
  function openTag(item?:VideoTaxonomyTag){
    setEditor({kind:'tag',item});setMergeTargetId('');
    setDraft(item?{...emptyDraft,name:item.name,code:item.code,group_id:item.group_id,applies_to:item.applies_to,sort_order:item.sort_order,status:item.status}:{...emptyDraft,group_id:selectedGroupId,applies_to:selectedGroup?.applies_to||'both',sort_order:groupTags.length});
  }
  async function submit(event:FormEvent){
    event.preventDefault();if(!editor||!draft.name.trim())return;
    setSaving(true);setError('');
    try{
      if(editor.kind==='category'){
        if(editor.item)await videoTaxonomyApi.updateCategory(editor.item.id,{name:draft.name,code:draft.code,parent_id:draft.parent_id,sort_order:draft.sort_order,status:draft.status});
        else await videoTaxonomyApi.createCategory({object_type:objectType,name:draft.name,code:draft.code,parent_id:draft.parent_id,sort_order:draft.sort_order});
      }else if(editor.kind==='group'){
        if(editor.item)await videoTaxonomyApi.updateGroup(editor.item.id,{name:draft.name,code:draft.code,applies_to:draft.applies_to,inherit_to_final:draft.inherit_to_final,sort_order:draft.sort_order,status:draft.status});
        else await videoTaxonomyApi.createGroup({name:draft.name,code:draft.code,applies_to:draft.applies_to,inherit_to_final:draft.inherit_to_final,sort_order:draft.sort_order});
      }else{
        if(!draft.group_id)throw new Error('请选择标签组');
        if(editor.item)await videoTaxonomyApi.updateTag(editor.item.id,{name:draft.name,code:draft.code,group_id:draft.group_id,applies_to:draft.applies_to,sort_order:draft.sort_order,status:draft.status});
        else await videoTaxonomyApi.createPublicTag({name:draft.name,code:draft.code,group_id:draft.group_id,applies_to:draft.applies_to,sort_order:draft.sort_order});
      }
      setEditor(null);await load();
    }catch(err:any){setError(errorMessage(err,err.message||'保存失败'))}finally{setSaving(false)}
  }
  async function removeItem(kind:EditorKind,item:VideoCategoryDefinition|VideoTagGroupDefinition|VideoTaxonomyTag,closeEditor=false){
    if(deleting||saving||reordering)return;
    const kindName=kind==='category'?'分类':kind==='group'?'标签组':'公共标签';
    if(!window.confirm(`确定删除${kindName} "${item.name}"? 此操作不可撤销.`))return;
    setDeleting({kind,id:item.id});setError('');
    try{
      if(kind==='category')await videoTaxonomyApi.deleteCategory(item.id);
      else if(kind==='group')await videoTaxonomyApi.deleteGroup(item.id);
      else await videoTaxonomyApi.deleteTag(item.id);
      if(closeEditor)setEditor(null);
      await load();
    }catch(err:any){setError(errorMessage(err,'删除失败'))}finally{setDeleting(null)}
  }
  const removeCurrent=()=>{if(editor?.item)void removeItem(editor.kind,editor.item,true)};
  async function mergeCurrent(){
    if(!editor?.item||!mergeTargetId)return;
    const targets=mergeTargets();const target=targets.find(item=>item.id===Number(mergeTargetId));
    if(!target||!window.confirm(`将 ${editor.item.name} 合并到 ${target.name}?`))return;
    setSaving(true);setError('');
    try{
      if(editor.kind==='category')await videoTaxonomyApi.mergeCategory(editor.item.id,target.id);
      else if(editor.kind==='tag')await videoTaxonomyApi.mergeTag(editor.item.id,target.id);
      setEditor(null);await load();
    }catch(err:any){setError(errorMessage(err,'合并失败'))}finally{setSaving(false)}
  }
  function mergeTargets(){
    if(!editor?.item)return[];
    if(editor.kind==='category'){
      const item=editor.item as VideoCategoryDefinition;
      return catalog?.categories.filter(candidate=>candidate.id!==item.id&&candidate.parent_id===item.parent_id&&candidate.status==='active')||[];
    }
    if(editor.kind==='tag')return catalog?.public_tags.filter(candidate=>candidate.id!==editor.item!.id&&candidate.status==='active')||[];
    return[];
  }

  function sortableItems(kind:SortKind):SortableEntity[]{
    if(!catalog)return[];
    if(kind==='parents')return catalog.categories.filter(item=>item.parent_id===null).sort(bySortOrder);
    if(kind==='children')return catalog.categories.filter(item=>item.parent_id===selectedParentId).sort(bySortOrder);
    if(kind==='groups')return [...catalog.groups].sort(bySortOrder);
    return catalog.public_tags.filter(item=>item.group_id===selectedGroupId).sort(bySortOrder);
  }
  async function reorder(kind:SortKind,sourceId:number,targetId:number){
    if(!catalog||sourceId===targetId||reordering)return;
    const items=sortableItems(kind);
    const sourceIndex=items.findIndex(item=>item.id===sourceId);
    const targetIndex=items.findIndex(item=>item.id===targetId);
    if(sourceIndex<0||targetIndex<0)return;
    const reordered=[...items];
    const [moved]=reordered.splice(sourceIndex,1);
    reordered.splice(targetIndex,0,moved);
    const orderById=new Map(reordered.map((item,index)=>[item.id,index]));
    const updates=reordered.filter(item=>item.sort_order!==orderById.get(item.id)).map(item=>({id:item.id,sort_order:orderById.get(item.id)!}));
    if(!updates.length)return;
    setReordering(kind);setError('');
    setCatalog(current=>{
      if(!current)return current;
      if(kind==='parents'||kind==='children')return {...current,categories:current.categories.map(item=>orderById.has(item.id)?{...item,sort_order:orderById.get(item.id)!}:item)};
      if(kind==='groups')return {...current,groups:current.groups.map(item=>orderById.has(item.id)?{...item,sort_order:orderById.get(item.id)!}:item)};
      return {...current,public_tags:current.public_tags.map(item=>orderById.has(item.id)?{...item,sort_order:orderById.get(item.id)!}:item)};
    });
    try{
      await Promise.all(updates.map(item=>{
        if(kind==='parents'||kind==='children')return videoTaxonomyApi.updateCategory(item.id,{sort_order:item.sort_order});
        if(kind==='groups')return videoTaxonomyApi.updateGroup(item.id,{sort_order:item.sort_order});
        return videoTaxonomyApi.updateTag(item.id,{sort_order:item.sort_order});
      }));
    }catch(err:any){
      const message=errorMessage(err,'排序保存失败');
      await load();setError(message);
    }finally{setReordering(null)}
  }
  async function move(kind:SortKind,itemId:number,direction:-1|1){
    const items=sortableItems(kind);
    const index=items.findIndex(item=>item.id===itemId);
    const target=items[index+direction];
    if(target)await reorder(kind,itemId,target.id);
  }

  if(access==='loading')return <div className="grid min-h-[50vh] place-items-center text-[var(--muted)]"><div className="flex items-center gap-2 text-sm"><Loader2 size={18} className="animate-spin"/>正在验证访问权限</div></div>;
  if(access==='denied')return <div className="mx-auto grid min-h-[50vh] max-w-xl place-items-center animate-fade-in"><div className="w-full border border-[var(--border)] bg-white p-8 text-center"><ShieldAlert className="mx-auto text-[var(--muted)]" size={28}/><h1 className="mt-4 font-display text-2xl">无权访问</h1><p className="mt-2 text-sm text-[var(--muted)]">只有管理员可以管理视频分类与公共标签.</p></div></div>;

  return <div className="mx-auto max-w-7xl space-y-6 animate-fade-in">
    <div><h1 className="text-2xl font-semibold">企业管理后台</h1><div className="mt-4 flex gap-6 border-b border-[var(--border)]"><Link className="pb-3 text-[var(--muted)]" to="/tenant-admin">用户</Link><Link className="pb-3 text-[var(--muted)]" to="/tenant-admin/roles">角色</Link><span className="border-b-2 border-[var(--accent)] pb-3 font-medium text-[var(--accent)]">分类与标签</span></div></div>
    <header className="flex flex-wrap items-end justify-between gap-4"><div><h2 className="font-display text-3xl">分类与标签</h2><p className="mt-2 text-sm text-[var(--muted)]">统一维护视频素材和成片使用的两级分类与公共标签。</p></div><label className="flex items-center gap-2 text-xs text-[var(--muted)]"><input type="checkbox" checked={showInactive} onChange={event=>setShowInactive(event.target.checked)}/>显示已停用项</label></header>
    {error&&<div className="flex items-start justify-between border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700" role="alert"><span>{error}</span><button onClick={()=>setError('')} aria-label="关闭错误"><X size={16}/></button></div>}
    <div className="inline-flex border border-[var(--border)] bg-white p-1" role="tablist" aria-label="管理对象"><button role="tab" aria-selected={objectType==='material'} className={`min-h-10 px-5 text-sm ${objectType==='material'?'bg-black text-white':'text-[var(--muted)] hover:text-black'}`} onClick={()=>setObjectType('material')}>视频素材</button><button role="tab" aria-selected={objectType==='final_output'} className={`min-h-10 px-5 text-sm ${objectType==='final_output'?'bg-black text-white':'text-[var(--muted)] hover:text-black'}`} onClick={()=>setObjectType('final_output')}>成片</button></div>
    {loading||!catalog?<div className="grid h-64 place-items-center text-[var(--muted)]"><Loader2 className="animate-spin"/></div>:<>
      <section className="grid grid-cols-3 border border-[var(--border)] bg-white"><div className="border-r border-[var(--border)] p-4"><div className="text-xs text-[var(--muted)]">启用分类</div><div className="mt-2 font-display text-2xl">{activeCategories}</div></div><div className="border-r border-[var(--border)] p-4"><div className="text-xs text-[var(--muted)]">公共标签</div><div className="mt-2 font-display text-2xl">{activeTags}</div></div><div className="p-4"><div className="text-xs text-[var(--muted)]">当前对象</div><div className="mt-2 text-sm font-medium">{objectType==='material'?'视频素材':'成片'}</div></div></section>
      <section className="panel overflow-hidden">
        <div className="panel-header"><div><h2 className="flex items-center gap-2 font-medium"><FolderTree size={17}/>分类结构</h2><p className="mt-1 text-xs text-[var(--muted)]">分类最多两级, 每条视频只能使用一条分类路径.</p></div></div>
        <div className="grid gap-5 p-5 lg:grid-cols-2">
          <TaxonomyList
            title="一级分类"
            items={parents}
            selectedId={selectedParentId}
            emptyText="暂无一级分类"
            meta={item=>`${item.child_count} 个二级`}
            busy={reordering==='parents'}
            onAdd={()=>openCategory(1)}
            onSelect={item=>setSelectedParentId(item.id)}
            onEdit={item=>openCategory(1,item)}
            onDelete={item=>void removeItem('category',item)}
            deleteBusy={deleting!==null}
            deletingId={deleting?.kind==='category'?deleting.id:null}
            onReorder={(sourceId,targetId)=>reorder('parents',sourceId,targetId)}
            onMove={(itemId,direction)=>move('parents',itemId,direction)}
          />
          <TaxonomyList
            key={`children-${selectedParentId}`}
            title="二级分类"
            items={children}
            emptyText={selectedParentId?'暂无二级分类':'请先选择一级分类'}
            meta={item=>`使用 ${item.usage_count} 次`}
            busy={reordering==='children'}
            onAdd={selectedParentId?()=>openCategory(2):undefined}
            onEdit={item=>openCategory(2,item)}
            onDelete={item=>void removeItem('category',item)}
            deleteBusy={deleting!==null}
            deletingId={deleting?.kind==='category'?deleting.id:null}
            onReorder={(sourceId,targetId)=>reorder('children',sourceId,targetId)}
            onMove={(itemId,direction)=>move('children',itemId,direction)}
          />
        </div>
      </section>
      <section className="panel overflow-hidden">
        <div className="panel-header"><div><h2 className="flex items-center gap-2 font-medium"><Tags size={17}/>公共标签</h2><p className="mt-1 text-xs text-[var(--muted)]">公共标签支持多选, 默认同时适用于视频素材和成片.</p></div></div>
        <div className="grid items-start gap-5 p-5 lg:grid-cols-2">
          <TaxonomyList
            title="标签组"
            items={groups}
            selectedId={selectedGroupId}
            emptyText="暂无标签组"
            meta={item=>`${item.tag_count} 个标签`}
            busy={reordering==='groups'}
            onAdd={()=>openGroup()}
            onSelect={item=>setSelectedGroupId(item.id)}
            onEdit={item=>openGroup(item)}
            onDelete={item=>void removeItem('group',item)}
            deleteBusy={deleting!==null}
            deletingId={deleting?.kind==='group'?deleting.id:null}
            onReorder={(sourceId,targetId)=>reorder('groups',sourceId,targetId)}
            onMove={(itemId,direction)=>move('groups',itemId,direction)}
          />
          <div>
            <TaxonomyList
              key={`tags-${selectedGroupId}`}
              title="公共标签"
              items={groupTags}
              emptyText={selectedGroupId?'暂无公共标签':'请先选择标签组'}
              meta={item=>`使用 ${item.usage_count} 次`}
              busy={reordering==='tags'}
              onAdd={selectedGroupId?()=>openTag():undefined}
              onEdit={item=>openTag(item)}
              onDelete={item=>void removeItem('tag',item)}
              deleteBusy={deleting!==null}
              deletingId={deleting?.kind==='tag'?deleting.id:null}
              onReorder={(sourceId,targetId)=>reorder('tags',sourceId,targetId)}
              onMove={(itemId,direction)=>move('tags',itemId,direction)}
            />
            {selectedGroup&&<div className="mt-3 flex flex-wrap gap-x-5 gap-y-2 border border-[var(--border)] bg-[var(--bg-elevated)] px-3 py-2.5 text-xs text-[var(--muted)]"><span>适用范围 {appliesToText[selectedGroup.applies_to]}</span><span>成片继承 {selectedGroup.inherit_to_final?'开启':'关闭'}</span><span>使用 {selectedGroup.usage_count} 次</span></div>}
          </div>
        </div>
      </section>
    </>}
    {editor&&<div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget)setEditor(null)}}><form className="panel w-full max-w-lg bg-white p-6" onSubmit={submit}><div className="mb-5 flex items-center justify-between"><div><h2 className="text-lg font-medium">{editor.item?'编辑':'新建'}{editor.kind==='category'?'分类':editor.kind==='group'?'标签组':'公共标签'}</h2>{editor.item&&<p className="mt-1 text-xs text-[var(--muted)]">当前使用 {editor.item.usage_count??0} 次</p>}</div><button type="button" className="p-2" onClick={()=>setEditor(null)} aria-label="关闭"><X size={18}/></button></div><div className="space-y-4"><label className="field"><span>名称</span><input autoFocus required value={draft.name} onChange={event=>setDraft({...draft,name:event.target.value})}/></label><label className="field"><span>编码</span><input value={draft.code} onChange={event=>setDraft({...draft,code:event.target.value})} placeholder="留空自动生成"/></label>{editor.kind==='category'&&editor.level===2&&<label className="field"><span>所属一级分类</span><select className="input-select" value={draft.parent_id||''} onChange={event=>setDraft({...draft,parent_id:Number(event.target.value)||null})}>{catalog?.categories.filter(item=>item.parent_id===null).map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}{editor.kind==='tag'&&<label className="field"><span>标签组</span><select className="input-select" value={draft.group_id||''} onChange={event=>setDraft({...draft,group_id:Number(event.target.value)||null})}><option value="">请选择标签组</option>{catalog?.groups.filter(item=>item.status==='active').map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}{editor.kind!=='category'&&<label className="field"><span>适用范围</span><select className="input-select" value={draft.applies_to} onChange={event=>setDraft({...draft,applies_to:event.target.value as VideoTagAppliesTo})}><option value="both">视频素材和成片</option><option value="material">仅视频素材</option><option value="final_output">仅成片</option></select></label>}{editor.kind==='group'&&<label className="flex min-h-11 items-center gap-3 border border-[var(--border)] px-3 text-sm"><input type="checkbox" checked={draft.inherit_to_final} onChange={event=>setDraft({...draft,inherit_to_final:event.target.checked})}/><span>允许该组公共标签继承到成片</span></label>}<div className="grid grid-cols-2 gap-3"><label className="field"><span>排序</span><input type="number" value={draft.sort_order} onChange={event=>setDraft({...draft,sort_order:Number(event.target.value)})}/></label>{editor.item&&<label className="field"><span>状态</span><select className="input-select" value={draft.status} onChange={event=>setDraft({...draft,status:event.target.value as 'active'|'inactive'})}><option value="active">启用</option><option value="inactive">停用</option></select></label>}</div>{editor.item&&editor.kind!=='group'&&mergeTargets().length>0&&<div className="border-t border-[var(--border)] pt-4"><label className="field"><span>合并到</span><div className="flex gap-2"><select className="input-select" value={mergeTargetId} onChange={event=>setMergeTargetId(event.target.value)}><option value="">请选择目标</option>{mergeTargets().map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select><button type="button" className="btn-secondary shrink-0" disabled={!mergeTargetId||saving} onClick={mergeCurrent}><ChevronRight size={14}/>合并</button></div></label></div>}</div><div className="mt-6 flex items-center justify-between gap-3"><div>{editor.item&&<button type="button" className="btn-danger" onClick={removeCurrent} disabled={saving||deleting!==null}>{deleting?.kind===editor.kind&&deleting.id===editor.item.id?<Loader2 size={14} className="animate-spin"/>:<Trash2 size={14}/>}删除</button>}</div><div className="flex gap-2"><button type="button" className="btn-secondary" onClick={()=>setEditor(null)}>取消</button><button className="btn-primary" disabled={saving||deleting!==null||!draft.name.trim()}>{saving?<Loader2 size={14} className="animate-spin"/>:<Check size={14}/>}保存</button></div></div></form></div>}
  </div>;
}
