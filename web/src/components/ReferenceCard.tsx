import { Card } from 'antd'
import { ArrowRightOutlined } from '@ant-design/icons'
import type { AskReference } from '@/api/types'
import { formatAuthors } from '@/utils/format'

interface Props {
  refItem: AskReference
  index: number
  /** 编号格式：true 显示 [1]，false 显示 1（默认） */
  bracketed?: boolean
  onClick: () => void
}

/** 参考文献卡片：点击跳转到 /paper/{id} 详情页，AskPage / GeneratePage 共用 */
export default function ReferenceCard({ refItem, index, bracketed = false, onClick }: Props) {
  const badge = bracketed ? `[${index + 1}]` : `${index + 1}`
  return (
    <Card
      className="pf-glass-card"
      variant="borderless"
      hoverable
      style={{ marginBottom: 12, cursor: 'pointer' }}
      onClick={onClick}
    >
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <div
          style={{
            flexShrink: 0,
            minWidth: 28,
            height: 28,
            borderRadius: 6,
            background: '#e6f0ff',
            color: '#1e40af',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: 600,
            fontSize: 13,
            padding: '0 6px',
          }}
        >
          {badge}
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            className="pf-serif pf-link"
            style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.5, marginBottom: 4 }}
          >
            {refItem.title}
          </div>
          <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
            {refItem.authors && refItem.authors.length > 0 && (
              <>
                {formatAuthors(refItem.authors, 3)}
                {refItem.year && <span style={{ margin: '0 8px', color: '#cbd5e1' }}>·</span>}
              </>
            )}
            {refItem.year && <span>{refItem.year}</span>}
            <span style={{ margin: '0 8px', color: '#cbd5e1' }}>·</span>
            <span style={{ fontFamily: 'monospace' }}>{refItem.id}</span>
          </div>
          {refItem.snippet && (
            <div
              className="pf-abstract"
              style={{
                marginTop: 6,
                fontSize: 13,
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {refItem.snippet}
            </div>
          )}
        </div>
        <ArrowRightOutlined style={{ color: '#cbd5e1', fontSize: 13, marginTop: 8 }} />
      </div>
    </Card>
  )
}
