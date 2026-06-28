import { Layout } from 'antd'
import { useTranslation } from 'react-i18next'
import { CATEGORIES, CATEGORY_COLOR } from '@/utils/constants'
import { usePaperStore } from '@/store/usePaperStore'

const { Sider } = Layout

export default function SideBar() {
  const { t } = useTranslation()
  const category = usePaperStore((s) => s.category)
  const setCategory = usePaperStore((s) => s.setCategory)
  const stats = usePaperStore((s) => s.stats)

  const countOf = (key: string) => {
    if (!stats) return 0
    if (key === 'all') return stats.totalPapers
    return stats.byCategory.find((c) => c.category === key)?.count ?? 0
  }

  return (
    <Sider
      width={232}
      style={{
        background: '#fbfbfd',
        borderRight: '1px solid #e8ecf1',
        overflow: 'auto',
        height: 'calc(100vh - 60px)',
        position: 'sticky',
        top: 60,
        paddingTop: 8,
      }}
    >
      <div style={{ padding: '12px 12px 10px' }}>
        <div
          style={{
            fontSize: 12,
            color: '#94a3b8',
            letterSpacing: 1,
            paddingLeft: 10,
            fontWeight: 500,
          }}
        >
          {t('sidebar.categories')}
        </div>
      </div>

      <div>
        {CATEGORIES.map((c) => {
          const active = category === c.key
          return (
            <div
              key={c.key}
              className={`pf-category-item ${active ? 'pf-category-active' : ''}`}
              onClick={() => setCategory(c.key)}
            >
              <span
                className="pf-category-indicator"
                style={{ background: CATEGORY_COLOR[c.key] || '#94a3b8' }}
              />
              <span className="pf-category-label">
                <span>{c.icon}</span>
                {c.label}
              </span>
              <span className="pf-category-count">{countOf(c.key)}</span>
            </div>
          )
        })}
      </div>

      <div
        style={{
          padding: '16px 20px',
          borderTop: '1px solid #f1f5f9',
          margin: '12px 10px 0 10px',
        }}
      >
        <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 8 }}>{t('sidebar.systemStatus')}</div>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            fontSize: 13,
            color: '#15803d',
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: '50%',
              background: '#22c55e',
              display: 'inline-block',
            }}
          />
          {t('sidebar.apiOnline')}
        </div>
      </div>
    </Sider>
  )
}
