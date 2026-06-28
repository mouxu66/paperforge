import { useRef, useState } from 'react'
import { Typography, Input, Button, Card, Space, Tag, Empty, Spin, message } from 'antd'
import {
  EditOutlined,
  DownloadOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { fetchGenerate } from '@/api/generate'
import type { GenerateResponse } from '@/api/types'
import ReferenceCard from '@/components/ReferenceCard'
import { exportMarkdown, exportWord, exportLatex, exportPdf } from '@/utils/export'

const { TextArea } = Input

export default function GeneratePage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [topic, setTopic] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<GenerateResponse | null>(null)
  const markdownRef = useRef<HTMLDivElement>(null)

  const handleGenerate = async () => {
    const t = topic.trim()
    if (!t || loading) return
    setLoading(true)
    setResult(null)
    try {
      const res = await fetchGenerate(t)
      setResult(res)
    } catch {
      // 错误已在 generateHttp 拦截器中提示
    } finally {
      setLoading(false)
    }
  }

  const filename = `survey_${topic.trim().slice(0, 20).replace(/\s+/g, '_') || 'untitled'}`

  const handleExportMarkdown = () => {
    if (!result) return
    exportMarkdown(result.content, result.references, filename)
    message.success(t('generate.exportedMarkdown'))
  }

  const handleExportWord = () => {
    if (!result) return
    const html = markdownRef.current?.innerHTML || ''
    exportWord(html, result.references, filename)
    message.success(t('generate.exportedWord'))
  }

  const handleExportLatex = () => {
    if (!result) return
    exportLatex(result.content, result.references, filename)
    message.success(t('generate.exportedLatex'))
  }

  const handleExportPdf = () => {
    if (!result) return
    const html = markdownRef.current?.innerHTML || ''
    exportPdf(html, result.references, filename)
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <div style={{ marginBottom: 20 }}>
        <Typography.Title level={3} className="pf-page-title" style={{ marginBottom: 0 }}>
          {t('generate.title')}
        </Typography.Title>
        <div className="pf-subtitle">{t('generate.subtitle')}</div>
      </div>

      {/* 输入区 */}
      <Card className="pf-glass-card" variant="borderless" style={{ marginBottom: 20, padding: 8 }}>
        <TextArea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={t('generate.placeholder')}
          rows={3}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault()
              void handleGenerate()
            }
          }}
          style={{ borderRadius: 8, resize: 'none' }}
        />
        <div
          style={{
            marginTop: 12,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <span style={{ fontSize: 12, color: '#94a3b8' }}>
            {t('generate.hint')}
          </span>
          <Button
            type="primary"
            icon={<EditOutlined />}
            onClick={handleGenerate}
            loading={loading}
          >
            {t('generate.submit')}
          </Button>
        </div>
      </Card>

      {/* 加载中 */}
      {loading && (
        <Card className="pf-glass-card" variant="borderless" style={{ textAlign: 'center', padding: 48 }}>
          <Spin size="large" />
          <div style={{ marginTop: 16, color: '#64748b', fontSize: 14 }}>
            {t('generate.loading')}
          </div>
        </Card>
      )}

      {/* 结果区 */}
      {!loading && result && (
        <>
          <Card
            className="pf-glass-card"
            variant="borderless"
            style={{ marginBottom: 16, padding: 8 }}
            title={
              <Space>
                <FileTextOutlined style={{ color: '#1e40af' }} />
                <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
                  {t('generate.bodyTitle')}
                </span>
              </Space>
            }
          >
            <div className="pf-markdown" ref={markdownRef}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {result.content}
              </ReactMarkdown>
            </div>
          </Card>

          {/* 导出按钮区 */}
          <Space wrap style={{ marginBottom: 20 }}>
            <Button icon={<DownloadOutlined />} onClick={handleExportMarkdown}>
              {t('generate.exportMarkdown')}
            </Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportWord}>
              {t('generate.exportWord')}
            </Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportLatex}>
              {t('generate.exportLatex')}
            </Button>
            <Button icon={<DownloadOutlined />} onClick={handleExportPdf}>
              {t('generate.exportPdf')}
            </Button>
          </Space>

          {result.references && result.references.length > 0 ? (
            <div>
              <div style={{ marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
                <FileTextOutlined style={{ color: '#1e40af' }} />
                <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600, color: '#1a1a2e' }}>
                  {t('generate.references')}
                </span>
                <Tag color="blue" style={{ marginInlineStart: 0 }}>
                  {result.references.length}
                </Tag>
              </div>
              {result.references.map((refItem, idx) => (
                <ReferenceCard
                  key={refItem.id || idx}
                  refItem={refItem}
                  index={idx}
                  bracketed
                  onClick={() => navigate(`/paper/${refItem.id}`)}
                />
              ))}
            </div>
          ) : (
            <Empty description={t('generate.emptyReferences')} />
          )}
        </>
      )}

      {/* 空状态 */}
      {!loading && !result && (
        <Card className="pf-glass-card" variant="borderless" style={{ textAlign: 'center', padding: 48 }}>
          <EditOutlined style={{ fontSize: 40, color: '#cbd5e1', marginBottom: 16 }} />
          <div className="pf-serif" style={{ fontSize: 16, color: '#64748b', marginBottom: 6 }}>
            {t('generate.emptyTitle')}
          </div>
          <div style={{ fontSize: 13, color: '#94a3b8' }}>
            {t('generate.emptyDesc')}
          </div>
        </Card>
      )}
    </div>
  )
}
