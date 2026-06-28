import { Card, Statistic } from 'antd'
import { FileTextOutlined, DatabaseOutlined, HddOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import type { LibraryStats } from '@/api/types'
import { formatSize } from '@/utils/format'

interface Props {
  stats: LibraryStats | null
  loading?: boolean
}

export default function StatCards({ stats, loading }: Props) {
  const { t } = useTranslation()
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
      <Card loading={loading} className="pf-card-hover" variant="borderless">
        <Statistic
          title={<span style={{ color: '#64748b' }}>{t('stats.totalPapers')}</span>}
          value={stats?.totalPapers ?? 0}
          prefix={<FileTextOutlined style={{ color: '#1e40af' }} />}
          valueStyle={{ color: '#1a1a2e', fontWeight: 600 }}
        />
      </Card>
      <Card loading={loading} className="pf-card-hover" variant="borderless">
        <Statistic
          title={<span style={{ color: '#64748b' }}>{t('stats.textChunks')}</span>}
          value={stats?.totalChunks ?? 0}
          prefix={<DatabaseOutlined style={{ color: '#1e40af' }} />}
          valueStyle={{ color: '#1a1a2e', fontWeight: 600 }}
        />
      </Card>
      <Card loading={loading} className="pf-card-hover" variant="borderless">
        <Statistic
          title={<span style={{ color: '#64748b' }}>{t('stats.indexSize')}</span>}
          value={stats ? formatSize(stats.totalSize) : '0 B'}
          prefix={<HddOutlined style={{ color: '#1e40af' }} />}
          valueStyle={{ color: '#1a1a2e', fontWeight: 600 }}
        />
      </Card>
    </div>
  )
}
