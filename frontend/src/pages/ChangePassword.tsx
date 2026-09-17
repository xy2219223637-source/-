import { useState, type FormEvent } from 'react';
import { authApi } from '../api/client';

export default function ChangePassword() {
  const [form,setForm]=useState({current:'',next:'',confirm:''});
  const [error,setError]=useState('');
  async function submit(event:FormEvent){event.preventDefault();setError('');if(form.next!==form.confirm){setError('两次输入的新密码不一致');return;}try{await authApi.changePassword(form.current,form.next);window.location.href='/';}catch(err:any){setError(err.response?.data?.detail||'修改失败');}}
  return <div className="min-h-screen grid place-items-center bg-[var(--bg)] p-6"><form className="panel w-full max-w-md space-y-4 p-8" onSubmit={submit}><div><h1 className="text-xl font-semibold">设置新密码</h1><p className="mt-1 text-sm text-[var(--muted)]">首次登录或管理员重置密码后，需要先设置自己的密码。</p></div><label className="field"><span>当前密码</span><input type="password" required minLength={8} value={form.current} onChange={e=>setForm({...form,current:e.target.value})}/></label><label className="field"><span>新密码</span><input type="password" required minLength={8} value={form.next} onChange={e=>setForm({...form,next:e.target.value})}/></label><label className="field"><span>确认新密码</span><input type="password" required minLength={8} value={form.confirm} onChange={e=>setForm({...form,confirm:e.target.value})}/></label>{error&&<div className="text-sm text-red-600">{error}</div>}<button className="btn-primary w-full justify-center" type="submit">保存并进入系统</button></form></div>;
}
