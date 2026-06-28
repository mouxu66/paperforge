import { Card, Tag, Button } from 'antd'
import { CloseOutlined, DeleteOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import type { HistoryItem } from '@/store/useHistoryStore'

/** 格式化历史时间戳为 MM-DD HH:mm */
function formatHistoryTime(ts: number): string {
  const d = new Date(ts)
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  return `${mm}-${dd} ${hh}:${mi}`
}

interface AskHistorySidebarProps {
  items: HistoryItem[]
  activeHistoryId: string | null
  onSelect: (item: HistoryItem) => void
  onRemoveItem: (id: string) => void
  onClearAll: () => void
}

/** 历史对话侧边栏（AskPage 左侧） */
export default function AskHistorySidebar({
  items,
  activeHistoryId,
  onSelect,
  onRemoveItem,
  onClearAll,
}: AskHistorySidebarProps) {
  const { t } = useTranslation()
  return (
    <Card
      className="pf-glass-card"
      variant="borderless"
      style={{ padding: 0, position: 'sticky', top: 80 }}
    >
      {/* 标题栏 */}
      <div
        style={{
          padding: '14px 16px 10px',
          borderBottom: '1px solid rgba(232,236,241,0.6)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
        }}
      >
        <span className="pf-serif" style={{ fontSize: 14, fontWeight: 600, color: '#1a1a2e' }}>
          {t('ask.history')}
        </span>
        <Tag style={{ marginInlineEnd: 0, fontSize: 11 }}>{items.length}</Tag>
      </div>

      {/* 历史列表 */}
      <div style={{ maxHeight: 'calc(100vh - 280px)', overflowY: 'auto', padding: '8px 8px' }}>
        {items.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '32px 12px', color: '#94a3b8', fontSize: 13 }}>
            {t('ask.noHistory')}
          </div>
        ) : (
          items.map((item) => (
            <div
              key={item.id}
              className={`pf-history-item ${activeHistoryId === item.id ? 'pf-history-active' : ''}`}
              onClick={() => onSelect(item)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 13,
                      color: '#334155',
                      lineHeight: 1.5,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {item.query.slice(0, 20)}
                    {item.query.length > 20 ? '…' : ''}
                  </div>
                  <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>
                    {formatHistoryTime(item.timestamp)}
                  </div>
                </div>
                <Button
                  type="text"
                  size="small"
                  className="pf-history-delete"
                  icon={<CloseOutlined style={{ fontSize: 12, color: '#94a3b8' }} />}
                  onClick={(e) => {
                    e.stopPropagation()
                    onRemoveItem(item.id)
                  }}
                  style={{ flexShrink: 0, padding: 2 }}
                />
              </div>
            </div>
          ))
        )}
      </div>

      {/* 清空全部 */}
      {items.length > 0 && (
        <div style={{ padding: '8px 12px 12px', borderTop: '1px solid rgba(232,236,241,0.6)' }}>
          <Button
            danger
            block
            size="small"
            icon={<DeleteOutlined />}
            onClick={onClearAll}
          >
            {t('ask.clearAllHistory')}
          </Button>
        </div>
      )}
    </Card>
  )
}
