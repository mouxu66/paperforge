import { useEffect, useRef, useState } from 'react'
import { Skeleton, Pagination } from 'antd'
import { FixedSizeList as List } from 'react-window'
import { useTranslation } from 'react-i18next'
import type { Paper } from '@/api/types'
import PaperCard from './PaperCard'
import EmptyState from './EmptyState'

/** P2-1: 数据量低于此阈值时不启用虚拟滚动，避免不必要开销 */
const VIRTUAL_THRESHOLD = 50
/** 虚拟列表每行高度（含 16px 间距），PaperCard 标题+作者+3行摘要+标签约 234px */
const ROW_HEIGHT = 250
/** 每行渲染 2 张卡片（保持与降级模式一致的 2 列网格） */
const COLUMNS = 2
/** 虚拟列表可视区最大高度，超出后内部滚动 */
const MAX_LIST_HEIGHT = 600

interface Props {
  items: Paper[]
  loading: boolean
  total: number
  page: number
  pageSize: number
  onPage: (p: number) => void
}

export default function PaperList({ items, loading, total, page, pageSize, onPage }: Props) {
  const { t } = useTranslation()
  // P2-1: 测量容器宽度，react-window FixedSizeList 需要数值宽度
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const update = () => setWidth(el.clientWidth)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  if (loading) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} active paragraph={{ rows: 4 }} style={{ padding: 16, background: '#fff', borderRadius: 12 }} />
        ))}
      </div>
    )
  }

  if (!items.length) {
    return <EmptyState description={t('search.noResults')} />
  }

  const pagination = (
    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 24 }}>
      <Pagination
        current={page}
        pageSize={pageSize}
        total={total}
        onChange={onPage}
        showTotal={(total) => t('common.total', { count: total })}
        showSizeChanger={false}
      />
    </div>
  )

  // P2-1: 降级策略 — 数据量 < 50 条时使用普通渲染
  if (items.length < VIRTUAL_THRESHOLD) {
    return (
      <>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
          {items.map((p) => (
            <PaperCard key={p.id} paper={p} />
          ))}
        </div>
        {pagination}
      </>
    )
  }

  // P2-1: 虚拟滚动 — 数据量 ≥ 50 时启用 react-window
  const rowCount = Math.ceil(items.length / COLUMNS)
  const listHeight = Math.min(rowCount * ROW_HEIGHT, MAX_LIST_HEIGHT)

  const renderRow = ({
    index,
    style,
    data,
  }: {
    index: number
    style: React.CSSProperties
    data: Paper[]
  }) => {
    const startIdx = index * COLUMNS
    const rowItems = data.slice(startIdx, startIdx + COLUMNS)
    return (
      <div style={style}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(${COLUMNS}, 1fr)`,
            gap: 16,
            height: '100%',
            paddingRight: 8,
          }}
        >
          {rowItems.map((p) => (
            <PaperCard key={p.id} paper={p} />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div ref={containerRef}>
      {width > 0 && (
        <List
          height={listHeight}
          width={width}
          itemCount={rowCount}
          itemSize={ROW_HEIGHT}
          itemData={items}
        >
          {renderRow}
        </List>
      )}
      {pagination}
    </div>
  )
}
