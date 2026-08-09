import { lazy, Suspense, useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { Skeleton } from "antd";
import MainLayout from "./layouts/MainLayout";
import ErrorBoundary from "./components/ErrorBoundary";
import { useFavoriteStore } from "./store/useFavoriteStore";
import { usePaperStore } from "./store/usePaperStore";
// 初始化 i18n（子任务 5）：必须在任何使用 useTranslation 的组件渲染前导入
import "./i18n";

// 路由级懒加载：按需打包，减小首屏 chunk 体积
const HomePage = lazy(() => import("./pages/HomePage"));
const FavoritesPage = lazy(() => import("./pages/FavoritesPage"));
const DetailPage = lazy(() => import("./pages/DetailPage"));
const AskPage = lazy(() => import("./pages/AskPage"));
const GeneratePage = lazy(() => import("./pages/GeneratePage"));
const ComparePapersPage = lazy(() => import("./pages/ComparePapersPage"));
const ModelsPage = lazy(() => import("./pages/ModelsPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const DuplicatePapersPage = lazy(() => import("./pages/DuplicatePapersPage"));
const WritingDashboard = lazy(() => import("./pages/WritingDashboard"));
const WritingEditor = lazy(() => import("./pages/WritingEditor"));
const DepthAnalysis = lazy(() => import("./pages/DepthAnalysis"));
const DepthReview = lazy(() => import("./pages/DepthReview"));
const ReflectionReportsPage = lazy(() => import("./pages/ReflectionReportsPage"));
const ExperimentAuditPage = lazy(() => import("./pages/ExperimentAuditPage"));
const FigureSearchPage = lazy(() => import("./pages/FigureSearchPage"));
const ReflectionResultView = lazy(() => import("./components/ReflectionResultView"));
const ExtensionPage = lazy(() => import("./pages/ExtensionPage"));
const LocalRagPage = lazy(() => import("./pages/LocalRagPage"));
const NotFoundPage = lazy(() => import("./pages/NotFoundPage"));

/** P2-3: 懒加载 fallback —— 骨架屏替代白屏/Spin */
function PageLoading() {
  return (
    <div
      style={{
        width: "100%",
        maxWidth: "1480px",
        padding: "30px clamp(20px, 3vw, 42px) 48px",
        margin: "0 auto",
      }}
    >
      {/* 顶部导航骨架 */}
      <Skeleton active paragraph={{ rows: 0 }} style={{ marginBottom: 24 }} />
      {/* 内容区骨架 */}
      <Skeleton active paragraph={{ rows: 6 }} />
    </div>
  );
}

export default function App() {
  // 应用启动时拉取后端收藏与统计，覆盖本地缓存，确保显示后端最新数据。
  // App 包裹所有路由，任何页面入口（包括直接打开 /paper/:id）都会触发一次。
  // statsLoading / loading 防抖保证并发调用安全。
  useEffect(() => {
    void useFavoriteStore.getState().load();
    void usePaperStore.getState().loadStats();
  }, []);

  return (
    <ErrorBoundary>
      <Suspense fallback={<PageLoading />}>
        <Routes>
          <Route element={<MainLayout />}>
            <Route path="/" element={<HomePage />} />
            <Route path="/ask" element={<AskPage />} />
            <Route path="/generate" element={<GeneratePage />} />
            <Route path="/compare" element={<ComparePapersPage />} />
            <Route path="/models" element={<ModelsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/duplicates" element={<DuplicatePapersPage />} />
            <Route path="/write" element={<WritingDashboard />} />
            <Route path="/write/:projectId" element={<WritingEditor />} />
            <Route path="/analysis" element={<Navigate to="/depth" replace />} />
            <Route path="/depth" element={<DepthAnalysis />} />
            {/* F1: V4 审稿结果/列表页原来被重定向到 /depth，导致完整实现的 DepthReviewPage
                （含 DepthResultView 单篇结果 + DepthListPage 列表）从未被路由挂载，用户无法访问。
                现在真正挂载 DepthReviewPage，它通过 useParams 判断 paperId 自动切换结果/列表视图。 */}
            <Route path="/depth-v4" element={<DepthReview />} />
            <Route path="/depth-v4/result/:paperId" element={<DepthReview />} />
            <Route path="/depth-v4/list" element={<DepthReview />} />
            <Route path="/experiment-audit" element={<ExperimentAuditPage />} />
            <Route path="/figures" element={<FigureSearchPage />} />
            <Route path="/extension" element={<ExtensionPage />} />
            <Route path="/local-rag" element={<LocalRagPage />} />
            <Route path="/reflection" element={<ReflectionReportsPage />} />
            <Route path="/reflection/result/:paperId" element={<ReflectionResultView />} />
            <Route path="/favorites" element={<FavoritesPage />} />
            <Route path="/paper/:id" element={<DetailPage />} />
            <Route path="/404" element={<NotFoundPage />} />
            <Route path="*" element={<Navigate to="/404" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}
