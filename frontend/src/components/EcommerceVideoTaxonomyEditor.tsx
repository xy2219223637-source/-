import {useMemo,useState} from 'react';
import {Check,Loader2,Pencil,Plus,X} from 'lucide-react';
import {ecommerceVideoTaxonomyApi} from '../api/client';
import type {VideoLibraryAsset,VideoObjectType,VideoTagAssignment,VideoTaxonomyCatalog,VideoTaxonomyTag} from '../types';

type SummaryProps={
  asset:VideoLibraryAsset;
  compact?:boolean;
  showAiTags?:boolean;
  onEdit?:()=>void;
};

function assignmentClass(tag:VideoTagAssignment){
  if(tag.status==='inactive')return'border-gray-300 bg-gray-100 text-gray-500';
  return tag.scope==='personal'
    ?'border-amber-200 bg-amber-50 text-amber-800'
    :'border-emerald-200 bg-emerald-50 text-emerald-800';
}

function AssignmentChip({tag,removable=false,onRemove}:{tag:VideoTagAssignment;removable?:boolean;onRemove?:()=>void}){
  return <span className={`inline-flex min-h-7 items-center gap-1 border px-2 py-1 text-[11px] ${assignmentClass(tag)}`}>
    <span>{tag.name}{tag.source==='inherited'?' - 继承':''}{tag.status==='inactive'?' - 已停用':''}</span>
    {removable&&<button type="button" className="grid h-5 w-5 place-items-center" onClick={onRemove} aria-label={`移除 ${tag.name}`}><X size={12}/></button>}
  </span>;
}

function SummaryRow({label,empty,children,compact}:{label:string;empty:boolean;children:React.ReactNode;compact:boolean}){
  if(compact&&empty)return null;
  return <div className={`grid gap-2 ${compact?'grid-cols-[64px_1fr]':'sm:grid-cols-[92px_1fr]'}`}>
    <div className="pt-1 text-xs text-[var(--muted)]">{label}</div>
    <div className="flex min-h-7 flex-wrap items-center gap-1.5">{empty?<span className="text-xs text-[var(--muted)]">未设置</span>:children}</div>
  </div>;
}

export function VideoTaxonomySummary({asset,compact=false,showAiTags=true,onEdit}:SummaryProps){
  return <div className={compact?'space-y-2':'space-y-3'}>
    {!compact&&<div className="flex items-center justify-between gap-3"><h3 className="text-sm font-medium">分类与标签</h3>{onEdit&&<button type="button" className="btn-secondary" onClick={onEdit}><Pencil size={14}/>编辑</button>}</div>}
    <SummaryRow compact={compact} label="分类" empty={!asset.category}>
      {asset.category&&<span className={`border px-2 py-1 text-[11px] ${asset.category.status==='inactive'?'border-gray-300 bg-gray-100 text-gray-500':'border-blue-200 bg-blue-50 text-blue-800'}`}>{asset.category.path.join(' / ')}{asset.category.status==='inactive'?' - 已停用':''}</span>}
    </SummaryRow>
    <SummaryRow compact={compact} label="公共标签" empty={!asset.public_tags.length}>
      {asset.public_tags.map(tag=><AssignmentChip key={tag.id} tag={tag}/>)}
    </SummaryRow>
    <SummaryRow compact={compact} label="个人标签" empty={!asset.personal_tags.length}>
      {asset.personal_tags.map(tag=><AssignmentChip key={tag.id} tag={tag}/>)}
    </SummaryRow>
    {showAiTags&&<SummaryRow compact={compact} label="AI 内容标签" empty={!asset.effective_tags.length}>
      {asset.effective_tags.map(tag=><span key={tag} className="border border-[var(--border)] bg-[#f6f5f2] px-2 py-1 text-[11px]">{tag}</span>)}
    </SummaryRow>}
    {compact&&onEdit&&<button type="button" className="mt-1 inline-flex items-center gap-1 text-xs text-[var(--muted)] hover:text-black" onClick={onEdit}><Pencil size={12}/>编辑分类与标签</button>}
  </div>;
}

type EditorProps={
  asset:VideoLibraryAsset;
  catalog:VideoTaxonomyCatalog;
  objectType:VideoObjectType;
  onClose:()=>void;
  onSaved:()=>Promise<void>;
  onCatalogRefresh?:()=>Promise<void>;
};

function toggleId(values:number[],id:number){
  return values.includes(id)?values.filter(value=>value!==id):[...values,id];
}

function errorMessage(error:any){
  const detail=error?.response?.data?.detail;
  return typeof detail==='string'?detail:detail?.message||'分类与标签保存失败';
}

export function VideoTaxonomyEditor({asset,catalog,objectType,onClose,onSaved,onCatalogRefresh}:EditorProps){
  const [workingCatalog,setWorkingCatalog]=useState(catalog);
  const [categoryId,setCategoryId]=useState(asset.category_id===null?'':String(asset.category_id));
  const [selectedTagIds,setSelectedTagIds]=useState(asset.taxonomy_tags.map(tag=>tag.id));
  const [personalName,setPersonalName]=useState('');
  const [addingPersonal,setAddingPersonal]=useState(false);
  const [busy,setBusy]=useState('');
  const [error,setError]=useState('');

  const parents=useMemo(()=>workingCatalog.categories.filter(item=>item.parent_id===null&&item.status==='active'),[workingCatalog.categories]);
  const inactiveAssignments=asset.taxonomy_tags.filter(tag=>tag.status==='inactive');
  const activePublicGroups=workingCatalog.groups.filter(group=>group.status==='active');

  function toggleTag(tag:VideoTaxonomyTag){
    if(tag.status!=='active')return;
    setSelectedTagIds(current=>toggleId(current,tag.id));
  }

  async function createPersonal(){
    const name=personalName.trim();
    if(!name)return;
    setBusy('personal');setError('');
    try{
      const tag=await ecommerceVideoTaxonomyApi.createPersonalTag({name,applies_to:objectType});
      setWorkingCatalog(current=>({...current,personal_tags:[...current.personal_tags,tag]}));
      setSelectedTagIds(current=>[...current,tag.id]);
      setPersonalName('');setAddingPersonal(false);
      await onCatalogRefresh?.();
    }catch(errorValue){setError(errorMessage(errorValue))}finally{setBusy('')}
  }

  async function save(){
    const originalTagIds=asset.taxonomy_tags.map(tag=>tag.id);
    const addTagIds=selectedTagIds.filter(id=>!originalTagIds.includes(id));
    const removeTagIds=originalTagIds.filter(id=>!selectedTagIds.includes(id));
    const nextCategoryId=categoryId?Number(categoryId):null;
    const categoryChanged=nextCategoryId!==asset.category_id;
    if(!categoryChanged&&!addTagIds.length&&!removeTagIds.length){onClose();return}
    setBusy('save');setError('');
    try{
      await ecommerceVideoTaxonomyApi.updateAssets({
        asset_ids:[asset.id],
        ...(categoryChanged?{set_category:true,category_id:nextCategoryId}:{}),
        add_tag_ids:addTagIds,
        remove_tag_ids:removeTagIds,
      });
      await onSaved();
      onClose();
    }catch(errorValue){setError(errorMessage(errorValue))}finally{setBusy('')}
  }

  return <div className="fixed inset-0 z-50 grid place-items-center bg-black/45 p-4" role="presentation" onMouseDown={event=>{if(event.target===event.currentTarget&&!busy)onClose()}}>
    <div className="panel max-h-[90vh] w-full max-w-4xl overflow-hidden bg-white" role="dialog" aria-modal="true" aria-labelledby="taxonomy-editor-title">
      <div className="flex items-center justify-between border-b border-[var(--border)] px-5 py-4"><div><h2 id="taxonomy-editor-title" className="text-lg font-medium">编辑分类与标签</h2><p className="mt-1 text-xs text-[var(--muted)]">一次确认后统一保存本条视频的分类与标签.</p></div><button type="button" className="p-2 text-[var(--muted)] hover:text-black" onClick={onClose} disabled={!!busy} aria-label="关闭"><X size={18}/></button></div>
      <div className="max-h-[calc(90vh-142px)] overflow-y-auto p-5">
        {error&&<div className="mb-4 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{error}</div>}
        <section>
          <h3 className="text-sm font-medium">二级分类</h3>
          <select className="input-select mt-3 w-full" value={categoryId} onChange={event=>setCategoryId(event.target.value)}>
            <option value="">未设置分类</option>
            {asset.category?.status==='inactive'&&<option value={asset.category.id} disabled>{asset.category.path.join(' / ')} - 已停用</option>}
            {parents.map(parent=><optgroup key={parent.id} label={parent.name}>{workingCatalog.categories.filter(item=>item.parent_id===parent.id&&item.status==='active').map(item=><option key={item.id} value={item.id}>{parent.name} / {item.name}</option>)}</optgroup>)}
          </select>
        </section>

        <section className="mt-6 border-t border-[var(--border)] pt-5">
          <h3 className="text-sm font-medium">公共标签</h3>
          <div className="mt-3 space-y-4">{activePublicGroups.map(group=>{
            const tags=workingCatalog.public_tags.filter(tag=>tag.group_id===group.id&&tag.status==='active');
            if(!tags.length)return null;
            return <div key={group.id} className="grid gap-2 sm:grid-cols-[120px_1fr]"><div className="pt-2 text-xs text-[var(--muted)]">{group.name}</div><div className="flex flex-wrap gap-2">{tags.map(tag=><label key={tag.id} className={`inline-flex min-h-9 cursor-pointer items-center gap-2 border px-3 py-1.5 text-xs ${selectedTagIds.includes(tag.id)?'border-black bg-black text-white':'border-[var(--border)] bg-white hover:border-black'}`}><input className="sr-only" type="checkbox" checked={selectedTagIds.includes(tag.id)} onChange={()=>toggleTag(tag)}/>{selectedTagIds.includes(tag.id)&&<Check size={12}/>}<span>{tag.name}</span></label>)}</div></div>;
          })}</div>
        </section>

        <section className="mt-6 border-t border-[var(--border)] pt-5">
          <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-medium">个人标签</h3>{addingPersonal?<div className="flex items-center gap-1"><input autoFocus className="h-9 w-40 border border-black px-2 text-sm" value={personalName} onChange={event=>setPersonalName(event.target.value)} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();void createPersonal()}if(event.key==='Escape')setAddingPersonal(false)}} placeholder="个人标签名称"/><button type="button" className="grid h-9 w-9 place-items-center" onClick={()=>void createPersonal()} disabled={!personalName.trim()||!!busy} aria-label="保存个人标签">{busy==='personal'?<Loader2 className="animate-spin" size={15}/>:<Check size={15}/>}</button><button type="button" className="grid h-9 w-9 place-items-center" onClick={()=>setAddingPersonal(false)} aria-label="取消"><X size={15}/></button></div>:<button type="button" className="btn-secondary" onClick={()=>setAddingPersonal(true)}><Plus size={14}/>新建个人标签</button>}</div>
          <div className="mt-3 flex flex-wrap gap-2">{workingCatalog.personal_tags.filter(tag=>tag.status==='active').map(tag=><label key={tag.id} className={`inline-flex min-h-9 cursor-pointer items-center gap-2 border px-3 py-1.5 text-xs ${selectedTagIds.includes(tag.id)?'border-black bg-black text-white':'border-[var(--border)] bg-white hover:border-black'}`}><input className="sr-only" type="checkbox" checked={selectedTagIds.includes(tag.id)} onChange={()=>toggleTag(tag)}/>{selectedTagIds.includes(tag.id)&&<Check size={12}/>}<span>{tag.name}</span></label>)}{!workingCatalog.personal_tags.some(tag=>tag.status==='active')&&<span className="py-2 text-xs text-[var(--muted)]">暂无个人标签</span>}</div>
        </section>

        {inactiveAssignments.length>0&&<section className="mt-6 border-t border-[var(--border)] pt-5"><h3 className="text-sm font-medium">已停用的历史标签</h3><p className="mt-1 text-xs text-[var(--muted)]">历史标签保留展示, 可以从当前视频移除, 不能重新添加.</p><div className="mt-3 flex flex-wrap gap-2">{inactiveAssignments.filter(tag=>selectedTagIds.includes(tag.id)).map(tag=><AssignmentChip key={tag.id} tag={tag} removable onRemove={()=>setSelectedTagIds(current=>current.filter(id=>id!==tag.id))}/>)}</div></section>}
      </div>
      <div className="flex items-center justify-end gap-2 border-t border-[var(--border)] bg-[var(--bg-elevated)] px-5 py-4"><button type="button" className="btn-secondary" onClick={onClose} disabled={!!busy}>取消</button><button type="button" className="btn-primary" onClick={()=>void save()} disabled={!!busy}>{busy==='save'?<Loader2 size={14} className="animate-spin"/>:<Check size={14}/>}确认保存</button></div>
    </div>
  </div>;
}
