import { useState, type FormEvent } from 'react';
import { authApi } from '../api/client';
import IcpFiling from '../components/IcpFiling';
import { safeReturnPath } from '../lib/loginReturn';
import { COMPANY_NAME } from '../lib/siteIdentity';

export default function Login() {
  const [form, setForm] = useState({ phone: '', password: '' });
  const [error, setError] = useState('');

  async function submit(event: FormEvent) {
    event.preventDefault(); setError('');
    try {
      const result = await authApi.login(form.phone, form.password);
      localStorage.setItem('access_token', result.access_token);
      const oauthReturn = sessionStorage.getItem('qianchuan_oauth_return') || '';
      sessionStorage.removeItem('qianchuan_oauth_return');
      if (result.user?.must_change_password === 'true') {
          window.location.href = '/change-password';
          return;
      }

      if (/^\/qianchuan\/oauth\/result\?session_id=[0-9a-f-]{36}$/.test(oauthReturn)) {
          window.location.href = oauthReturn;
          return;
      }
      window.location.href = safeReturnPath(
          new URLSearchParams(window.location.search).get('returnTo')
        );
    } catch (err: any) {
      setError(err.response?.data?.detail || '操作失败');
    }
  }

  return <div className="min-h-screen flex flex-col bg-[var(--bg)] p-6">
    <div className="flex flex-1 items-center justify-center">
      <form className="panel p-8 w-full max-w-md space-y-4" onSubmit={submit}>
        <div className="pb-2">
          <div className="text-xl font-medium leading-7 break-words text-balance">{COMPANY_NAME}</div>
        </div>
        <h1 className="text-base font-medium">登录品牌创作平台</h1>
        <label className="field"><span>手机号</span><input type="tel" inputMode="numeric" autoComplete="tel" required pattern="1[0-9]{10}" maxLength={11} value={form.phone} onChange={(e)=>setForm({...form,phone:e.target.value.replace(/\D/g, '')})}/></label>
        <label className="field"><span>密码</span><input type="password" minLength={8} required value={form.password} onChange={(e)=>setForm({...form,password:e.target.value})}/></label>
        {error && <div className="text-sm text-red-600">{error}</div>}
        <button className="btn-primary w-full justify-center" type="submit">登录</button>
        <div className="text-center text-xs text-[var(--muted)]">账号由企业管理员创建；忘记密码请联系管理员重置</div>
      </form>
    </div>
    <IcpFiling className="mt-6 text-center" />
  </div>;
}
