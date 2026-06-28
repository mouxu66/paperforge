import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Popover, Spin, Typography, Tag, Empty } from 'antd'
import { LinkOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import type { Paper } from '@/api/types'
import { fetchPaperById } from '@/api/papers'

/** 模块级论文缓存：避免同一篇论文多次悬停时重复请求 */
const paperCache = new Map<string, Paper | null>()

interface ReferencePreviewProps {
  paperId: string
  children?: React.ReactNode
}

/**
 * 引用悬浮预览组件。
 *
 * 包裹 [@paper_id] 标记，鼠标悬停 300ms 后调用 GET /api/papers/{id}
 * 获取论文详情，在浮窗中显示标题、作者、摘要前 100 字、年份、PDF 链接。
 *
 * - 使用模块级缓存，同一篇论文不重复请求
 * - 加载中显示 Spin
 * - 论文不存在显示 Empty
 * - 标题可点击跳转到论文详情页
 */
export default function ReferencePreview({ paperId, children }: ReferencePreviewProps) {
  const { t } = useTranslation()
  const [paper, setPaper] = useState<Paper | null | undefined>(undefined)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    // 命中缓存则直接使用
    if (paperCache.has(paperId)) {
      setPaper(paperCache.get(paperId) ?? null)
      return
    }
    // 延迟加载：仅在 Popover 首次展开时请求
    if (paper !== undefined) return
  }, [paperId, paper])

  const loadPaper = async () => {
    if (paperCache.has(paperId)) {
      setPaper(paperCache.get(paperId) ?? null)
      return
    }
    setLoading(true)
    try {
      const p = await fetchPaperById(paperId)
      paperCache.set(paperId, p)
      setPaper(p)
    } catch {
      paperCache.set(paperId, null)
      setPaper(null)
    } finally {
      setLoading(false)
    }
  }

  const abstractSnippet =
    paper?.abstract ? paper.abstract.slice(0, 100) + (paper.abstract.length > 100 ? '…' : '') : ''

  const content = (
    <div style={{ width: 320, maxWidth: '90vw' }}>
      {loading ? (
        <div style={{ textAlign: 'center', padding: 24 }}>
          <Spin size="small" />
        </div>
      ) : paper ? (
        <div>
          <Link
            to={`/paper/${paper.id}`}
            style={{ display: 'block', marginBottom: 8 }}
          >
            <Typography.Text className="pf-serif" strong style={{ fontSize: 14, color: '#1e40af' }}>
              {paper.title}
            </Typography.Text>
          </Link>
          <div style={{ marginBottom: 6, fontSize: 12, color: '#64748b' }}>
            {paper.authors.slice(0, 3).join(', ')}
            {paper.authors.length > 3 ? ' et al.' : ''}
          </div>
          <div style={{ marginBottom: 8 }}>
            <Tag color="blue" style={{ fontSize: 11 }}>{paper.year}</Tag>
            {paper.journal && <Tag style={{ fontSize: 11 }}>{paper.journal}</Tag>}
          </div>
          {abstractSnippet && (
            <Typography.Paragraph
              style={{ fontSize: 12, color: '#475569', margin: 0, lineHeight: 1.6 }}
              ellipsis={{ rows: 3 }}
            >
              {abstractSnippet}
            </Typography.Paragraph>
          )}
          {paper.pdfUrl && (
            <a
              href={paper.pdfUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginTop: 8, fontSize: 12 }}
            >
              <LinkOutlined /> PDF
            </a>
          )}
        </div>
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <span
              style={{ fontSize: 12, color: '#94a3b8' }}
              dangerouslySetInnerHTML={{
                __html: t('outlinePreview.paperNotFound', { id: paperId }),
              }}
            />
          }
        />
      )}
    </div>
  )

  return (
    <Popover
      content={content}
      trigger="hover"
      mouseEnterDelay={0.3}
      mouseLeaveDelay={0.2}
      onOpenChange={(open) => {
        if (open && paper === undefined) void loadPaper()
      }}
      placement="top"
    >
      <span
        data-paper-id={paperId}
        style={{
          color: '#1e40af',
          borderBottom: '1px dashed #1e40af',
          cursor: 'pointer',
          padding: '0 1px',
        }}
      >
        {children ?? `[@${paperId}]`}
      </span>
    </Popover>
  )
}

/**
 * 工具函数：在文本节点中检测 [@paper_id] 并替换为 ReferencePreview 组件。
 *
 * 用于 react-markdown 的 components 覆盖：将段落、列表项等元素的字符串子节点
 * 按 [@xxx] 模式拆分，匹配部分渲染为可悬浮预览的 <ReferencePreview>。
 */
const CITE_PATTERN = /\[@([^\]\s]+)\]/g

export function renderTextWithCitations(node: React.ReactNode): React.ReactNode {
  if (typeof node === 'string') {
    const parts: React.ReactNode[] = []
    let lastIndex = 0
    let match: RegExpExecArray | null
    // 重置正则的 lastIndex（全局正则需要）
    CITE_PATTERN.lastIndex = 0
    let key = 0
    while ((match = CITE_PATTERN.exec(node)) !== null) {
      if (match.index > lastIndex) {
        parts.push(node.slice(lastIndex, match.index))
      }
      parts.push(<ReferencePreview key={`cite-${key++}`} paperId={match[1]} />)
      lastIndex = match.index + match[0].length
    }
    if (lastIndex < node.length) {
      parts.push(node.slice(lastIndex))
    }
    return parts.length > 0 ? parts : node
  }
  if (Array.isArray(node)) {
    return node.map((child) => renderTextWithCitations(child))
  }
  return node
}
