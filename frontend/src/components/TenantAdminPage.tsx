import type { ReactNode } from 'react';
import TenantAdminTabs from './TenantAdminTabs';

/** Keep the administration frame stable while each page supplies its own tools. */
export default function TenantAdminPage({ title, description, actions, children, enterprise = true }: {
  title: string;
  description: string;
  actions?: ReactNode;
  children: ReactNode;
  enterprise?: boolean;
}) {
  return <div className="w-full min-w-0 space-y-6">
    {enterprise && <header>
      <h1 className="text-2xl font-semibold">企业管理后台</h1>
      <TenantAdminTabs />
    </header>}
    <div className="flex min-h-16 flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        {enterprise ? <h2 className="text-xl font-semibold">{title}</h2> : <h1 className="text-2xl font-semibold">{title}</h1>}
        <p className="mt-2 text-sm text-[var(--muted)]">{description}</p>
      </div>
      {actions && <div className="flex min-h-10 flex-wrap items-center gap-3">{actions}</div>}
    </div>
    {children}
  </div>;
}
