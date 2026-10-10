import {useEffect,useRef} from 'react';
import {createPortal} from 'react-dom';
import {X} from 'lucide-react';
import type {QianchuanUpload} from '../api/client';
import QianchuanPlanCatalog from './QianchuanPlanCatalog';
import type {QianchuanDemoPlan} from './QianchuanPlanDemo';

export default function QianchuanPlanModal({uploads,onClose,onPlanCompleted}:{
  uploads:QianchuanUpload[]; onClose:()=>void; onPlanCompleted:(plan:QianchuanDemoPlan)=>void;
}){
  const dialogRef=useRef<HTMLElement>(null);

  useEffect(()=>{
    const opener=document.activeElement as HTMLElement|null;
    dialogRef.current?.focus();
    const keyboard=(event:KeyboardEvent)=>{if(event.key==='Escape')onClose()};
    document.addEventListener('keydown',keyboard);
    return()=>{document.removeEventListener('keydown',keyboard);if(opener?.isConnected)opener.focus()};
  },[onClose]);

  return createPortal(<div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
    <section ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="qianchuan-plan-modal-title"
      className="panel max-h-[92vh] w-full max-w-5xl overflow-y-auto p-6 outline-none">
      <header className="flex items-start justify-between gap-4">
        <div><h2 id="qianchuan-plan-modal-title" className="text-xl font-medium">推送投流</h2>
          <p className="mt-2 text-sm text-[var(--muted)]">为当前视频创建一条新的商品全域计划；演示流程不会写入千川。</p></div>
        <button className="btn-secondary" aria-label="关闭推送投流" onClick={onClose}><X size={16}/></button>
      </header>
      <QianchuanPlanCatalog uploads={uploads} onPlanCompleted={onPlanCompleted}/>
    </section>
  </div>,document.body);
}
