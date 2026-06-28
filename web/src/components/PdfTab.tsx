import { Card } from 'antd'
import PDFViewer from './PDFViewer'
import type { Paper } from '@/api/types'

interface PdfTabProps {
  paper: Paper
}

/**
 * 解析论文的 PDF 预览来源。
 *
 * B4 起改用后端 pdf-proxy 代理地址，避免 pdfjs-dist 直连 arXiv 时的 CORS 限制。
 * 代理路由：GET /api/papers/{paper_id}/pdf-proxy
 */
function resolvePdfProxyUrl(paper: Paper): string {
  // Vite 开发环境通过 /api 代理到 FastAPI；生产环境同源直连
  const base = import.meta.env.VITE_API_BASE || '/api'
  return `${base}/papers/${paper.id}/pdf-proxy`
}

/** PDF 预览面板：B4 起使用 PDFViewer 替代 iframe，支持文本高亮与批注 */
export default function PdfTab({ paper }: PdfTabProps) {
  return (
    <Card
      variant="borderless"
      style={{ padding: 0, overflow: 'hidden', borderRadius: 12 }}
      bodyStyle={{ padding: 12 }}
    >
      <PDFViewer paper={paper} pdfUrl={resolvePdfProxyUrl(paper)} />
    </Card>
  )
}
