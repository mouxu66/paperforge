import { lazy, Suspense, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { Skeleton } from 'antd'
import MainLayout from './layouts/MainLayout'
import { useFavoriteStore } from './store/useFavoriteStore'
import { usePaperStore } from './store/usePaperStore'
// 初始化 i18n（子任务 5）：必须在任何使用 useTranslation 的组件渲染前导入
import './i18n'

// 路由级懒加载：按需打包，减小首屏 chunk 体积
const HomePage = lazy(() => import('./pages/HomePage'))
const FavoritesPage = lazy(() => import('./pages/FavoritesPage'))
const DetailPage = lazy(() => import('./pages/DetailPage'))
const AskPage = lazy(() => import('./pages/AskPage'))
const GeneratePage = lazy(() => import('./pages/GeneratePage'))
const ModelsPage = lazy(() => import('./pages/ModelsPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const WritingDashboard = lazy(() => import('./pages/WritingDashboard'))
const WritingEditor = lazy(() => import('./pages/WritingEditor'))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'))

/** P2-3: 懒加载 fallback —— 骨架屏替代白屏/Spin */
function PageLoading() {
  return (
    <div style={{ padding: 24, maxWidth: 1000, margin: '0 auto' }}>
      {/* 顶部导航骨架 */}
      <Skeleton active paragraph={{ rows: 0 }} style={{ marginBottom: 24 }} />
      {/* 内容区骨架 */}
      <Skeleton active paragraph={{ rows: 6 }} />
    </div>
  )
}

export default function App() {
  // 应用启动时拉取后端收藏与统计，覆盖本地缓存，确保显示后端最新数据。
  // App 包裹所有路由，任何页面入口（包括直接打开 /paper/:id）都会触发一次。
  // statsLoading / loading 防抖保证并发调用安全。
  useEffect(() => {
    void useFavoriteStore.getState().load()
    void usePaperStore.getState().loadStats()
  }, [])

  return (
    <Suspense fallback={<PageLoading />}>
      <Routes>
        <Route element={<MainLayout />}>
          <Route path="/" element={<HomePage />} />
          <Route path="/ask" element={<AskPage />} />
          <Route path="/generate" element={<GeneratePage />} />
          <Route path="/models" element={<ModelsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/write" element={<WritingDashboard />} />
          <Route path="/write/:projectId" element={<WritingEditor />} />
          <Route path="/favorites" element={<FavoritesPage />} />
          <Route path="/paper/:id" element={<DetailPage />} />
          <Route path="/404" element={<NotFoundPage />} />
          <Route path="*" element={<Navigate to="/404" replace />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
