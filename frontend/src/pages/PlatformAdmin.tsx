import { useEffect, useState, type FormEvent } from 'react';
import { Building2, Plus, RefreshCw } from 'lucide-react';
import { platformAdminApi } from '../api/client';

type TenantRow = { id:string; name:string; code:string; status:string; member_count:number; owner_name:string; owner_phone:string; owner_user_id:string; owner_status:string; created_at:string };

export default function PlatformAdmin() {
  const [rows, setRows] = useState<TenantRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name:'', code:'', owner_name:'', owner_phone:'', initial_password:'' });
  const load = async () => { setLoading(true); setError(''); try { setRows(await platformAdminApi.listTenants()); } catch (err:any) { setError(err.response?.data?.detail || '加载失败'); } finally { setLoading(false); } };
  useEffect(() => { void load(); }, []);
  async function create(event: FormEvent) { event.preventDefault(); setError(''); try { await platformAdminApi.createTenant(form); setShowCreate(false); setForm({ name:'', code:'', owner_name:'', owner_phone:'', initial_password:'' }); await load(); } catch (err:any) { setError(err.response?.data?.detail || '创建失败'); } }
  async function toggle(row: TenantRow) { await platformAdminApi.updateTenant(row.id, { status: row.status === 'active' ? 'disabled' : 'active' }); await load(); }
  async function reset(row: TenantRow) { const password = window.prompt(`为 ${row.owner_name} 设置新密码（至少8位）`); if (!password) return; try { await platformAdminApi.resetPassword(row.owner_user_id, password); window.alert('密码已重置，用户下次登录需修改密码'); } catch (err:any) { window.alert(err.response?.data?.detail || '重置失败'); } }
  async function editOwner(row: TenantRow) { const name=window.prompt('主管理员姓名',row.owner_name);if(!name)return;const phone=window.prompt('主管理员手机号',row.owner_phone);if(!phone)return;try{await platformAdminApi.updateUser(row.owner_user_id,{name,phone});await load();}catch(err:any){window.alert(err.response?.data?.detail||'修改失败');} }
  async function toggleOwner(row: TenantRow) { try { await platformAdminApi.updateUser(row.owner_user_id,{status:row.owner_status==='active'?'disabled':'active'});await load(); } catch(err:any){window.alert(err.response?.data?.detail||'操作失败');} }
  return <div className="space-y-6">
    <div className="flex items-center justify-between gap-4"><div><h1 className="text-2xl font-semibold">平台管理后台</h1><p className="mt-1 text-sm text-[var(--muted)]">管理客户企业及其主管理员</p></div><button className="btn-primary" onClick={()=>setShowCreate(true)}><Plus size={16}/>新增企业</button></div>
    {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-600">{error}</div>}
    {showCreate && <form className="panel grid gap-4 p-5 md:grid-cols-2" onSubmit={create}>
      <h2 className="md:col-span-2 font-medium">新增企业与主管理员</h2>
      <label className="field"><span>企业名称</span><input required value={form.name} onChange={e=>setForm({...form,name:e.target.value})}/></label>
      <label className="field"><span>企业代码</span><input required pattern="[a-z0-9-]+" value={form.code} onChange={e=>setForm({...form,code:e.target.value})}/></label>
      <label className="field"><span>主管理员姓名</span><input required value={form.owner_name} onChange={e=>setForm({...form,owner_name:e.target.value})}/></label>
      <label className="field"><span>主管理员手机号</span><input required pattern="1[0-9]{10}" maxLength={11} value={form.owner_phone} onChange={e=>setForm({...form,owner_phone:e.target.value.replace(/\D/g,'')})}/></label>
      <label className="field"><span>初始密码</span><input required type="password" minLength={8} value={form.initial_password} onChange={e=>setForm({...form,initial_password:e.target.value})}/></label>
      <div className="flex items-end gap-2"><button className="btn-primary" type="submit">创建</button><button className="btn-secondary" type="button" onClick={()=>setShowCreate(false)}>取消</button></div>
    </form>}
    <div className="panel overflow-x-auto"><table className="w-full min-w-[850px] text-sm"><thead><tr className="border-b border-[var(--border)] text-left text-[var(--muted)]"><th className="p-4">企业</th><th>代码</th><th>主管理员</th><th>手机号</th><th>成员数</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>
      {rows.map(row=><tr key={row.id} className="border-b border-[var(--border)] last:border-0"><td className="p-4"><span className="inline-flex items-center gap-2 font-medium"><Building2 size={16}/>{row.name}</span></td><td>{row.code}</td><td>{row.owner_name || '-'}{row.owner_status==='disabled'&&<span className="ml-1 text-xs text-red-500">已停用</span>}</td><td>{row.owner_phone || '-'}</td><td>{row.member_count}</td><td><span className={row.status==='active'?'text-emerald-600':'text-[var(--muted)]'}>{row.status==='active'?'启用':'停用'}</span></td><td>{new Date(row.created_at).toLocaleDateString()}</td><td><div className="flex flex-wrap gap-3"><button className="text-[var(--accent)]" onClick={()=>toggle(row)}>{row.status==='active'?'停用企业':'启用企业'}</button><button className="text-[var(--accent)]" onClick={()=>editOwner(row)}>编辑主管理员</button><button className="text-[var(--accent)]" onClick={()=>toggleOwner(row)}>{row.owner_status==='active'?'停用账号':'启用账号'}</button><button className="text-[var(--accent)]" onClick={()=>reset(row)}>重置密码</button></div></td></tr>)}
      {!loading && !rows.length && <tr><td colSpan={8} className="p-10 text-center text-[var(--muted)]">暂无企业</td></tr>}
    </tbody></table>{loading && <div className="flex items-center justify-center gap-2 p-8 text-sm text-[var(--muted)]"><RefreshCw className="animate-spin" size={16}/>加载中</div>}</div>
  </div>;
}
