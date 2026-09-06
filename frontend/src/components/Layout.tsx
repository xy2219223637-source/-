import { NavLink, Outlet } from 'react-router-dom';
import {
  LayoutDashboard,
  PackagePlus,
  BookOpen,
  Palette,
  PanelsTopLeft,
  Activity,
  LayoutTemplate,
  Factory,
  ShieldCheck,
  Video,
  Clapperboard,
  Film,
  Library,
  Layers,
} from 'lucide-react';
import IcpFiling from './IcpFiling';

const nav = [
  { to: '/', label: '工作台', icon: LayoutDashboard, end: true },
  { to: '/products', label: '商品管理', icon: PackagePlus },
  { to: '/knowledge', label: '知识库', icon: BookOpen },
  { to: '/brand-visuals', label: '品牌视觉', icon: Palette },
  { to: '/design-skills', label: '设计 Skill', icon: Palette },
  { to: '/creative-projects', label: 'AI 创作工作台', icon: PanelsTopLeft },
  { to: '/semi-finished-assets', label: '半成品素材库', icon: Layers },
  { to: '/finished-video-assets', label: '成品素材库', icon: Film },
  { to: '/final-videos', label: '成片库', icon: Library },
  { to: '/smart-video-compose', label: '智能成片', icon: Clapperboard },
  { to: '/finished-video-remix', label: '成品混剪', icon: Video },
  { to: '/video-projects', label: 'AI 口播视频', icon: Video },
  { to: '/task-center', label: '任务与费用', icon: Activity },
];

const plannedFeatures = [
  {
    label: '详情页模板',
    icon: LayoutTemplate,
    description: '复用已验证的详情页结构，待模板使用闭环完善后开放',
  },
  {
    label: '批量生产',
    icon: Factory,
    description: '通过 CSV 批量创建商品并生成详情页，暂未开放',
  },
  {
    label: '质量中心',
    icon: ShieldCheck,
    description: '集中配置质检规则、管理审核与质量反馈，暂未开放',
  },
];

export default function Layout() {
  if (!localStorage.getItem('access_token')) {
    window.location.href = '/login';
    return null;
  }
  return (
    <div className="min-h-screen flex bg-[var(--bg)] text-[var(--text)]">
      <aside className="w-60 shrink-0 border-r border-[var(--border)] bg-[var(--panel)] flex flex-col">
        <div className="px-5 py-6 border-b border-[var(--border)]">
          <div className="font-display text-3xl leading-none">Dirovo哈</div>
          <div className="mt-1 font-display text-base leading-none tracking-[0.08em]">蒂洛薇</div>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  isActive
                    ? 'bg-[var(--accent-soft)] text-[var(--accent)] font-medium'
                    : 'text-[var(--muted)] hover:bg-[var(--bg-elevated)] hover:text-[var(--text)]'
                }`
              }
            >
              <item.icon size={18} />
              {item.label}
            </NavLink>
          ))}
          <div className="pt-4 mt-4 border-t border-[var(--border)]">
            <div className="px-3 mb-2 text-[11px] font-medium tracking-wide text-[var(--muted)]">
              规划中
            </div>
            {plannedFeatures.map((item) => (
              <div
                key={item.label}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-[var(--muted)] opacity-70 cursor-not-allowed"
                title={item.description}
                aria-disabled="true"
              >
                <item.icon size={18} />
                <span className="flex-1">{item.label}</span>
                <span className="rounded-full border border-[var(--border)] px-2 py-0.5 text-[10px] leading-none">
                  规划中
                </span>
              </div>
            ))}
          </div>
        </nav>
        <div className="p-4 border-t border-[var(--border)] text-xs text-[var(--muted)] space-y-2">
          <div>多模态 Agent · RAG · 电商详情页</div>
          <IcpFiling className="block" />
        </div>
      </aside>
      <main className="flex-1 overflow-auto">
        <div className="max-w-6xl mx-auto px-8 py-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
