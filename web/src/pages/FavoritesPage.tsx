import { Typography, Space } from 'antd'
import { StarOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import PaperCard from '@/components/PaperCard'
import EmptyState from '@/components/EmptyState'
import { useFavoriteStore } from '@/store/useFavoriteStore'

export default function FavoritesPage() {
  // items 由 persist 中间件自动从 localStorage 恢复，无需手动 load
  const items = useFavoriteStore((s) => s.items)
  const { t } = useTranslation()

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <Typography.Title level={3} className="pf-page-title" style={{ marginBottom: 0 }}>
          {t('favorites.title')}
        </Typography.Title>
        <div className="pf-subtitle">{t('favorites.subtitle')}</div>
      </div>

      {items.length === 0 ? (
        <EmptyState description={t('favorites.empty')} />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
          {items.map((p) => (
            <PaperCard key={p.id} paper={p} />
          ))}
        </div>
      )}

      <div style={{ marginTop: 24, textAlign: 'center' }}>
        <Space style={{ color: '#94a3b8', fontSize: 13 }}>
          <StarOutlined /> {t('favorites.total', { count: items.length })}
        </Space>
      </div>
    </div>
  )
}
