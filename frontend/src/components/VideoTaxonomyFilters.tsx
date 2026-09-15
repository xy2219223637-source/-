import {useEffect,useMemo,useRef,useState} from 'react';
import {BookmarkPlus,Check,Loader2,Pencil,Plus,RotateCcw,Save,Search,Trash2,X} from 'lucide-react';
import {videoTaxonomyApi} from '../api/client';
import type {VideoLibraryAsset,VideoObjectType,VideoTaxonomyCatalog,VideoTaxonomyFilterValue,VideoTaxonomyTag} from '../types';

type FilterProps={
  objectType:VideoObjectType;
  catalog:VideoTaxonomyCatalog;
  value:VideoTaxonomyFilterValue;
  onChange:(value:VideoTaxonomyFilterValue)=>void;
  onRefresh:()=>Promise<void>;
  presetFilters?:Record<string,unknown>;
  onApplyPreset?:(filters:Record<string,unknown>)=>void;
};

const selectedClass='border-black bg-black text-white';
const idleClass='border-[var(--border)] bg-white text-[var(--text)] hover:border-black';

function numberList(value:unknown):number[]{
  return Array.isArray(value)?value.filter((item):item is number=>typeof item==='number'):[];
}

export function matchesVideoTaxonomy(asset:VideoLibraryAsset,catalog:VideoTaxonomyCatalog,value:VideoTaxonomyFilterValue){
  if(value.categoryId!==null){
    const selected=catalog.categories.find(item=>item.id===value.categoryId);
    if(!selected)return false;
    const categoryMatches=selected.parent_id===null
      ? asset.category_id===selected.id||asset.category?.parent_id===selected.id
      : asset.category_id===selected.id;
    if(!categoryMatches)return false;
  }
  const assigned=new Set(asset.taxonomy_tags.map(tag=>tag.id));
  if(value.publicTagIds.length){
    const publicTagsMatch=value.publicTagMatchMode==='all'
      ? value.publicTagIds.every(tagId=>assigned.has(tagId))
      : value.publicTagIds.some(tagId=>assigned.has(tagId));
    if(!publicTagsMatch)return false;
  }
  if(value.personalTagIds.length&&!value.personalTagIds.some(tagId=>assigned.has(tagId)))return false;
  return true;
}

function TagButton({tag,selected,onClick}:{tag:VideoTaxonomyTag;selected:boolean;onClick:()=>void}){
  return <button type="button" aria-pressed={selected} onClick={onClick} className={`min-h-9 border px-3 py-1.5 text-xs transition-colors ${selected?selectedClass:idleClass}`}>
    <span className="inline-flex items-center gap-1.5">{selected&&<Check size={12}/>}<span>{tag.name}</span><span className={selected?'text-white/65':'text-[var(--muted)]'}>{tag.usage_count}</span></span>
  </button>;
}

export function VideoTaxonomyFilters({objectType,catalog,value,onChange,onRefresh,presetFilters={},onApplyPreset}:FilterProps){
  const [search,setSearch]=useState('');
  const [activePublicGroupId,setActivePublicGroupId]=useState<number|null>(null);
  const publicGroupCloseTimer=useRef<number|null>(null);
  const [presetId,setPresetId]=useState('');
  const [presetName,setPresetName]=useState('');
  const [savingPreset,setSavingPreset]=useState(false);
  const [addingPersonal,setAddingPersonal]=useState(false);
  const [personalName,setPersonalName]=useState('');
  const [editingPersonalId,setEditingPersonalId]=useState<number|null>(null);
  const [editingPersonalName,setEditingPersonalName]=useState('');
  const [busy,setBusy]=useState('');
  const [error,setError]=useState('');
  const parents=useMemo(()=>catalog.categories.filter(item=>item.parent_id===null),[catalog]);
  const selectedCategory=catalog.categories.find(item=>item.id===value.categoryId);
  const selectedParentId=selectedCategory?.parent_id??selectedCategory?.id??null;
  const children=selectedParentId===null?[]:catalog.categories.filter(item=>item.parent_id===selectedParentId);
  const normalizedSearch=search.trim().toLowerCase();
  const publicGroups=useMemo(()=>catalog.groups.map(group=>({
    ...group,
    tags:catalog.public_tags.filter(tag=>tag.group_id===group.id),
  })).filter(group=>group.tags.length),[catalog]);
  const activePublicGroup=publicGroups.find(group=>group.id===activePublicGroupId)??null;
  const visiblePublicTags=activePublicGroup?.tags.filter(tag=>!normalizedSearch||tag.name.toLowerCase().includes(normalizedSearch))??[];
  const selectedPublicTags=useMemo(()=>value.publicTagIds
    .map(tagId=>catalog.public_tags.find(tag=>tag.id===tagId))
    .filter((tag):tag is VideoTaxonomyTag=>Boolean(tag)),[catalog.public_tags,value.publicTagIds]);

  useEffect(()=>()=>{
    if(publicGroupCloseTimer.current!==null)window.clearTimeout(publicGroupCloseTimer.current);
  },[]);

  const cancelPublicGroupClose=()=>{
    if(publicGroupCloseTimer.current===null)return;
    window.clearTimeout(publicGroupCloseTimer.current);
    publicGroupCloseTimer.current=null;
  };
  const closePublicGroup=()=>{
    cancelPublicGroupClose();
    publicGroupCloseTimer.current=window.setTimeout(()=>{
      setActivePublicGroupId(null);
      setSearch('');
      publicGroupCloseTimer.current=null;
    },180);
  };
  const openPublicGroup=(groupId:number)=>{
    cancelPublicGroupClose();
    if(activePublicGroupId!==groupId)setSearch('');
    setActivePublicGroupId(groupId);
  };

  const toggle=(key:'publicTagIds'|'personalTagIds',tagId:number)=>{
    const current=value[key];
    onChange({...value,[key]:current.includes(tagId)?current.filter(id=>id!==tagId):[...current,tagId]});
  };
  const reset=()=>onChange({categoryId:null,publicTagIds:[],publicTagMatchMode:'any',personalTagIds:[]});
  const activeCount=(value.categoryId===null?0:1)+value.publicTagIds.length+value.personalTagIds.length;

  async function savePreset(){
    const name=presetName.trim();if(!name)return;
    setBusy('preset');setError('');
    try{
      await videoTaxonomyApi.createPreset({object_type:objectType,name,filters:{...presetFilters,category_id:value.categoryId,public_tag_ids:value.publicTagIds,public_tag_match_mode:value.publicTagMatchMode,personal_tag_ids:value.personalTagIds}});
      setPresetName('');setSavingPreset(false);await onRefresh();
    }catch(err:any){setError(err.response?.data?.detail||'筛选预设保存失败')}finally{setBusy('')}
  }
  function applyPreset(id:string){
    setPresetId(id);
    const preset=catalog.presets.find(item=>String(item.id)===id);if(!preset)return;
    const filters=preset.filters;
    onChange({
      categoryId:typeof filters.category_id==='number'?filters.category_id:null,
      publicTagIds:numberList(filters.public_tag_ids),
      publicTagMatchMode:filters.public_tag_match_mode==='all'?'all':'any',
      personalTagIds:numberList(filters.personal_tag_ids),
    });
    onApplyPreset?.(filters);
  }
  async function deletePreset(){
    if(!presetId)return;
    setBusy('preset-delete');setError('');
    try{await videoTaxonomyApi.deletePreset(Number(presetId));setPresetId('');await onRefresh()}catch(err:any){setError(err.response?.data?.detail||'筛选预设删除失败')}finally{setBusy('')}
  }
  async function createPersonal(){
    const name=personalName.trim();if(!name)return;
    setBusy('personal-create');setError('');
    try{
      const tag=await videoTaxonomyApi.createPersonalTag({name,applies_to:'both'});
      onChange({...value,personalTagIds:[...value.personalTagIds,tag.id]});
      setPersonalName('');setAddingPersonal(false);await onRefresh();
    }catch(err:any){setError(err.response?.data?.detail||'个人标签创建失败')}finally{setBusy('')}
  }
  async function updatePersonal(tagId:number){
    const name=editingPersonalName.trim();if(!name)return;
    setBusy(`personal-${tagId}`);setError('');
    try{await videoTaxonomyApi.updateTag(tagId,{name});setEditingPersonalId(null);await onRefresh()}catch(err:any){setError(err.response?.data?.detail||'个人标签更新失败')}finally{setBusy('')}
  }
  async function deletePersonal(tagId:number){
    if(!window.confirm('删除这个个人标签及其全部绑定关系?'))return;
    setBusy(`personal-${tagId}`);setError('');
    try{await videoTaxonomyApi.deleteTag(tagId);onChange({...value,personalTagIds:value.personalTagIds.filter(id=>id!==tagId)});await onRefresh()}catch(err:any){setError(err.response?.data?.detail||'个人标签删除失败')}finally{setBusy('')}
  }

  return <section className="panel overflow-visible">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3">
      <div><h2 className="text-sm font-medium">分类与标签</h2><p className="mt-1 text-[11px] text-[var(--muted)]">公共标签支持跨标签组多选</p></div>
      <div className="flex flex-wrap items-center gap-2">
        <select className="input-select min-w-44" value={presetId} onChange={event=>applyPreset(event.target.value)} aria-label="常用筛选预设"><option value="">选择常用筛选预设</option>{catalog.presets.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select>
        {savingPreset?<><input autoFocus className="h-10 min-w-36 border border-[var(--border)] bg-white px-3 text-sm outline-none focus:border-black" value={presetName} onChange={event=>setPresetName(event.target.value)} onKeyDown={event=>{if(event.key==='Enter')void savePreset();if(event.key==='Escape')setSavingPreset(false)}} placeholder="预设名称"/><button className="btn-primary" onClick={savePreset} disabled={!presetName.trim()||busy==='preset'}>{busy==='preset'?<Loader2 size={14} className="animate-spin"/>:<Save size={14}/>}保存</button><button className="tax-icon-button p-2" onClick={()=>setSavingPreset(false)} aria-label="取消保存预设"><X size={16}/></button></>:<button className="btn-secondary" onClick={()=>setSavingPreset(true)}><BookmarkPlus size={14}/>保存预设</button>}
        {presetId&&<button className="p-2 text-[var(--muted)] hover:text-[var(--danger)]" onClick={deletePreset} aria-label="删除当前预设"><Trash2 size={16}/></button>}
        {activeCount>0&&<button className="btn-secondary" onClick={reset}><RotateCcw size={14}/>重置 {activeCount}</button>}
      </div>
    </div>
    {error&&<div className="border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-700" role="alert">{typeof error==='string'?error:'操作失败'}</div>}
    <div className="grid gap-3 border-b border-[var(--border)] px-4 py-4 lg:grid-cols-[104px_1fr]">
      <div className="pt-2 text-xs text-[var(--muted)]">一级分类</div><div className="flex flex-wrap gap-2"><button type="button" className={`min-h-9 border px-3 text-xs ${selectedParentId===null?selectedClass:idleClass}`} onClick={()=>onChange({...value,categoryId:null})}>全部</button>{parents.map(item=><button type="button" key={item.id} className={`min-h-9 border px-3 text-xs ${selectedParentId===item.id?selectedClass:idleClass}`} onClick={()=>onChange({...value,categoryId:item.id})}>{item.name}</button>)}</div>
    </div>
    <div className="grid gap-3 border-b border-[var(--border)] px-4 py-4 lg:grid-cols-[104px_1fr]">
      <div className="pt-2 text-xs text-[var(--muted)]">二级分类</div><div className="flex flex-wrap gap-2">{selectedParentId===null?<span className="py-2 text-xs text-[var(--muted)]">选择一级分类后显示</span>:<><button type="button" className={`min-h-9 border px-3 text-xs ${selectedCategory?.parent_id===null?selectedClass:idleClass}`} onClick={()=>onChange({...value,categoryId:selectedParentId})}>全部</button>{children.map(item=><button type="button" key={item.id} className={`min-h-9 border px-3 text-xs ${value.categoryId===item.id?selectedClass:idleClass}`} onClick={()=>onChange({...value,categoryId:item.id})}>{item.name}</button>)}</>}</div>
    </div>
    <div className="relative z-20 border-b border-[var(--border)] px-4 py-4">
      <div className="grid gap-3 lg:grid-cols-[104px_1fr]">
        <div className="pt-2 text-xs text-[var(--muted)]">公共标签</div>
        <div
          className="relative"
          onMouseEnter={cancelPublicGroupClose}
          onMouseLeave={closePublicGroup}
          onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node))closePublicGroup()}}
        >
          <div className="flex flex-wrap items-center gap-2">
            {publicGroups.map(group=>{
              const selectedCount=group.tags.filter(tag=>value.publicTagIds.includes(tag.id)).length;
              const open=activePublicGroupId===group.id;
              return <button
                key={group.id}
                type="button"
                aria-expanded={open}
                aria-haspopup="true"
                onMouseEnter={()=>openPublicGroup(group.id)}
                onFocus={()=>openPublicGroup(group.id)}
                onClick={()=>openPublicGroup(group.id)}
                className={`min-h-9 border px-3 text-xs transition-colors ${open?selectedClass:idleClass}`}
              >
                <span>{group.name}</span>
                {selectedCount>0&&<span className={`ml-2 ${open?'text-white/70':'text-[var(--muted)]'}`}>已选 {selectedCount}</span>}
              </button>;
            })}
            {value.publicTagIds.length>0&&<button
              type="button"
              className="inline-flex min-h-9 items-center gap-1.5 px-2 text-xs text-[var(--muted)] hover:text-black"
              onClick={()=>onChange({...value,publicTagIds:[]})}
            ><RotateCcw size={13}/>重置公共标签</button>}
          </div>
          {activePublicGroup&&<div
            className="absolute left-0 right-0 top-full z-30 pt-1"
            role="group"
            aria-label={`${activePublicGroup.name}公共标签`}
            onMouseEnter={cancelPublicGroupClose}
            onMouseLeave={closePublicGroup}
          >
            <div className="border border-[var(--border)] bg-white p-4 shadow-xl">
            <div className="flex items-center justify-between gap-3">
              <div><div className="text-sm font-medium">{activePublicGroup.name}</div><div className="mt-1 text-[11px] text-[var(--muted)]">可选择多个公共标签</div></div>
              <button type="button" className="p-2 text-[var(--muted)] hover:text-black" onClick={()=>{cancelPublicGroupClose();setActivePublicGroupId(null);setSearch('')}} aria-label="关闭公共标签"><X size={16}/></button>
            </div>
            <label className="relative mt-3 block max-w-sm">
              <Search size={14} className="pointer-events-none absolute left-3 top-3 text-[var(--muted)]"/>
              <input className="h-10 w-full border border-[var(--border)] bg-white pl-9 pr-3 text-sm outline-none focus:border-black" value={search} onChange={event=>setSearch(event.target.value)} placeholder={`搜索${activePublicGroup.name}标签`}/>
            </label>
            <div className="mt-3 flex flex-wrap gap-2">{visiblePublicTags.map(tag=><TagButton key={tag.id} tag={tag} selected={value.publicTagIds.includes(tag.id)} onClick={()=>toggle('publicTagIds',tag.id)}/>)}</div>
            {!visiblePublicTags.length&&<div className="mt-3 text-xs text-[var(--muted)]">没有匹配的公共标签</div>}
            </div>
          </div>}
        </div>
      </div>
    </div>
    <div className="grid gap-3 px-4 py-4 lg:grid-cols-[104px_1fr]">
      <div className="pt-2 text-xs text-[var(--muted)]">个人标签</div><div className="flex flex-wrap items-center gap-2">{catalog.personal_tags.map(tag=>editingPersonalId===tag.id?<div key={tag.id} className="flex items-center gap-1"><input autoFocus className="h-9 w-32 border border-black bg-white px-2 text-sm" value={editingPersonalName} onChange={event=>setEditingPersonalName(event.target.value)} onKeyDown={event=>{if(event.key==='Enter')void updatePersonal(tag.id);if(event.key==='Escape')setEditingPersonalId(null)}}/><button className="p-2" onClick={()=>updatePersonal(tag.id)} aria-label="保存个人标签"><Check size={15}/></button><button className="p-2" onClick={()=>setEditingPersonalId(null)} aria-label="取消编辑"><X size={15}/></button></div>:<div key={tag.id} className="flex items-center"><TagButton tag={tag} selected={value.personalTagIds.includes(tag.id)} onClick={()=>toggle('personalTagIds',tag.id)}/><button className="p-2 text-[var(--muted)] hover:text-black" onClick={()=>{setEditingPersonalId(tag.id);setEditingPersonalName(tag.name)}} aria-label={`编辑 ${tag.name}`}><Pencil size={13}/></button><button className="p-2 text-[var(--muted)] hover:text-[var(--danger)]" onClick={()=>deletePersonal(tag.id)} aria-label={`删除 ${tag.name}`}><Trash2 size={13}/></button></div>)}{addingPersonal?<div className="flex items-center gap-1"><input autoFocus className="h-9 w-36 border border-black bg-white px-2 text-sm" value={personalName} onChange={event=>setPersonalName(event.target.value)} onKeyDown={event=>{if(event.key==='Enter')void createPersonal();if(event.key==='Escape')setAddingPersonal(false)}} placeholder="个人标签名称"/><button className="p-2" onClick={createPersonal} disabled={!personalName.trim()} aria-label="保存个人标签">{busy==='personal-create'?<Loader2 size={15} className="animate-spin"/>:<Check size={15}/>}</button><button className="p-2" onClick={()=>setAddingPersonal(false)} aria-label="取消新建"><X size={15}/></button></div>:<button className="btn-secondary" onClick={()=>setAddingPersonal(true)}><Plus size={14}/>个人标签</button>}</div>
    </div>
    {selectedPublicTags.length>0&&<div className="flex flex-wrap items-center gap-2 border-t border-[var(--border)] bg-[var(--bg-elevated)] px-4 py-3" aria-live="polite">
      <span className="mr-1 text-xs text-[var(--muted)]">已选公共标签</span>
      <div className="inline-flex border border-[var(--border)] bg-white p-0.5" role="group" aria-label="公共标签匹配方式">
        {([['any','任意满足'],['all','全部满足']] as const).map(([mode,label])=><button
          key={mode}
          type="button"
          aria-pressed={value.publicTagMatchMode===mode}
          className={`min-h-8 px-3 text-xs transition-colors ${value.publicTagMatchMode===mode?'bg-black text-white':'text-[var(--muted)] hover:text-black'}`}
          onClick={()=>onChange({...value,publicTagMatchMode:mode})}
        >{label}</button>)}
      </div>
      {selectedPublicTags.map(tag=>{
        const groupName=catalog.groups.find(group=>group.id===tag.group_id)?.name;
        return <span key={tag.id} className="inline-flex min-h-8 items-center gap-1.5 border border-[var(--border)] bg-white pl-3 pr-1 text-xs">
          <span>{groupName?`${groupName}: `:''}{tag.name}</span>
          <button type="button" className="grid h-7 w-7 place-items-center text-[var(--muted)] hover:text-black" onClick={()=>toggle('publicTagIds',tag.id)} aria-label={`移除 ${tag.name}`}><X size={13}/></button>
        </span>;
      })}
      <button type="button" className="ml-auto inline-flex min-h-8 items-center gap-1.5 px-2 text-xs text-[var(--muted)] hover:text-black" onClick={()=>onChange({...value,publicTagIds:[]})}><RotateCcw size={13}/>清空公共标签</button>
    </div>}
  </section>;
}

type BatchProps={catalog:VideoTaxonomyCatalog;selectedIds:number[];onClear:()=>void;onDone:()=>Promise<void>};

export function VideoBatchTaxonomyBar({catalog,selectedIds,onClear,onDone}:BatchProps){
  const [categoryAction,setCategoryAction]=useState('no-change');
  const [tagId,setTagId]=useState('');
  const [busy,setBusy]=useState('');
  const [error,setError]=useState('');
  if(!selectedIds.length)return null;
  const parents=catalog.categories.filter(item=>item.parent_id===null);
  async function update(data:{set_category?:boolean;category_id?:number|null;add_tag_ids?:number[];remove_tag_ids?:number[]},key:string){
    setBusy(key);setError('');
    try{await videoTaxonomyApi.updateAssets({asset_ids:selectedIds,...data});await onDone()}catch(err:any){const detail=err.response?.data?.detail;setError(typeof detail==='string'?detail:detail?.message||'批量操作失败')}finally{setBusy('')}
  }
  return <section className="panel border-black p-4">
    <div className="flex flex-wrap items-end gap-3"><div className="mr-auto"><div className="text-sm font-medium">已选择 {selectedIds.length} 条</div><button className="mt-1 text-xs text-[var(--muted)] underline" onClick={onClear}>取消选择</button></div><label className="field min-w-52"><span>修改分类</span><select className="input-select" value={categoryAction} onChange={event=>setCategoryAction(event.target.value)}><option value="no-change">选择分类</option><option value="clear">清除分类</option>{parents.map(parent=><optgroup key={parent.id} label={parent.name}><option value={parent.id}>{parent.name} / 全部</option>{catalog.categories.filter(item=>item.parent_id===parent.id).map(item=><option key={item.id} value={item.id}>{parent.name} / {item.name}</option>)}</optgroup>)}</select></label><button className="btn-secondary" disabled={categoryAction==='no-change'||!!busy} onClick={()=>update({set_category:true,category_id:categoryAction==='clear'?null:Number(categoryAction)},'category')}>{busy==='category'?<Loader2 size={14} className="animate-spin"/>:<Save size={14}/>}应用分类</button><label className="field min-w-48"><span>公共或个人标签</span><select className="input-select" value={tagId} onChange={event=>setTagId(event.target.value)}><option value="">选择标签</option>{catalog.groups.map(group=><optgroup key={group.id} label={group.name}>{catalog.public_tags.filter(tag=>tag.group_id===group.id).map(tag=><option key={tag.id} value={tag.id}>{tag.name}</option>)}</optgroup>)}{catalog.personal_tags.length>0&&<optgroup label="个人标签">{catalog.personal_tags.map(tag=><option key={tag.id} value={tag.id}>{tag.name}</option>)}</optgroup>}</select></label><button className="btn-primary" disabled={!tagId||!!busy} onClick={()=>update({add_tag_ids:[Number(tagId)]},'add')}>{busy==='add'?<Loader2 size={14} className="animate-spin"/>:<Plus size={14}/>}添加</button><button className="btn-secondary" disabled={!tagId||!!busy} onClick={()=>update({remove_tag_ids:[Number(tagId)]},'remove')}>{busy==='remove'?<Loader2 size={14} className="animate-spin"/>:<X size={14}/>}移除</button></div>{error&&<div className="mt-3 text-xs text-red-700" role="alert">{error}</div>}
  </section>;
}
