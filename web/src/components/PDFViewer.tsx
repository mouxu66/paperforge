import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  ColorPicker,
  Empty,
  Input,
  List,
  message,
  Popover,
  Space,
  Spin,
  Tag,
  Tooltip,
} from 'antd'
import {
  DeleteOutlined,
  HighlightOutlined,
  ReadOutlined,
} from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import * as pdfjsLib from 'pdfjs-dist'
// Vite 通过 ?url 后缀将 worker 文件作为 URL 资源导入
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import type { Paper } from '@/api/types'
import type { HighlightColor } from '@/api/types'

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl

// ---------------------------------------------------------------------------
// 类型与常量
// ---------------------------------------------------------------------------
/** 单条高亮记录（localStorage 主存储，与后端 PdfAnnotation 结构对齐） */
interface HighlightRecord {
  id: string
  paperId: string
  page: number
  /** 归一化矩形列表：[x, y, w, h, ...]，坐标为 0-1 相对页面尺寸 */
  rects: number[]
  /** 选中的原文（便于列表展示与跳转） */
  text: string
  color: string
  note: string
  createdAt: string
}

const COLOR_PRESETS: { labelKey: string; value: HighlightColor }[] = [
  { labelKey: 'pdf.colorYellow', value: '#FFEB3B' },
  { labelKey: 'pdf.colorBlue', value: '#4FC3F7' },
  { labelKey: 'pdf.colorGreen', value: '#81C784' },
  { labelKey: 'pdf.colorPink', value: '#F48FB1' },
]

const STORAGE_KEY_PREFIX = 'pf_pdf_highlights_'
const RENDER_SCALE = 1.4
/** 初始渲染页数上限，超出后滚动到底部时懒加载更多 */
const INITIAL_PAGE_BATCH = 3

interface PDFViewerProps {
  paper: Paper
  /** PDF 数据源 URL；若为空则使用 arXiv 默认 PDF 链接 */
  pdfUrl: string
}

/** 生成 UUID（避免依赖额外包） */
function uuid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `h_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

/** 按 paperId 加载高亮记录（localStorage） */
function loadHighlights(paperId: string): HighlightRecord[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + paperId)
    if (!raw) return []
    const arr = JSON.parse(raw) as HighlightRecord[]
    return Array.isArray(arr) ? arr : []
  } catch {
    return []
  }
}

/** 保存高亮记录到 localStorage */
function saveHighlights(paperId: string, records: HighlightRecord[]): void {
  try {
    localStorage.setItem(STORAGE_KEY_PREFIX + paperId, JSON.stringify(records))
  } catch {
    // 存储满或被禁用时静默降级
  }
}

/**
 * 将 window.Selection 的若干 ClientRect 转换为相对页面容器的归一化矩形。
 * 返回扁平数组 [x, y, w, h, ...]，坐标范围 0-1，便于跨缩放比例复现高亮。
 */
function selectionToNormalizedRects(
  selection: Selection,
  pageEl: HTMLElement,
  pageWidth: number,
  pageHeight: number,
): number[] {
  const range = selection.getRangeAt(0)
  const rects = range.getClientRects()
  const containerRect = pageEl.getBoundingClientRect()
  const out: number[] = []
  for (let i = 0; i < rects.length; i++) {
    const r = rects[i]
    const x = (r.left - containerRect.left) / pageWidth
    const y = (r.top - containerRect.top) / pageHeight
    const w = r.width / pageWidth
    const h = r.height / pageHeight
    // 过滤掉退化的零尺寸矩形
    if (w > 0.001 && h > 0.001) {
      out.push(x, y, w, h)
    }
  }
  return out
}

export default function PDFViewer({ paper, pdfUrl }: PDFViewerProps) {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  // 页面渲染容器映射：pageNo -> { wrapper, canvas, textLayerDiv, width, height }
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  const abortRef = useRef<boolean>(false)
  // 共享的 PDF 文档代理（加载一次，渲染与翻页共用，避免重复 getDocument）
  const pdfDocRef = useRef<pdfjsLib.PDFDocumentProxy | null>(null)
  // 待渲染页队列（totalPages 就绪后渲染前 N 页）
  const [pendingRender, setPendingRender] = useState(0)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>('')
  const [totalPages, setTotalPages] = useState(0)
  const [renderedPages, setRenderedPages] = useState(INITIAL_PAGE_BATCH)
  const [highlights, setHighlights] = useState<HighlightRecord[]>(() =>
    loadHighlights(paper.id),
  )
  const [currentColor, setCurrentColor] = useState<string>('#FFEB3B')
  const [activePage, setActivePage] = useState<number>(1)
  // 待确认的高亮（选中文本后弹出的 Popover 数据）
  const [pending, setPending] = useState<{
    page: number
    rects: number[]
    text: string
    note: string
  } | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(true)

  // 同步高亮到 localStorage
  useEffect(() => {
    saveHighlights(paper.id, highlights)
  }, [highlights, paper.id])

  // 加载 PDF（单次 getDocument，结果存入 pdfDocRef 供渲染复用）
  useEffect(() => {
    abortRef.current = false
    setLoading(true)
    setError('')
    pageRefs.current.clear()
    setRenderedPages(INITIAL_PAGE_BATCH)
    setPendingRender(0)

    let task: pdfjsLib.PDFDocumentLoadingTask | null = null

    const load = async () => {
      try {
        task = pdfjsLib.getDocument({
          url: pdfUrl,
          // 关闭 CMap 警告：使用标准字体路径
          cMapUrl: 'https://unpkg.com/pdfjs-dist@4.7.76/cmaps/',
          cMapPacked: true,
        })
        const pdfDoc = await task.promise
        if (abortRef.current) {
          pdfDoc.destroy().catch(() => {})
          return
        }
        pdfDocRef.current = pdfDoc
        setTotalPages(pdfDoc.numPages)
        setActivePage(1)
        // 触发首次渲染
        setPendingRender((n) => n + 1)
      } catch (e) {
        if (abortRef.current) return
        const msg = e instanceof Error ? e.message : String(e)
        setError(t('pdf.loadFailed', { msg }))
      } finally {
        if (!abortRef.current) setLoading(false)
      }
    }
    load()

    return () => {
      abortRef.current = true
      task?.destroy().catch(() => {})
      pdfDocRef.current?.destroy().catch(() => {})
      pdfDocRef.current = null
    }
  }, [pdfUrl, t])

  // 渲染指定页（canvas + text layer）
  const renderPage = useCallback(
    async (pageNo: number) => {
      const pdfDoc = pdfDocRef.current
      if (!pdfDoc) return
      const wrapper = pageRefs.current.get(pageNo)
      if (!wrapper) return
      // 已渲染则跳过
      if (wrapper.dataset.rendered === '1') return
      wrapper.dataset.rendered = '1'

      try {
        const page = await pdfDoc.getPage(pageNo)
        const viewport = page.getViewport({ scale: RENDER_SCALE })
        const canvas = wrapper.querySelector('canvas')
        const textDiv = wrapper.querySelector<HTMLElement>('.pf-pdf-text-layer')
        if (!canvas || !textDiv) return

        const ctx = canvas.getContext('2d')
        if (!ctx) return
        // 高 DPI 渲染
        const outputScale = window.devicePixelRatio || 1
        canvas.width = Math.floor(viewport.width * outputScale)
        canvas.height = Math.floor(viewport.height * outputScale)
        canvas.style.width = `${viewport.width}px`
        canvas.style.height = `${viewport.height}px`
        wrapper.style.width = `${viewport.width}px`
        wrapper.style.height = `${viewport.height}px`
        wrapper.dataset.pageWidth = String(viewport.width)
        wrapper.dataset.pageHeight = String(viewport.height)

        const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined
        await page.render({
          canvasContext: ctx,
          viewport,
          transform: transform as unknown as number[],
        }).promise

        // 文本层（透明覆盖在 canvas 上，使文字可选）
        textDiv.style.width = `${viewport.width}px`
        textDiv.style.height = `${viewport.height}px`
        textDiv.innerHTML = ''
        const textContent = await page.getTextContent()
        const textLayer = new pdfjsLib.TextLayer({
          textContentSource: textContent,
          container: textDiv,
          viewport,
        })
        await textLayer.render()
      } catch (e) {
        // 单页失败不阻塞其他页
        console.warn(`渲染第 ${pageNo} 页失败`, e)
      }
    },
    [],
  )

  // 当 totalPages/renderedPages/pendingRender 变化时，渲染前 N 页
  useEffect(() => {
    if (totalPages === 0 || loading || !pdfDocRef.current) return
    let cancelled = false
    const run = async () => {
      const end = Math.min(renderedPages, totalPages)
      for (let i = 1; i <= end; i++) {
        if (cancelled) break
        await renderPage(i)
      }
    }
    run()
    return () => {
      cancelled = true
    }
  }, [renderedPages, totalPages, loading, pendingRender, renderPage])

  // 选中文本 → 弹出添加高亮 Popover
  const handleMouseUp = useCallback(
    (pageNo: number, e: React.MouseEvent<HTMLDivElement>) => {
      const sel = window.getSelection()
      if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
        return
      }
      const text = sel.toString().trim()
      if (!text) return
      const wrapper = e.currentTarget
      const pageWidth = Number(wrapper.dataset.pageWidth || 0)
      const pageHeight = Number(wrapper.dataset.pageHeight || 0)
      if (!pageWidth || !pageHeight) return
      const rects = selectionToNormalizedRects(sel, wrapper, pageWidth, pageHeight)
      if (rects.length === 0) return
      setPending({ page: pageNo, rects, text, note: '' })
    },
    [],
  )

  const confirmHighlight = () => {
    if (!pending) return
    const rec: HighlightRecord = {
      id: uuid(),
      paperId: paper.id,
      page: pending.page,
      rects: pending.rects,
      text: pending.text,
      color: currentColor,
      note: pending.note,
      createdAt: new Date().toISOString(),
    }
    setHighlights((prev) => [rec, ...prev])
    setPending(null)
    window.getSelection()?.removeAllRanges()
    message.success(t('pdf.highlightAdded'))
  }

  const cancelHighlight = () => {
    setPending(null)
    window.getSelection()?.removeAllRanges()
  }

  const removeHighlight = (id: string) => {
    setHighlights((prev) => prev.filter((h) => h.id !== id))
  }

  const updateNote = (id: string, note: string) => {
    setHighlights((prev) => prev.map((h) => (h.id === id ? { ...h, note } : h)))
  }

  const jumpToHighlight = (rec: HighlightRecord) => {
    const wrapper = pageRefs.current.get(rec.page)
    if (wrapper) {
      wrapper.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setActivePage(rec.page)
    }
  }

  const loadMorePages = () => {
    setRenderedPages((n) => Math.min(n + INITIAL_PAGE_BATCH, totalPages))
  }

  // 按页码分组高亮（便于渲染覆盖层）
  const highlightsByPage = useMemo(() => {
    const map = new Map<number, HighlightRecord[]>()
    for (const h of highlights) {
      if (!map.has(h.page)) map.set(h.page, [])
      map.get(h.page)!.push(h)
    }
    return map
  }, [highlights])

  const pendingStyle = useMemo(
    () => ({ background: currentColor } as React.CSSProperties),
    [currentColor],
  )

  return (
    <div className="pf-pdf-viewer" style={{ display: 'flex', gap: 12 }}>
      {/* 左侧：PDF 渲染区 */}
      <div
        ref={containerRef}
        className="pf-pdf-canvas-wrap"
        style={{
          flex: 1,
          minWidth: 0,
          maxHeight: 720,
          overflow: 'auto',
          background: '#f1f5f9',
          padding: 16,
          borderRadius: 8,
        }}
      >
        {/* 工具栏 */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: 12,
            position: 'sticky',
            top: 0,
            background: '#ffffffee',
            padding: '6px 10px',
            borderRadius: 6,
            backdropFilter: 'blur(6px)',
            zIndex: 5,
          }}
        >
          <Space size="small">
            <HighlightOutlined style={{ color: currentColor }} />
            <span style={{ fontSize: 12, color: '#475569' }}>{t('pdf.highlightColor')}</span>
            <ColorPicker
              value={currentColor}
              onChange={(c) => setCurrentColor(c.toHexString())}
              size="small"
              showText
              format="hex"
              presets={[
                {
                  label: t('pdf.presetLabel'),
                  colors: COLOR_PRESETS.map((p) => p.value),
                },
              ]}
            />
          </Space>
          <Space size="small">
            <Tag color="blue">
              {activePage}/{totalPages || '?'}
            </Tag>
            <Tag color="purple">{highlights.length} {t('pdf.highlightCount')}</Tag>
            <Button
              size="small"
              type={sidebarOpen ? 'primary' : 'default'}
              icon={<ReadOutlined />}
              onClick={() => setSidebarOpen((v) => !v)}
            >
              {t('pdf.highlightList')}
            </Button>
          </Space>
        </div>

        {loading && (
          <div style={{ textAlign: 'center', padding: '60px 0' }}>
            <Spin tip={t('pdf.loadingPdf')} />
          </div>
        )}
        {error && (
          <div style={{ color: '#dc2626', padding: 24, textAlign: 'center' }}>
            {error}
          </div>
        )}

        {!loading && !error && (
          <>
            {Array.from({ length: Math.min(renderedPages, totalPages) }).map(
              (_, idx) => {
                const pageNo = idx + 1
                return (
                  <div
                    key={pageNo}
                    data-page={pageNo}
                    ref={(el) => {
                      if (el) pageRefs.current.set(pageNo, el)
                      else pageRefs.current.delete(pageNo)
                    }}
                    onMouseUp={(e) => handleMouseUp(pageNo, e)}
                    style={{
                      position: 'relative',
                      margin: '0 auto 16px',
                      background: '#fff',
                      boxShadow: '0 1px 4px rgba(0,0,0,0.1)',
                    }}
                  >
                    {/* 页码角标 */}
                    <div
                      style={{
                        position: 'absolute',
                        top: 4,
                        right: 8,
                        fontSize: 11,
                        color: '#94a3b8',
                        zIndex: 4,
                      }}
                    >
                      {pageNo}
                    </div>
                    <canvas />
                    {/* 高亮覆盖层（位于 canvas 与 text layer 之间） */}
                    {(highlightsByPage.get(pageNo) || []).map((h) =>
                      Array.from({ length: h.rects.length / 4 }).map((__, ri) => {
                        const base = ri * 4
                        const x = h.rects[base]
                        const y = h.rects[base + 1]
                        const w = h.rects[base + 2]
                        const hh = h.rects[base + 3]
                        return (
                          <Tooltip
                            key={`${h.id}-${ri}`}
                            title={h.note || h.text.slice(0, 80)}
                            placement="top"
                          >
                            <div
                              style={{
                                position: 'absolute',
                                left: `${x * 100}%`,
                                top: `${y * 100}%`,
                                width: `${w * 100}%`,
                                height: `${hh * 100}%`,
                                background: h.color,
                                opacity: 0.4,
                                pointerEvents: 'auto',
                                cursor: 'pointer',
                                zIndex: 2,
                                mixBlendMode: 'multiply',
                              }}
                              onClick={() => jumpToHighlight(h)}
                            />
                          </Tooltip>
                        )
                      }),
                    )}
                    {/* 待确认高亮预览 */}
                    {pending && pending.page === pageNo && (
                      <Popover
                        title={t('pdf.addHighlight')}
                        trigger="click"
                        open
                        onOpenChange={(o) => {
                          if (!o) cancelHighlight()
                        }}
                        content={
                          <div style={{ width: 280 }}>
                            <Input.TextArea
                              rows={3}
                              placeholder={t('pdf.annotationPlaceholder')}
                              value={pending.note}
                              onChange={(e) =>
                                setPending((p) =>
                                  p ? { ...p, note: e.target.value } : p,
                                )
                              }
                            />
                            <div
                              style={{
                                marginTop: 8,
                                display: 'flex',
                                gap: 8,
                                justifyContent: 'flex-end',
                              }}
                            >
                              <Button size="small" onClick={cancelHighlight}>
                                {t('common.cancel')}
                              </Button>
                              <Button
                                size="small"
                                type="primary"
                                onClick={confirmHighlight}
                              >
                                {t('pdf.confirmHighlight')}
                              </Button>
                            </div>
                            <div
                              style={{
                                marginTop: 6,
                                fontSize: 11,
                                color: '#94a3b8',
                              }}
                            >
                              {t('pdf.color')}{currentColor}
                            </div>
                          </div>
                        }
                      >
                        <div
                          style={{
                            ...pendingStyle,
                            position: 'absolute',
                            left: `${(pending.rects[0] || 0) * 100}%`,
                            top: `${(pending.rects[1] || 0) * 100}%`,
                            width: `${(pending.rects[2] || 0) * 100}%`,
                            height: `${(pending.rects[3] || 0) * 100}%`,
                            opacity: 0.5,
                            zIndex: 3,
                          }}
                        />
                      </Popover>
                    )}
                    {/* 文本层（透明，置于最上层以支持选中） */}
                    <div
                      className="pf-pdf-text-layer"
                      style={{
                        position: 'absolute',
                        left: 0,
                        top: 0,
                        overflow: 'hidden',
                        opacity: 1,
                        zIndex: 1,
                        // pdfjs TextLayer 需要文字透明但仍可选中
                        color: 'transparent',
                      }}
                    />
                  </div>
                )
              },
            )}
            {/* 懒加载更多页 */}
            {renderedPages < totalPages && (
              <div style={{ textAlign: 'center', padding: 16 }}>
                <Button onClick={loadMorePages} type="dashed">
                  {t('pdf.loadMore', { count: totalPages - renderedPages })}
                </Button>
              </div>
            )}
          </>
        )}
      </div>

      {/* 右侧：高亮列表侧边栏 */}
      {sidebarOpen && (
        <div
          className="pf-pdf-sidebar"
          style={{
            width: 280,
            flexShrink: 0,
            background: '#fff',
            borderRadius: 8,
            padding: 12,
            maxHeight: 720,
            overflow: 'auto',
            boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
          }}
        >
          <div
            style={{
              fontWeight: 600,
              fontSize: 13,
              marginBottom: 8,
              color: '#1e293b',
            }}
          >
            {t('pdf.highlightsLabel', { count: highlights.length })}
          </div>
          {highlights.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t('pdf.highlightHint')}
              style={{ margin: '24px 0' }}
            />
          ) : (
            <List
              size="small"
              dataSource={highlights}
              renderItem={(h) => (
                <List.Item
                  style={{ padding: '8px 4px', alignItems: 'flex-start' }}
                >
                  <div style={{ width: '100%' }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 6,
                        marginBottom: 4,
                      }}
                    >
                      <span
                        style={{
                          display: 'inline-block',
                          width: 10,
                          height: 10,
                          borderRadius: 2,
                          background: h.color,
                          flexShrink: 0,
                        }}
                      />
                      <Tag style={{ marginRight: 0 }}>P{h.page}</Tag>
                      <span style={{ fontSize: 11, color: '#94a3b8' }}>
                        {new Date(h.createdAt).toLocaleString('zh-CN', {
                          month: '2-digit',
                          day: '2-digit',
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={() => removeHighlight(h.id)}
                        style={{ marginLeft: 'auto', padding: '0 4px' }}
                      />
                    </div>
                    <div
                      onClick={() => jumpToHighlight(h)}
                      style={{
                        fontSize: 12,
                        color: '#334155',
                        cursor: 'pointer',
                        lineHeight: 1.5,
                        marginBottom: 4,
                        display: '-webkit-box',
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                      }}
                    >
                      {h.text}
                    </div>
                    <Input.TextArea
                      rows={1}
                      size="small"
                      placeholder={t('pdf.annotationEditor')}
                      value={h.note}
                      onChange={(e) => updateNote(h.id, e.target.value)}
                      style={{ fontSize: 11 }}
                    />
                  </div>
                </List.Item>
              )}
            />
          )}
        </div>
      )}

      {/* pdfjs TextLayer 样式（透明文字、可选中） */}
      <style>{`
        .pf-pdf-text-layer span {
          color: transparent;
          -webkit-user-select: text;
          user-select: text;
        }
        .pf-pdf-text-layer ::selection {
          background: rgba(96, 165, 250, 0.35);
        }
      `}</style>
    </div>
  )
}
