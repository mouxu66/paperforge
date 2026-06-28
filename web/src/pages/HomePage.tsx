import { useEffect } from 'react'
import { Typography } from 'antd'
import { useTranslation } from 'react-i18next'
import SearchBar from '@/components/SearchBar'
import StatCards from '@/components/StatCards'
import PaperList from '@/components/PaperList'
import UploadPaper from '@/components/UploadPaper'
import ArxivImport from '@/components/ArxivImport'
import { usePaperStore } from '@/store/usePaperStore'

export default function HomePage() {
  const { t } = useTranslation()
  const {
    items,
    total,
    loading,
    keyword,
    sort,
    page,
    pageSize,
    stats,
    semantic,
    setKeyword,
    setSort,
    setPage,
    setSemantic,
    loadPapers,
  } = usePaperStore()

  // 统计由 App.tsx 全局初始化调用，这里不再重复请求 loadStats
  // 查询条件变化时重新拉取论文列表（含语义搜索模式切换）
  useEffect(() => {
    loadPapers()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword, sort, page, semantic])

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <Typography.Title level={3} className="pf-page-title" style={{ marginBottom: 0 }}>
          {t('home.title')}
        </Typography.Title>
        <div className="pf-subtitle">{t('home.subtitle')}</div>
      </div>

      <div style={{ marginBottom: 20 }}>
        <StatCards stats={stats} loading={!stats} />
      </div>

      <div style={{ marginBottom: 20 }}>
        <SearchBar
          keyword={keyword}
          sort={sort}
          semantic={semantic}
          onKeyword={setKeyword}
          onSort={setSort}
          onSemantic={setSemantic}
        />
      </div>

      <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <ArxivImport />
      </div>

      <UploadPaper />

      <PaperList
        items={items}
        loading={loading}
        total={total}
        page={page}
        pageSize={pageSize}
        onPage={setPage}
      />
    </div>
  )
}
