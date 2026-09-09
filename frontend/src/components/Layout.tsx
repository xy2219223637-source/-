import { useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
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
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Menu,
  X,
} from 'lucide-react';
import IcpFiling from './IcpFiling';
import dirovoLogo from '../assets/dirovo-logo.svg';

const dashboardLink = { to: '/', label: '工作台', icon: LayoutDashboard, end: true };

const routePreloaders: Record<string, () => Promise<unknown>> = {
  '/': () => import('../pages/Dashboard'),
  '/products': () => import('../pages/Products'),
  '/knowledge': () => import('../pages/Knowledge'),
  '/brand-visuals': () => import('../pages/BrandVisuals'),
  '/design-skills': () => import('../pages/DesignSkills'),
  '/creative-projects': () => import('../pages/CreativeProjects'),
  '/finished-video-assets': () => import('../pages/VideoAssetLibrary'),
  '/finished-video-remix': () => import('../pages/AutoEditWorkspace'),
  '/video-projects': () => import('../pages/VideoProjects'),
  '/final-videos': () => import('../pages/FinalVideoLibrary'),
  '/task-center': () => import('../pages/TaskCenter'),
  '/semi-finished-assets': () => import('../pages/VideoAssetLibrary'),
  '/smart-video-compose': () => import('../pages/AutoEditWorkspace'),
  '/director-inspiration': () => import('../pages/DirectorProjects'),
  '/shooting-tasks': () => import('../pages/ShootingTasks'),
};

function preloadRoute(path: string) {
  void routePreloaders[path]?.();
}

const navGroups = [
  {
    label: '商品与品牌',
    items: [
      { to: '/products', label: '商品管理', icon: PackagePlus },
      { to: '/knowledge', label: '知识库', icon: BookOpen },
      { to: '/brand-visuals', label: '品牌视觉', icon: Palette },
    ],
  },
  {
    label: '内容设计',
    items: [
      { to: '/design-skills', label: '设计 Skill', icon: Palette },
      { to: '/creative-projects', label: 'AI 创作工作台', icon: PanelsTopLeft },
    ],
  },
  {
    label: '视频生产',
    items: [
      { to: '/finished-video-assets', label: '成品素材库', icon: Film },
      { to: '/finished-video-remix', label: '成品混剪', icon: Video },
      { to: '/final-videos', label: '成片库', icon: Library },
      { to: '/director-inspiration', label: '编导灵感', icon: Clapperboard },
      { to: '/shooting-tasks', label: '拍摄任务', icon: ClipboardList },
    ],
  },
  {
    label: '运营管理',
    items: [
      { to: '/task-center', label: '任务与费用', icon: Activity },
    ],
  },
];

const plannedLinks = [
  { to: '/semi-finished-assets', label: '半成品素材库', icon: Layers },
  { to: '/smart-video-compose', label: '智能成片', icon: Clapperboard },
  { to: '/video-projects', label: 'AI 口播视频', icon: Video },
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

function navClassName({ isActive }: { isActive: boolean }) {
  return `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
    isActive
      ? 'bg-[var(--accent-soft)] text-[var(--accent)] font-medium'
      : 'text-[var(--muted)] hover:bg-[var(--bg-elevated)] hover:text-[var(--text)]'
  }`;
}

export default function Layout() {
  const location = useLocation();
  const [plannedOpen, setPlannedOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const isDirectorWorkspace = /^\/director-inspiration\/\d+\/workspace/.test(location.pathname);

  if (!localStorage.getItem('access_token')) {
    window.location.href = '/login';
    return null;
  }

  const closeMobileNav = () => setMobileNavOpen(false);

  return (
    <div className="min-h-screen flex bg-[var(--bg)] text-[var(--text)]">
      {mobileNavOpen && (
        <button
          className="fixed inset-0 z-40 bg-black/35 lg:hidden"
          onClick={closeMobileNav}
          aria-label="关闭导航遮罩"
        />
      )}
      <aside
        className={`${mobileNavOpen ? 'fixed inset-y-0 left-0 z-50 flex' : 'hidden'} lg:static lg:flex w-60 shrink-0 border-r border-[var(--border)] bg-[var(--panel)] flex-col`}
      >
        <div className="flex min-h-24 items-center justify-between gap-2 border-b border-[var(--border)] px-5 py-4">
          <img src={dirovoLogo} alt="Dirovo 蒂洛薇" className="h-auto w-[148px] max-w-full" />
          <button
            className="lg:hidden w-8 h-8 grid place-items-center shrink-0"
            onClick={closeMobileNav}
            aria-label="关闭导航"
          >
            <X size={18} />
          </button>
        </div>
        <nav className="flex-1 overflow-y-auto p-3 space-y-1">
          <NavLink
            to={dashboardLink.to}
            end={dashboardLink.end}
            onClick={closeMobileNav}
            onPointerEnter={() => preloadRoute(dashboardLink.to)}
            onFocus={() => preloadRoute(dashboardLink.to)}
            className={navClassName}
          >
            <dashboardLink.icon size={18} />
            {dashboardLink.label}
          </NavLink>
          {navGroups.map((group) => (
            <div key={group.label} className="pt-3">
              <div className="px-3 pb-1.5 text-[10px] font-medium tracking-[0.12em] text-[var(--muted)] opacity-70">
                {group.label}
              </div>
              <div className="space-y-1">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    onClick={closeMobileNav}
                    onPointerEnter={() => preloadRoute(item.to)}
                    onFocus={() => preloadRoute(item.to)}
                    className={navClassName}
                  >
                    <item.icon size={18} />
                    {item.label}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
          <div className="pt-4 mt-4 border-t border-[var(--border)]">
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left text-sm font-medium text-[var(--muted)] transition-colors hover:bg-[var(--bg-elevated)] hover:text-[var(--text)]"
              onClick={() => setPlannedOpen((open) => !open)}
              aria-expanded={plannedOpen}
            >
              {plannedOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
              <span className="flex-1">规划中</span>
              <span className="text-[10px] font-normal">{plannedLinks.length + plannedFeatures.length}</span>
            </button>
            {plannedOpen && (
              <div className="mt-1 space-y-1">
                {plannedLinks.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    onClick={closeMobileNav}
                    onPointerEnter={() => preloadRoute(item.to)}
                    onFocus={() => preloadRoute(item.to)}
                    className={({ isActive }) =>
                      `flex items-center gap-3 rounded-lg py-2.5 pl-7 pr-3 text-sm transition-colors ${
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
                {plannedFeatures.map((item) => (
                  <div
                    key={item.label}
                    className="flex cursor-not-allowed items-center gap-3 rounded-lg py-2.5 pl-7 pr-3 text-sm text-[var(--muted)] opacity-60"
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
            )}
          </div>
        </nav>
        <div className="p-4 border-t border-[var(--border)] text-xs text-[var(--muted)] space-y-2">
          <div>多模态 Agent · RAG · 电商详情页</div>
          <IcpFiling className="block" />
        </div>
      </aside>
      <main className="flex-1 min-w-0 overflow-auto">
        <div className="lg:hidden sticky top-0 z-30 h-14 border-b border-[var(--border)] bg-[var(--panel)] flex items-center px-4 gap-3">
          <button
            className="w-9 h-9 grid place-items-center"
            onClick={() => setMobileNavOpen(true)}
            aria-label="打开导航"
          >
            <Menu size={20} />
          </button>
          <div className="font-display text-xl">Dirovo</div>
        </div>
        <div className={isDirectorWorkspace ? 'max-w-[1800px] mx-auto px-5 py-5' : 'max-w-6xl mx-auto px-8 py-8'}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}
