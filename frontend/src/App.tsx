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
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
