/** PaperForge 综述多格式导出工具 — Markdown / Word / LaTeX / PDF */
import type { AskReference } from '@/api/types'
import { formatAuthors } from './format'

// ---------------------------------------------------------------------------
// 通用工具
// ---------------------------------------------------------------------------

/** 触发浏览器下载 */
function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/** 转义 HTML 特殊字符 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** 构建单条参考文献的纯文本引用 */
function refToText(ref: AskReference, index: number): string {
  const parts: string[] = [`[${index + 1}]`, ref.title]
  if (ref.authors && ref.authors.length > 0) parts.push(formatAuthors(ref.authors, 3))
  if (ref.year) parts.push(String(ref.year))
  parts.push(`arXiv:${ref.id}`)
  return parts.join('. ')
}

/** 构建参考文献的 HTML 片段 */
function refsToHtml(refs: AskReference[]): string {
  if (!refs.length) return ''
  const items = refs.map((r, i) => `<li>${escapeHtml(refToText(r, i))}</li>`).join('')
  return `<h2>参考文献</h2><ol>${items}</ol>`
}

// ---------------------------------------------------------------------------
// Markdown → LaTeX 转换（基础版，处理标题/粗体/斜体/代码/列表/引用/链接）
// ---------------------------------------------------------------------------

function markdownToLatex(md: string): string {
  const lines = md.split('\n')
  const result: string[] = []
  let inItemize = false
  let inEnumerate = false
  let inVerbatim = false

  const closeLists = () => {
    if (inItemize) {
      result.push('\\end{itemize}')
      inItemize = false
    }
    if (inEnumerate) {
      result.push('\\end{enumerate}')
      inEnumerate = false
    }
  }

  const inline = (text: string): string =>
    text
      .replace(/`([^`]+)`/g, '\\texttt{$1}')
      .replace(/\*\*([^*]+)\*\*/g, '\\textbf{$1}')
      .replace(/\*([^*]+)\*/g, '\\textit{$1}')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '\\href{$2}{$1}')

  for (const line of lines) {
    // 代码块开关
    if (line.trim().startsWith('```')) {
      if (inVerbatim) {
        result.push('\\end{verbatim}')
        inVerbatim = false
      } else {
        result.push('\\begin{verbatim}')
        inVerbatim = true
      }
      continue
    }
    if (inVerbatim) {
      result.push(line)
      continue
    }

    const isItem = /^\s*[-*+]\s/.test(line)
    const isEnum = /^\s*\d+\.\s/.test(line)
    if (!isItem && !isEnum) closeLists()

    if (line.startsWith('### ')) {
      result.push(`\\subsubsection{${inline(line.slice(4))}}`)
    } else if (line.startsWith('## ')) {
      result.push(`\\subsection{${inline(line.slice(3))}}`)
    } else if (line.startsWith('# ')) {
      result.push(`\\section{${inline(line.slice(2))}}`)
    } else if (isItem) {
      if (!inItemize) {
        result.push('\\begin{itemize}')
        inItemize = true
      }
      result.push(`  \\item ${inline(line.replace(/^\s*[-*+]\s+/, ''))}`)
    } else if (isEnum) {
      if (!inEnumerate) {
        result.push('\\begin{enumerate}')
        inEnumerate = true
      }
      result.push(`  \\item ${inline(line.replace(/^\s*\d+\.\s+/, ''))}`)
    } else if (line.startsWith('> ')) {
      result.push(`\\begin{quote}\n${inline(line.slice(2))}\n\\end{quote}`)
    } else if (line.trim() === '---' || line.trim() === '***') {
      result.push('\\noindent\\rule{\\textwidth}{0.4pt}')
    } else if (line.trim() === '') {
      result.push('')
    } else {
      result.push(inline(line))
    }
  }
  closeLists()
  if (inVerbatim) result.push('\\end{verbatim}')
  return result.join('\n')
}

// ---------------------------------------------------------------------------
// Markdown → HTML 转换（基础版，供 Word 导出使用）
// ---------------------------------------------------------------------------

export function markdownToHtml(md: string): string {
  const lines = md.split('\n')
  const result: string[] = []
  let inUl = false
  let inOl = false
  let inPre = false

  const closeLists = () => {
    if (inUl) {
      result.push('</ul>')
      inUl = false
    }
    if (inOl) {
      result.push('</ol>')
      inOl = false
    }
  }

  const inline = (text: string): string =>
    text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>')

  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (inPre) {
        result.push('</pre>')
        inPre = false
      } else {
        closeLists()
        result.push('<pre>')
        inPre = true
      }
      continue
    }
    if (inPre) {
      result.push(line)
      continue
    }

    const isItem = /^\s*[-*+]\s/.test(line)
    const isEnum = /^\s*\d+\.\s/.test(line)
    if (!isItem && !isEnum) closeLists()

    if (line.startsWith('### ')) {
      result.push(`<h3>${inline(line.slice(4))}</h3>`)
    } else if (line.startsWith('## ')) {
      result.push(`<h2>${inline(line.slice(3))}</h2>`)
    } else if (line.startsWith('# ')) {
      result.push(`<h1>${inline(line.slice(2))}</h1>`)
    } else if (isItem) {
      if (!inUl) {
        result.push('<ul>')
        inUl = true
      }
      result.push(`<li>${inline(line.replace(/^\s*[-*+]\s+/, ''))}</li>`)
    } else if (isEnum) {
      if (!inOl) {
        result.push('<ol>')
        inOl = true
      }
      result.push(`<li>${inline(line.replace(/^\s*\d+\.\s+/, ''))}</li>`)
    } else if (line.startsWith('> ')) {
      result.push(`<blockquote>${inline(line.slice(2))}</blockquote>`)
    } else if (line.trim() === '') {
      result.push('')
    } else {
      result.push(`<p>${inline(line)}</p>`)
    }
  }
  closeLists()
  if (inPre) result.push('</pre>')
  return result.join('\n')
}

// ---------------------------------------------------------------------------
// 四种导出函数
// ---------------------------------------------------------------------------

/** 导出为 Markdown（.md） */
export function exportMarkdown(
  content: string,
  refs: AskReference[],
  filename: string,
): void {
  let md = content
  if (refs.length) {
    md += '\n\n## 参考文献\n\n'
    md += refs.map((r, i) => refToText(r, i)).join('\n\n')
  }
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' })
  download(blob, `${filename}.md`)
}

/** 导出为 Word（mhtml 方案，零依赖，.doc） */
export function exportWord(
  html: string,
  refs: AskReference[],
  filename: string,
): void {
  const refsHtml = refsToHtml(refs)
  const fullHtml =
    '<html xmlns:o="urn:schemas-microsoft-com:office:office" ' +
    'xmlns:w="urn:schemas-microsoft-com:office:word" ' +
    'xmlns="http://www.w3.org/TR/REC-html40">' +
    '<head><meta charset="utf-8"><style>' +
    'body{font-family:Georgia,serif;font-size:14px;line-height:1.9;color:#1a1a2e}' +
    'h1{font-size:1.5em}h2{font-size:1.3em}h3{font-size:1.15em}' +
    'table{border-collapse:collapse;width:100%}' +
    'th,td{border:1px solid #ccc;padding:8px}' +
    'th{background:#f5f5f5}' +
    'code{background:#f1f5f9;padding:2px 6px}' +
    'blockquote{border-left:3px solid #1e40af;padding-left:12px;color:#666}' +
    '</style></head><body>' +
    html +
    refsHtml +
    '</body></html>'
  const blob = new Blob(['\ufeff', fullHtml], { type: 'application/msword' })
  download(blob, `${filename}.doc`)
}

/** 导出为 LaTeX（.tex） */
export function exportLatex(
  content: string,
  refs: AskReference[],
  filename: string,
): void {
  const latexBody = markdownToLatex(content)
  const bibItems = refs
    .map((r, i) => {
      const parts: string[] = [r.title]
      if (r.authors && r.authors.length > 0) parts.push(formatAuthors(r.authors, 3))
      if (r.year) parts.push(String(r.year))
      parts.push(`arXiv:${r.id}`)
      return `\\bibitem{${i + 1}} ${parts.join('. ')}.`
    })
    .join('\n')

  const latex =
    `\\documentclass[11pt]{article}\n` +
    `\\usepackage[utf8]{inputenc}\n` +
    `\\usepackage[T1]{fontenc}\n` +
    `\\usepackage{hyperref}\n` +
    `\\usepackage{geometry}\n` +
    `\\usepackage{booktabs}\n` +
    `\\geometry{a4paper, margin=1in}\n\n` +
    `\\title{${filename.replace(/_/g, ' ')}}\n` +
    `\\date{\\today}\n\n` +
    `\\begin{document}\n` +
    `\\maketitle\n\n` +
    `${latexBody}\n\n` +
    (refs.length
      ? `\\begin{thebibliography}{99}\n${bibItems}\n\\end{thebibliography}\n\n`
      : '') +
    `\\end{document}\n`

  const blob = new Blob([latex], { type: 'application/x-tex;charset=utf-8' })
  download(blob, `${filename}.tex`)
}

/** 导出为 PDF（调用浏览器打印，用户可选择另存为 PDF） */
export function exportPdf(
  html: string,
  refs: AskReference[],
  filename: string,
): void {
  const refsHtml = refsToHtml(refs)
  const printWindow = window.open('', '_blank')
  if (!printWindow) return
  printWindow.document.write(
    '<html><head><title>' +
      escapeHtml(filename) +
      '</title><style>' +
      'body{font-family:Georgia,"Times New Roman",serif;padding:40px;line-height:1.9;color:#1a1a2e;max-width:800px;margin:0 auto}' +
      'h1{font-size:1.5em}h2{font-size:1.3em}h3{font-size:1.15em}' +
      'table{border-collapse:collapse;width:100%}' +
      'th,td{border:1px solid #ccc;padding:8px}' +
      'th{background:#f5f5f5}' +
      'code{background:#f1f5f9;padding:2px 6px;border-radius:3px}' +
      'blockquote{border-left:3px solid #1e40af;padding-left:12px;color:#666}' +
      'ol{padding-left:1.5em}' +
      '</style></head><body>' +
      html +
      refsHtml +
      '</body></html>',
  )
  printWindow.document.close()
  printWindow.focus()
  setTimeout(() => printWindow.print(), 300)
}
