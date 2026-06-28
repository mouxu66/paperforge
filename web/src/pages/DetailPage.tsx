import { useEffect, useMemo, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Typography,
  Card,
  Tag,
  Button,
  Space,
  Skeleton,
  Result,
  Divider,
  Tabs,
  Statistic,
  Empty,
  List,
} from 'antd'
import { ArrowLeftOutlined, LinkOutlined } from '@ant-design/icons'
import { fetchPaperById } from '@/api/papers'
import { fetchCitationRelations } from '@/api/notes'
import type { CitationRelations, Paper } from '@/api/types'
import { arxivUrl, formatAuthors } from '@/utils/format'
import { SOURCE_COLOR } from '@/utils/constants'
import FavoriteButton from '@/components/FavoriteButton'
import AbstractTab from '@/components/AbstractTab'
import PdfTab from '@/components/PdfTab'
import CiteTab from '@/components/CiteTab'
import NoteList from '@/components/NoteList'

const { Title, Text } = Typography

/**
 * 引用关系面板 —— 被引用次数 + 基于语义相似度推荐的相关论文列表。
 * 进入该 Tab 时挂载并拉取引用关系数据。
 */
function CitationRelationsTab({ paper }: { paper: Paper }) {
  const { t } = useTranslation()
  const [relations, setRelations] = useState<CitationRelations | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    setLoading(true)
    fetchCitationRelations(paper.id)
      .then((r) => {
        if (active) setRelations(r)
      })
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [paper.id])

  if (loading) {
    return <Skeleton active paragraph={{ rows: 4 }} />
  }

  const references = relations?.references ?? []
  const citeCount = relations?.citations ?? paper.citations
  // B2: 有影响力引用数（Semantic Scholar 富化，未富化时不显示）
  const influentialCitations = paper.influentialCitations

  return (
    <div>
      <div style={{ display: 'flex', gap: 32, marginBottom: 8 }}>
        <Statistic title={t('paper.citationCount')} value={citeCount} />
        {influentialCitations != null && (
          <Statistic
            title={t('paper.influentialCitations')}
            value={influentialCitations}
            valueStyle={{ color: '#1e40af' }}
          />
        )}
      </div>
      <Divider />
      <Title level={5} className="pf-serif" style={{ marginBottom: 4 }}>
        {t('paper.relatedPapers')}
      </Title>
      <Text
        type="secondary"
        style={{ fontSize: 12, display: 'block', marginBottom: 12 }}
      >
        {t('paper.relatedBySemantic')}
      </Text>
      {references.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t('paper.noRelatedPapers')}
        />
      ) : (
        <List
          dataSource={references}
          renderItem={(item) => (
            <List.Item style={{ padding: '10px 0' }}>
              <Link to={`/paper/${item.id}`} style={{ flex: 1, minWidth: 0 }}>
                <Text
                  className="pf-serif pf-link"
                  style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.5 }}
                >
                  {item.title}
                </Text>
                <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
                  {formatAuthors(item.authors, 3)}
                  {item.year && (
                    <>
                      <span style={{ margin: '0 8px', color: '#cbd5e1' }}>
                        ·
                      </span>
                      {item.year}
                    </>
                  )}
                </div>
              </Link>
            </List.Item>
          )}
        />
      )}
    </div>
  )
}

export default function DetailPage() {
  const { t } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [paper, setPaper] = useState<Paper | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    let active = true
    setLoading(true)
    setNotFound(false)
    fetchPaperById(id!)
      .then((p) => {
        if (!active) return
        if (!p) setNotFound(true)
        else setPaper(p)
      })
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [id])

  const bibtex = useMemo(() => {
    if (!paper) return ''
    return `@article{${paper.id},
  author = {${paper.authors.join(' and ')}},
  title = {${paper.title}},
  journal = {${paper.journal || 'arXiv preprint'}},
  year = {${paper.year}},
  doi = {${paper.id}}
}`
  }, [paper])

  if (loading) {
    return (
      <div style={{ maxWidth: 900, margin: '0 auto', padding: '0 24px' }}>
        <Skeleton active paragraph={{ rows: 12 }} />
      </div>
    )
  }

  if (notFound || !paper) {
    return (
      <Result
        status="404"
        title={t('paper.paperNotFound')}
        subTitle={t('paper.paperNotFoundDesc', { id })}
        extra={
          <Button type="primary" onClick={() => navigate('/')}>
            {t('common.backHome')}
          </Button>
        }
      />
    )
  }

  const tabItems = [
    {
      key: 'abstract',
      label: t('detail.tabAbstract'),
      children: <AbstractTab paper={paper} />,
    },
    {
      key: 'pdf',
      label: t('detail.tabPdf'),
      children: <PdfTab paper={paper} />,
    },
    {
      key: 'cite',
      label: t('detail.tabCite'),
      children: <CiteTab bibtex={bibtex} />,
    },
    {
      key: 'notes',
      label: t('detail.tabNotes'),
      children: <NoteList paperId={paper.id} />,
    },
    {
      key: 'citations',
      label: t('detail.tabCitations'),
      children: <CitationRelationsTab paper={paper} />,
    },
  ]

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: '0 24px' }}>
      <Link
        to="/"
        style={{ fontSize: 13, color: '#64748b', display: 'inline-flex', alignItems: 'center', gap: 4 }}
      >
        <ArrowLeftOutlined /> {t('paper.backToList')}
      </Link>

      <Card
        className="pf-glass-card"
        variant="borderless"
        style={{ marginTop: 16, padding: 8 }}
      >
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            gap: 16,
          }}
        >
          <div style={{ flex: 1 }}>
            <Title
              level={2}
              className="pf-serif"
              style={{
                fontSize: 28,
                fontWeight: 700,
                lineHeight: 1.35,
                marginBottom: 12,
                color: '#1a1a2e',
              }}
            >
              {paper.title}
            </Title>
            <Text style={{ color: '#666', fontSize: 15, lineHeight: 1.8 }}>
              {formatAuthors(paper.authors, 6)}
            </Text>
            <Divider style={{ margin: '14px 0' }} />
            <Space size={8} wrap>
              <Tag color={SOURCE_COLOR[paper.source]}>
                {paper.source.toUpperCase()}
              </Tag>
              <Tag>{paper.year}</Tag>
              <Tag color="blue">{t('paper.citations')} {paper.citations.toLocaleString()}</Tag>
              {paper.tags.map((t) => (
                <Tag key={t} bordered={false}>
                  {t}
                </Tag>
              ))}
            </Space>
          </div>

          <Space direction="vertical" size="small">
            <FavoriteButton
              paper={paper}
              variant="primary"
              size="middle"
              showLabel
            />
            <Button
              icon={<LinkOutlined />}
              href={paper.pdfUrl || arxivUrl(paper.id)}
              target="_blank"
            >
              {t('paper.viewOriginal')}
            </Button>
          </Space>
        </div>

        <Divider />

        <Tabs
          defaultActiveKey="abstract"
          items={tabItems}
          style={{ minHeight: 320 }}
        />
      </Card>
    </div>
  )
}
