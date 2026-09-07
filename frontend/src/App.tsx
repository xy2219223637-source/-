import { lazy, Suspense } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

const Layout = lazy(() => import('./components/Layout'));
const Dashboard = lazy(() => import('./pages/Dashboard'));
const ProductCreate = lazy(() => import('./pages/ProductCreate'));
const ProductsPage = lazy(() => import('./pages/Products'));
const ProductDetail = lazy(() => import('./pages/ProductDetail'));
const KnowledgePage = lazy(() => import('./pages/Knowledge'));
const Login = lazy(() => import('./pages/Login'));
const DesignSkillsPage = lazy(() => import('./pages/DesignSkills'));
const CreativeProjectsPage = lazy(() => import('./pages/CreativeProjects'));
const CreativeCanvasPage = lazy(() => import('./pages/CreativeCanvas'));
const StoryboardPage = lazy(() => import('./pages/Storyboard'));
const BrandVisualsPage = lazy(() => import('./pages/BrandVisuals'));
const TaskCenterPage = lazy(() => import('./pages/TaskCenter'));
const DetailTemplatesPage = lazy(() => import('./pages/DetailTemplates'));
const ProductionHubPage = lazy(() => import('./pages/ProductionHub'));
const QualityWorkbenchPage = lazy(() => import('./pages/QualityWorkbench'));
const VideoProjectsPage = lazy(() => import('./pages/VideoProjects'));
const VideoEditorPage = lazy(() => import('./pages/VideoEditor'));
const DirectorProjectsPage = lazy(() => import('./pages/DirectorProjects'));
const DirectorAnalysisPage = lazy(() => import('./pages/DirectorAnalysis'));
const DirectorWorkspacePage = lazy(() => import('./pages/DirectorWorkspace'));
const ShootingTasksPage = lazy(() => import('./pages/ShootingTasks'));
const PerformanceVideoProjectsPage = lazy(() => import('./pages/PerformanceVideoProjects'));
const AutoEditWorkspace = lazy(() => import('./pages/AutoEditWorkspace'));
const VideoAssetLibrary = lazy(() => import('./pages/VideoAssetLibrary'));
const FinalVideoLibrary = lazy(() => import('./pages/FinalVideoLibrary'));

function PageLoading() {
  return <div className="min-h-screen grid place-items-center bg-[var(--bg)] text-[var(--muted)]"><div className="flex items-center gap-2 text-sm"><Loader2 size={18} className="animate-spin text-[var(--accent)]" />页面加载中…</div></div>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route path="login" element={<Login />} />
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="products/new" element={<ProductCreate />} />
            <Route path="products" element={<ProductsPage />} />
            <Route path="products/:id" element={<ProductDetail />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="design-skills" element={<DesignSkillsPage />} />
            <Route path="brand-visuals" element={<BrandVisualsPage />} />
            <Route path="task-center" element={<TaskCenterPage />} />
            <Route path="detail-templates" element={<DetailTemplatesPage />} />
            <Route path="production" element={<ProductionHubPage />} />
            <Route path="quality" element={<QualityWorkbenchPage />} />
            <Route path="creative-projects" element={<CreativeProjectsPage />} />
            <Route path="creative-projects/:id" element={<CreativeCanvasPage />} />
            <Route path="creative-projects/:id/storyboard" element={<StoryboardPage />} />
            <Route path="video-projects" element={<VideoProjectsPage />} />
            <Route path="video-projects/:id" element={<VideoEditorPage />} />
            <Route path="director-inspiration" element={<DirectorProjectsPage />} />
            <Route path="director-inspiration/:id" element={<DirectorAnalysisPage />} />
            <Route path="director-inspiration/:id/workspace" element={<DirectorWorkspacePage />} />
            <Route path="shooting-tasks" element={<ShootingTasksPage />} />
            <Route path="semi-finished-assets" element={<VideoAssetLibrary libraryType="semi_finished" title="半成品素材库" description="存放达人口播、商品展示、质地特写、上脸过程、效果对比和空镜。上传后自动分析原声、口型与可用时间段，只供智能成片使用。" />} />
            <Route path="finished-video-assets" element={<VideoAssetLibrary libraryType="finished" title="成品素材库" description="只存放可参与混剪的成品片段。每段保留原声、原画面、原字幕和原 BGM，审核为可混剪后进入成品混剪。" />} />
            <Route path="final-videos" element={<FinalVideoLibrary />} />
            <Route path="smart-video-compose" element={<PerformanceVideoProjectsPage editMode="smart_compose" title="智能成片" description="只用半成品素材，按 Hook→卖点→品牌信任→痛点→展示→使用→效果→直播间 CTA 生成成片，并自动进入成片库。" />} />
            <Route path="smart-video-compose/:id" element={<AutoEditWorkspace />} />
            <Route path="finished-video-remix" element={<PerformanceVideoProjectsPage editMode="finished_remix" title="成品混剪" description="只用已审核可复用的成品素材，保留原声、原画面和原字幕，按相邻语义衔接生成多个去重版本。" />} />
            <Route path="finished-video-remix/:id" element={<AutoEditWorkspace />} />
            <Route path="performance-video-projects" element={<Navigate to="/smart-video-compose" replace />} />
            <Route path="performance-video-projects/:id" element={<AutoEditWorkspace />} />
            <Route path="video-assets" element={<Navigate to="/semi-finished-assets" replace />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
