import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { api, authApi } from '../api/client';

export default function TenantAdminTabs() {
  const [me, setMe] = useState<Awaited<ReturnType<typeof authApi.me>> | null>(null);
  const [billingEnabled, setBillingEnabled] = useState(false);
  useEffect(() => {
    void authApi.me().then(setMe).catch(() => setMe(null));
    void api.get('/me/billing/config').then(() => setBillingEnabled(true)).catch(() => setBillingEnabled(false));
  }, []);
  const allowed = (code: string) => !!me && (me.role === 'owner' || me.permissions.includes('*') || me.permissions.includes(code));
  const links = [
    { to: '/tenant-admin', label: '用户', visible: allowed('tenant_users.manage') },
    { to: '/tenant-admin/roles', label: '角色', visible: allowed('tenant_roles.manage') },
    { to: '/tenant-admin/billing', label: '企业积分管理', visible: billingEnabled && allowed('billing.view') },
    { to: '/tenant-admin/taxonomy', label: '原业务分类与标签', visible: me?.role === 'owner' || me?.role === 'admin' },
    { to: '/tenant-admin/ecommerce-taxonomy', label: '电商分类与标签', visible: me?.role === 'owner' || me?.role === 'admin' },
  ];
  return <nav aria-label="企业管理功能" className="mt-4 flex min-h-10 flex-wrap gap-x-6 gap-y-2 border-b border-[var(--border)]">
    {links.filter(link => link.visible).map(link => <NavLink key={link.to} to={link.to} end className={({ isActive }) => isActive
      ? 'border-b-2 border-[var(--accent)] pt-1 pb-3 font-medium text-[var(--accent)]'
      : 'pt-1 pb-3 text-[var(--muted)]'}>{link.label}</NavLink>)}
  </nav>;
}
