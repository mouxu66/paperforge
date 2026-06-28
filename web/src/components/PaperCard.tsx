import { Card, Tag, Tooltip, Space } from 'antd'
import {
  FileTextOutlined,
  LinkOutlined,
  DatabaseOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { Paper } from '@/api/types'
import { arxivUrl, formatAuthors, formatSize } from '@/utils/format'
import { SOURCE_COLOR, CATEGORY_COLOR } from '@/utils/constants'
import FavoriteButton from './FavoriteButton'

export default function PaperCard({ paper }: { paper: Paper }) {
  const navigate = useNavigate()
  const { t } = useTranslation()

  return (
    <Card
      className="pf-glass-card"
      variant="borderless"
      style={{ height: '100%' }}
      title={
        <div
          className="pf-link pf-serif"
          style={{
            fontSize: 15,
            fontWeight: 600,
            lineHeight: 1.45,
            paddingRight: 8,
          }}
          onClick={() => navigate(`/paper/${paper.id}`)}
        >
          {paper.title}
        </div>
      }
      extra={
        <FavoriteButton paper={paper} variant="text" size="small" stopPropagation />
      }
    >
      <div
        style={{
          marginBottom: 10,
          fontSize: 14,
          lineHeight: 1.8,
          color: '#475569',
        }}
      >
        {formatAuthors(paper.authors)}
        <span style={{ color: '#cbd5e1', margin: '0 8px' }}>·</span>
        <span>{paper.year}</span>
        <span style={{ color: '#cbd5e1', margin: '0 8px' }}>·</span>
        <Tag color={SOURCE_COLOR[paper.source]} style={{ marginInlineEnd: 0 }}>
          {paper.source.toUpperCase()}
        </Tag>
        <span
          style={{
            display: 'inline-block',
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: CATEGORY_COLOR[paper.category] || '#94a3b8',
            marginLeft: 8,
            verticalAlign: 'middle',
          }}
        />
      </div>

      <div
        className="pf-abstract"
        style={{
          display: '-webkit-box',
          WebkitLineClamp: 3,
          WebkitBoxOrient: 'vertical',
          overflow: 'hidden',
        }}
      >
        {paper.abstract}
      </div>

      <div
        style={{
          marginTop: 14,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <Space size={6} wrap>
          {/* B2: Semantic Scholar 研究领域标签（蓝色，区别于普通标签） */}
          {paper.fieldsOfStudy?.slice(0, 2).map((f) => (
            <Tag
              key={`fos-${f}`}
              bordered={false}
              color="blue"
              style={{ fontSize: 12, lineHeight: 1.6 }}
            >
              {f}
            </Tag>
          ))}
          {paper.tags.slice(0, 3).map((t) => (
            <Tag
              key={t}
              bordered={false}
              color="default"
              style={{ fontSize: 12, lineHeight: 1.6 }}
            >
              {t}
            </Tag>
          ))}
        </Space>
        <Space
          size={16}
          style={{ fontSize: 13, lineHeight: 1.8, color: '#94a3b8' }}
        >
          <span>
            <DatabaseOutlined /> {paper.chunkCount} chunks
          </span>
          <span>
            <FileTextOutlined /> {formatSize(paper.indexSize)}
          </span>
          <Tooltip title={t('paper.arxivOriginal')}>
            <a
              href={paper.pdfUrl || arxivUrl(paper.id)}
              target="_blank"
              rel="noreferrer"
              style={{ fontSize: 13 }}
            >
              <LinkOutlined /> PDF
            </a>
          </Tooltip>
        </Space>
      </div>
    </Card>
  )
}
