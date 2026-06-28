import { Tooltip, Button, message } from 'antd'
import { CopyOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'

interface CiteTabProps {
  bibtex: string
}

/** 引用格式面板：展示 BibTeX 条目并提供一键复制 */
export default function CiteTab({ bibtex }: CiteTabProps) {
  const { t } = useTranslation()
  const handleCopyBibtex = async () => {
    try {
      await navigator.clipboard.writeText(bibtex)
      message.success(t('common.copied'))
    } catch {
      message.error(t('common.copyFailed'))
    }
  }

  return (
    <div>
      <div
        style={{
          position: 'relative',
          background: '#f5f5f5',
          borderRadius: 10,
          padding: '16px 20px',
          fontFamily:
            "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace",
          fontSize: 13,
          lineHeight: 1.7,
          color: '#1a1a2e',
          overflowX: 'auto',
        }}
      >
        <Tooltip title={t('paper.copyBibtex')}>
          <Button
            type="text"
            size="small"
            icon={<CopyOutlined />}
            onClick={handleCopyBibtex}
            style={{
              position: 'absolute',
              top: 12,
              right: 12,
              color: '#64748b',
            }}
          />
        </Tooltip>
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {bibtex}
        </pre>
      </div>
      <div style={{ marginTop: 12, fontSize: 12, color: '#94a3b8' }}>
        {t('paper.bibtexTitle')}
      </div>
    </div>
  )
}
