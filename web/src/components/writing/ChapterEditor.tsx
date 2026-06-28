import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Drawer, Empty, Input, Modal, Progress, Segmented, Spin, Tag, Tooltip, Typography, message } from 'antd'
import {
  EditOutlined,
  EyeOutlined,
  ColumnHeightOutlined,
  HistoryOutlined,
  RobotOutlined,
  EditFilled,
  ApartmentOutlined,
  LoadingOutlined,
  CheckCircleOutlined,
  ReloadOutlined,
  HighlightOutlined,
  ThunderboltOutlined,
  SearchOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Chapter, RecommendedPaper, SuggestItem, StructureSuggestion } from '@/api/types'
import { fetchSuggest } from '@/api/papers'
import {
  applySuggestion,
  continueWritingStream,
  recommendCitations,
  rewriteText,
  saveContinuation,
  suggestStructure,
} from '@/api/writing'
import VersionDrawer from './VersionDrawer'
import ContinuationHistory from './ContinuationHistory'
import { renderTextWithCitations } from './ReferencePreview'

type ViewMode = 'edit' | 'preview' | 'split'

/** 自动保存状态：idle 空闲 / saving 保存中 / saved 已保存 / error 保存失败 */
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

interface ChapterEditorProps {
  chapter: Chapter | null
  saving: boolean
  generating: boolean
  /** 保存章节（标题 + 内容） */
  onSave: (chapterId: number, title: string, content: string) => Promise<void>
  /** AI 生成章节内容 */
  onGenerate: (chapterId: number) => Promise<void>
  /** C3：大纲变更后刷新树（采纳结构建议移动章节后调用） */
  onOutlineChanged?: () => void
}

/** 提示符状态：@ 触发的论文搜索浮层 */
interface MentionState {
  active: boolean
  start: number // @ 字符在文本中的位置
  query: string // @ 之后的查询串
  results: SuggestItem[]
  loading: boolean
  index: number // 当前高亮项
  top: number // 浮层 top（相对编辑容器）
  left: number // 浮层 left
}

const EMPTY_MENTION: MentionState = {
  active: false,
  start: -1,
  query: '',
  results: [],
  loading: false,
  index: 0,
  top: 0,
  left: 0,
}

/**
 * 计算 textarea 中指定光标位置的像素坐标（mirror-div 方案）。
 * 返回相对 textarea 左上角的偏移。
 */
function getCaretCoordinates(
  textarea: HTMLTextAreaElement,
  caretPos: number,
): { top: number; left: number } {
  const div = document.createElement('div')
  const style = window.getComputedStyle(textarea)
  const props: string[] = []
  // 拷贝影响布局的样式
  for (let i = 0; i < style.length; i++) {
    const p = style[i]
    props.push(p)
  }
  div.style.position = 'absolute'
  div.style.visibility = 'hidden'
  div.style.whiteSpace = 'pre-wrap'
  div.style.wordWrap = 'break-word'
  for (const p of props) {
    div.style.setProperty(p, style.getPropertyValue(p))
  }
  div.textContent = textarea.value.substring(0, caretPos)
  const span = document.createElement('span')
  span.textContent = textarea.value.substring(caretPos) || '.'
  div.appendChild(span)
  document.body.appendChild(div)
  const coords = {
    top: span.offsetTop + parseInt(style.borderTopWidth || '0', 10) - textarea.scrollTop,
    left: span.offsetLeft + parseInt(style.borderLeftWidth || '0', 10),
  }
  document.body.removeChild(div)
  return coords
}

/**
 * 从光标位置向前查找活跃的 @ 引用触发点。
 * 规则：@ 前须为空白或行首，@ 与光标之间须为非空白连续字符。
 */
function detectMention(text: string, caret: number): { start: number; query: string } | null {
  let i = caret - 1
  while (i >= 0 && i >= caret - 64) {
    const ch = text[i]
    if (ch === '@') {
      const prev = i > 0 ? text[i - 1] : ''
      if (i === 0 || /\s/.test(prev)) {
        const query = text.slice(i + 1, caret)
        if (/^\S*$/.test(query)) return { start: i, query }
      }
      return null
    }
    if (/\s/.test(ch)) return null
    i--
  }
  return null
}

/**
 * 章节内容编辑器 —— 右侧栏。
 *
 * 功能：
 * - Markdown 编辑（TextArea）+ 实时预览（react-markdown），编辑/预览/分屏三模式
 * - 输入 @ 触发论文搜索浮层，选中后插入 [@paper_id] 引用标记
 * - 「AI 生成」按钮调用 LLM 生成章节初稿
 * - 自动保存（防抖 2 秒），减少手动操作
 */
export default function ChapterEditor({
  chapter,
  saving,
  generating,
  onSave,
  onGenerate,
  onOutlineChanged,
}: ChapterEditorProps) {
  const { t } = useTranslation()
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [mode, setMode] = useState<ViewMode>('split')
  const [dirty, setDirty] = useState(false)
  const [mention, setMention] = useState<MentionState>(EMPTY_MENTION)
  const [versionDrawerOpen, setVersionDrawerOpen] = useState(false)
  // A1: 自动保存状态
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')
  const savedResetTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // 续写状态
  const [continueModalOpen, setContinueModalOpen] = useState(false)
  const [continueDirection, setContinueDirection] = useState('')
  const [continuing, setContinuing] = useState(false)
  // 结构建议状态
  const [structureLoading, setStructureLoading] = useState(false)
  const [structureResult, setStructureResult] = useState<StructureSuggestion | null>(null)
  // C1: 改写状态
  const [rewriting, setRewriting] = useState(false)
  // C2: 续写历史抽屉
  const [continuationHistoryOpen, setContinuationHistoryOpen] = useState(false)
  // C3: 采纳建议中
  const [applyingSuggestion, setApplyingSuggestion] = useState(false)
  // 引用智能推荐
  const [recommendOpen, setRecommendOpen] = useState(false)
  const [recommendLoading, setRecommendLoading] = useState(false)
  const [recommendPapers, setRecommendPapers] = useState<RecommendedPaper[]>([])

  const containerRef = useRef<HTMLDivElement>(null)
  const latestRef = useRef({ title, content, dirty, chapterId: chapter?.id ?? null })
  latestRef.current = { title, content, dirty, chapterId: chapter?.id ?? null }

  // 外部章节切换 / 外部内容更新（如 AI 生成）时同步本地状态
  const lastSyncRef = useRef<{ id: number | null; updatedAt: string }>({
    id: null,
    updatedAt: '',
  })
  useEffect(() => {
    if (!chapter) {
      setTitle('')
      setContent('')
      setDirty(false)
      setMention(EMPTY_MENTION)
      setSaveStatus('idle')
      if (savedResetTimer.current) {
        clearTimeout(savedResetTimer.current)
        savedResetTimer.current = null
      }
      lastSyncRef.current = { id: null, updatedAt: '' }
      return
    }
    const sync = lastSyncRef.current
    if (chapter.id !== sync.id || chapter.updatedAt !== sync.updatedAt) {
      setTitle(chapter.title)
      setContent(chapter.content)
      setDirty(false)
      setMention(EMPTY_MENTION)
      // 外部内容更新（如 AI 生成）不沿用上次的保存状态
      if (chapter.id !== sync.id) {
        setSaveStatus('idle')
        if (savedResetTimer.current) {
          clearTimeout(savedResetTimer.current)
          savedResetTimer.current = null
        }
      }
      lastSyncRef.current = { id: chapter.id, updatedAt: chapter.updatedAt }
    }
  }, [chapter])

  const doSave = useCallback(async () => {
    const { title: titleVal, content: c, dirty: d, chapterId } = latestRef.current
    if (!chapterId || !d) return
    const trimmed = titleVal.trim()
    if (!trimmed) {
      message.warning(t('chapter.titleRequired'))
      return
    }
    // A1: 进入"保存中"状态，清除上次的"已保存"自动复位定时器
    setSaveStatus('saving')
    if (savedResetTimer.current) {
      clearTimeout(savedResetTimer.current)
      savedResetTimer.current = null
    }
    try {
      await onSave(chapterId, trimmed, c)
      setDirty(false)
      setSaveStatus('saved')
      // 2 秒后从"已保存"复位到 idle，避免长期显示
      savedResetTimer.current = setTimeout(() => {
        setSaveStatus((cur) => (cur === 'saved' ? 'idle' : cur))
        savedResetTimer.current = null
      }, 2000)
    } catch {
      // 保存失败：保持红色直到下一次成功或手动重试
      setSaveStatus('error')
    }
  }, [onSave])

  // 自动保存：防抖 2 秒
  useEffect(() => {
    if (!chapter || !dirty) return
    const timer = setTimeout(() => {
      void doSave()
    }, 2000)
    return () => clearTimeout(timer)
  }, [content, title, dirty, chapter, doSave])

  // A1: 组件卸载时清理"已保存"复位定时器
  useEffect(() => {
    return () => {
      if (savedResetTimer.current) {
        clearTimeout(savedResetTimer.current)
        savedResetTimer.current = null
      }
    }
  }, [])

  const getTextarea = useCallback((): HTMLTextAreaElement | null => {
    return containerRef.current?.querySelector('textarea') ?? null
  }, [])

  // @ 引用搜索：防抖 300ms
  useEffect(() => {
    if (!mention.active) return
    setMention((m) => ({ ...m, loading: true }))
    const q = mention.query
    const timer = setTimeout(async () => {
      try {
        const results = await fetchSuggest(q)
        setMention((m) => ({ ...m, results, loading: false, index: 0 }))
      } catch {
        setMention((m) => ({ ...m, results: [], loading: false }))
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [mention.active, mention.query])

  const closeMention = useCallback(() => {
    setMention((m) => (m.active ? EMPTY_MENTION : m))
  }, [])

  /** 选中某论文后，将 @query 替换为 [@paper_id] */
  const insertCitation = useCallback(
    (paper: SuggestItem) => {
      const ta = getTextarea()
      if (!ta) return
      const caret = ta.selectionEnd
      const before = content.slice(0, mention.start)
      const after = content.slice(caret)
      const marker = `[@${paper.id}] `
      const next = before + marker + after
      setContent(next)
      setDirty(true)
      closeMention()
      // 恢复光标到插入点之后
      requestAnimationFrame(() => {
        const t = getTextarea()
        if (t) {
          const pos = before.length + marker.length
          t.focus()
          t.setSelectionRange(pos, pos)
        }
      })
    },
    [content, mention.start, getTextarea, closeMention],
  )

  const handleContentChange = (value: string) => {
    setContent(value)
    setDirty(true)
    // 检测 @ 触发
    const ta = getTextarea()
    if (!ta) {
      closeMention()
      return
    }
    const caret = ta.selectionEnd
    const detected = detectMention(value, caret)
    if (detected) {
      const coords = getCaretCoordinates(ta, caret)
      const rect = ta.getBoundingClientRect()
      const containerRect = containerRef.current?.getBoundingClientRect()
      const top = coords.top + (rect.top - (containerRect?.top ?? 0)) + 20
      const left = coords.left + (rect.left - (containerRect?.left ?? 0))
      setMention((m) =>
        m.active && m.start === detected.start
          ? { ...m, query: detected.query, top, left }
          : {
              ...EMPTY_MENTION,
              active: true,
              start: detected.start,
              query: detected.query,
              top,
              left,
            },
      )
    } else if (mention.active) {
      closeMention()
    }
  }

  /**
   * A2: 包裹选中文本（用于 Ctrl+B 粗体 / Ctrl+I 斜体）。
   * - 无选中文本时，在光标处插入 `before+after` 并把光标定位到中间
   * - 有选中文本时，包裹选中文本，并把选区扩展到包裹后的整段
   */
  const wrapSelection = useCallback(
    (before: string, after: string) => {
      const ta = getTextarea()
      if (!ta) return
      const start = ta.selectionStart
      const end = ta.selectionEnd
      const selected = content.slice(start, end)
      const next = content.slice(0, start) + before + selected + after + content.slice(end)
      setContent(next)
      setDirty(true)
      // 恢复光标 / 选区
      requestAnimationFrame(() => {
        const t = getTextarea()
        if (!t) return
        t.focus()
        if (selected.length > 0) {
          // 选中包裹后的整段，方便继续输入替换
          t.setSelectionRange(start + before.length, end + before.length)
        } else {
          // 无选中：光标停在包裹符之间，方便直接输入
          const pos = start + before.length
          t.setSelectionRange(pos, pos)
        }
      })
    },
    [content, getTextarea],
  )

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mention.active && mention.results.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setMention((m) => ({
          ...m,
          index: (m.index + 1) % m.results.length,
        }))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setMention((m) => ({
          ...m,
          index: (m.index - 1 + m.results.length) % m.results.length,
        }))
        return
      }
      if (e.key === 'Enter') {
        e.preventDefault()
        const item = mention.results[mention.index]
        if (item) insertCitation(item)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        closeMention()
        return
      }
    }
    // A2: Ctrl/Cmd + S 保存 / Ctrl/Cmd + B 粗体 / Ctrl/Cmd + I 斜体
    if (e.ctrlKey || e.metaKey) {
      const k = e.key.toLowerCase()
      if (k === 's') {
        e.preventDefault()
        void doSave()
        return
      }
      if (k === 'b') {
        e.preventDefault()
        wrapSelection('**', '**')
        return
      }
      if (k === 'i') {
        e.preventDefault()
        wrapSelection('*', '*')
        return
      }
    }
  }

  const handleGenerate = async () => {
    if (!chapter || generating) return
    await onGenerate(chapter.id)
  }

  /** 智能续写：SSE 流式追加文本到光标位置 */
  const handleContinue = async () => {
    if (!chapter || continuing) return
    setContinuing(true)
    const direction = continueDirection.trim()
    const chapterId = chapter.id
    // 在当前内容末尾追加（非光标位置，保持简单）
    let accumulated = ''
    await continueWritingStream(
      chapterId,
      direction,
      (token) => {
        accumulated += token
        setContent((cur) => cur + (cur && !cur.endsWith('\n') ? '\n\n' : '') + token)
      },
      () => {
        setContinuing(false)
        setContinueModalOpen(false)
        setContinueDirection('')
        setDirty(true)
        if (accumulated) {
          message.success(t('chapter.continueDone'))
          // C2: 自动保存续写历史（不阻塞 UI，失败静默）
          saveContinuation(chapterId, accumulated, direction).catch(() => {
            // 保存失败不影响续写结果
          })
        }
      },
      (err) => {
        setContinuing(false)
        message.error(err || t('chapter.continueFailed'))
      },
    )
  }

  /** 结构建议：调用 LLM 分析并展示结果 */
  const handleSuggestStructure = async () => {
    if (!chapter || structureLoading) return
    setStructureLoading(true)
    try {
      const result = await suggestStructure(chapter.id)
      setStructureResult(result)
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setStructureLoading(false)
    }
  }

  /** C1：全文改写 —— 选中文本后调用 LLM 润色，替换选区 */
  const handleRewrite = async () => {
    if (!chapter || rewriting) return
    const ta = getTextarea()
    if (!ta) {
      message.warning(t('chapter.selectTextToRewrite'))
      return
    }
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const selected = content.slice(start, end)
    if (!selected.trim()) {
      message.warning(t('chapter.selectTextFirst'))
      return
    }
    setRewriting(true)
    try {
      const result = await rewriteText(chapter.id, selected)
      const rewritten = result.rewritten
      // 替换选中文本，保持选区在新文本上
      const next = content.slice(0, start) + rewritten + content.slice(end)
      setContent(next)
      setDirty(true)
      requestAnimationFrame(() => {
        const t = getTextarea()
        if (t) {
          t.focus()
          t.setSelectionRange(start, start + rewritten.length)
        }
      })
      message.success(t('chapter.rewriteDone'))
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setRewriting(false)
    }
  }

  /** C3：采纳结构建议 —— 调用后端移动章节到目标位置，刷新大纲 */
  const handleApplySuggestion = async () => {
    if (!chapter || applyingSuggestion) return
    if (!structureResult?.targetChapterId) {
      message.warning(t('chapter.structureNoTarget'))
      return
    }
    setApplyingSuggestion(true)
    try {
      const res = await applySuggestion(chapter.id, structureResult.targetChapterId)
      message.success(res.message)
      setStructureResult(null)
      onOutlineChanged?.()
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setApplyingSuggestion(false)
    }
  }

  /** 引用智能推荐：基于当前章节内容检索相关论文 */
  const handleRecommendCitations = async () => {
    if (!chapter || recommendLoading) return
    // 章节内容为空时提示
    if (!content.trim()) {
      message.warning(t('chapter.recommendCitationsNoContent'))
      return
    }
    setRecommendOpen(true)
    setRecommendLoading(true)
    setRecommendPapers([])
    try {
      const res = await recommendCitations(chapter.id, content)
      setRecommendPapers(res.papers || [])
    } catch {
      message.error(t('chapter.recommendCitationsFailed'))
    } finally {
      setRecommendLoading(false)
    }
  }

  /** 插入推荐引用：在编辑器光标位置插入 [@paper_id] */
  const handleInsertRecommendCitation = (paper: RecommendedPaper) => {
    const ta = getTextarea()
    const marker = `[@${paper.id}] `
    if (!ta) {
      // 无 textarea 时追加到末尾
      setContent((cur) => cur + (cur && !cur.endsWith('\n') ? '\n\n' : '') + marker)
      setDirty(true)
      return
    }
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const next = content.slice(0, start) + marker + content.slice(end)
    setContent(next)
    setDirty(true)
    requestAnimationFrame(() => {
      const t = getTextarea()
      if (t) {
        t.focus()
        const pos = start + marker.length
        t.setSelectionRange(pos, pos)
      }
    })
    message.success(t('chapter.recommendCitationsInserted'))
  }

  /** C2：恢复续写历史 —— 将内容插入到光标位置 */
  const handleRestoreContinuation = (text: string) => {
    const ta = getTextarea()
    if (!ta) {
      // 无 textarea 时追加到末尾
      setContent((cur) => cur + (cur ? '\n\n' : '') + text)
      setDirty(true)
      return
    }
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const next = content.slice(0, start) + text + content.slice(end)
    setContent(next)
    setDirty(true)
    requestAnimationFrame(() => {
      const t = getTextarea()
      if (t) {
        t.focus()
        const pos = start + text.length
        t.setSelectionRange(pos, pos)
      }
    })
  }

  if (!chapter) {
    return (
      <div
        style={{
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t('chapter.selectChapter')}
        />
      </div>
    )
  }

  const editor = (
    <Input.TextArea
      value={content}
      onChange={(e) => handleContentChange(e.target.value)}
      onKeyDown={handleKeyDown}
      onBlur={() => setTimeout(closeMention, 150)}
      placeholder={t('chapter.editorPlaceholder')}
      autoSize={false}
      style={{
        width: '100%',
        height: '100%',
        resize: 'none',
        border: 'none',
        borderRadius: 0,
        padding: 16,
        fontFamily: 'Consolas, Monaco, "Courier New", monospace',
        fontSize: 14,
        lineHeight: 1.8,
      }}
    />
  )

  const preview = (
    <div className="pf-markdown" style={{ padding: 16, overflow: 'auto' }}>
      {content.trim() ? (
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            p: ({ children }) => <p>{renderTextWithCitations(children)}</p>,
            li: ({ children }) => <li>{renderTextWithCitations(children)}</li>,
            h1: ({ children }) => <h1>{renderTextWithCitations(children)}</h1>,
            h2: ({ children }) => <h2>{renderTextWithCitations(children)}</h2>,
            h3: ({ children }) => <h3>{renderTextWithCitations(children)}</h3>,
            h4: ({ children }) => <h4>{renderTextWithCitations(children)}</h4>,
            h5: ({ children }) => <h5>{renderTextWithCitations(children)}</h5>,
            h6: ({ children }) => <h6>{renderTextWithCitations(children)}</h6>,
            blockquote: ({ children }) => (
              <blockquote>{renderTextWithCitations(children)}</blockquote>
            ),
            td: ({ children }) => <td>{renderTextWithCitations(children)}</td>,
            th: ({ children }) => <th>{renderTextWithCitations(children)}</th>,
          }}
        >
          {content}
        </ReactMarkdown>
      ) : (
        <div style={{ color: '#94a3b8', fontSize: 13 }}>{t('chapter.emptyContent')}</div>
      )}
    </div>
  )

  return (
    <div ref={containerRef} style={{ display: 'flex', flexDirection: 'column', height: '100%', position: 'relative' }}>
      {/* 工具栏 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 16px',
          borderBottom: '1px solid #e8ecf1',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <Input
          value={title}
          onChange={(e) => {
            setTitle(e.target.value)
            setDirty(true)
          }}
          variant="borderless"
          placeholder={t('chapter.titlePlaceholder')}
          className="pf-serif"
          style={{
            flex: 1,
            minWidth: 120,
            fontSize: 17,
            fontWeight: 600,
            color: '#1a1a2e',
            padding: 0,
          }}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {/* A1: 自动保存状态指示器 */}
          {saveStatus === 'saving' && (
            <Tag icon={<LoadingOutlined spin />} color="processing" style={{ margin: 0 }}>
              {t('common.saving')}
            </Tag>
          )}
          {saveStatus === 'saved' && (
            <Tag icon={<CheckCircleOutlined />} color="success" style={{ margin: 0 }}>
              {t('common.saved')}
            </Tag>
          )}
          {saveStatus === 'error' && (
            <Tooltip title={t('common.retrySave')}>
              <Tag
                icon={<ReloadOutlined />}
                color="error"
                style={{ margin: 0, cursor: 'pointer' }}
                onClick={() => void doSave()}
              >
                {t('common.saveFailed')}
              </Tag>
            </Tooltip>
          )}
          {saveStatus === 'idle' && dirty && (
            <Tag style={{ margin: 0, color: '#d97706', borderColor: '#fcd34d', background: '#fffbeb' }}>
              {t('common.unsaved')}
            </Tag>
          )}
          <Segmented<ViewMode>
            size="small"
            value={mode}
            onChange={(v) => setMode(v)}
            options={[
              { label: t('chapter.edit'), value: 'edit', icon: <EditOutlined /> },
              { label: t('chapter.preview'), value: 'preview', icon: <EyeOutlined /> },
              { label: t('chapter.split'), value: 'split', icon: <ColumnHeightOutlined /> },
            ]}
          />
          <Button
            size="small"
            icon={<HistoryOutlined />}
            onClick={() => setVersionDrawerOpen(true)}
            disabled={!chapter}
          >
            {t('chapter.versionHistory')}
          </Button>
          <Button
            size="small"
            icon={<RobotOutlined />}
            onClick={handleGenerate}
            loading={generating}
            disabled={generating}
          >
            {t('chapter.aiGenerate')}
          </Button>
          <Button
            size="small"
            icon={<EditFilled />}
            onClick={() => setContinueModalOpen(true)}
            loading={continuing}
            disabled={continuing || !chapter}
            title={t('chapter.continueModalTitle')}
          >
            {t('chapter.continueWriting')}
          </Button>
          <Button
            size="small"
            icon={<ApartmentOutlined />}
            onClick={handleSuggestStructure}
            loading={structureLoading}
            disabled={structureLoading || !chapter}
            title={t('chapter.structureSuggest')}
          >
            {t('chapter.structureSuggest')}
          </Button>
          <Tooltip title={t('chapter.rewriteTooltip')}>
            <Button
              size="small"
              icon={<HighlightOutlined />}
              onClick={handleRewrite}
              loading={rewriting}
              disabled={rewriting || !chapter}
            >
              {rewriting ? t('chapter.rewriteInProgress') : t('chapter.rewrite')}
            </Button>
          </Tooltip>
          <Tooltip title={t('chapter.recommendCitations')}>
            <Button
              size="small"
              icon={<SearchOutlined />}
              onClick={handleRecommendCitations}
              loading={recommendLoading}
              disabled={recommendLoading || !chapter}
            >
              {recommendLoading
                ? t('chapter.recommendCitationsSearching')
                : t('chapter.recommendCitations')}
            </Button>
          </Tooltip>
          <Tooltip title={t('chapter.continueHistoryTooltip')}>
            <Button
              size="small"
              icon={<ThunderboltOutlined />}
              onClick={() => setContinuationHistoryOpen(true)}
              disabled={!chapter}
              title={t('chapter.continueHistory')}
            >
              {t('chapter.continueHistory')}
            </Button>
          </Tooltip>
          <Button
            type="primary"
            size="small"
            onClick={() => void doSave()}
            loading={saving}
            disabled={!dirty}
          >
            {t('chapter.save')}
          </Button>
        </div>
      </div>

      {/* 编辑区 */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {(mode === 'edit' || mode === 'split') && (
          <div
            style={{
              flex: mode === 'split' ? 1 : 2,
              height: '100%',
              borderRight: mode === 'split' ? '1px solid #e8ecf1' : 'none',
              background: '#fff',
              position: 'relative',
            }}
          >
            {editor}
            {/* @ 引用浮层 */}
            {mention.active && (
              <div
                className="pf-mention-popover"
                style={{
                  position: 'absolute',
                  top: mention.top,
                  left: mention.left,
                  zIndex: 1050,
                }}
              >
                {mention.loading ? (
                  <div className="pf-mention-hint">
                    <Spin size="small" /> {t('chapter.mentionSearching')}
                  </div>
                ) : mention.results.length === 0 ? (
                  <div className="pf-mention-hint">
                    {mention.query.length < 2 ? t('chapter.mentionInputHint') : t('chapter.mentionNoResults')}
                  </div>
                ) : (
                  mention.results.map((r, i) => (
                    <div
                      key={r.id}
                      className={`pf-mention-item${i === mention.index ? ' active' : ''}`}
                      onMouseDown={(e) => {
                        e.preventDefault()
                        insertCitation(r)
                      }}
                      onMouseEnter={() => setMention((m) => ({ ...m, index: i }))}
                    >
                      <Typography.Text className="pf-serif" style={{ fontSize: 13, fontWeight: 500 }} ellipsis>
                        {r.title}
                      </Typography.Text>
                      <span className="pf-mention-id">{r.id}</span>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        )}
        {(mode === 'preview' || mode === 'split') && (
          <div
            style={{
              flex: mode === 'split' ? 1 : 2,
              height: '100%',
              background: '#fff',
            }}
          >
            {preview}
          </div>
        )}
      </div>

      {/* 历史版本抽屉 */}
      <VersionDrawer
        open={versionDrawerOpen}
        chapterId={chapter?.id ?? null}
        onClose={() => setVersionDrawerOpen(false)}
        onRestore={(content) => {
          setContent(content)
          setDirty(true)
          setVersionDrawerOpen(false)
          message.success(t('chapter.versionRestored'))
        }}
      />

      {/* 智能续写方向输入 Modal */}
      <Modal
        title={t('chapter.continueModalTitle')}
        open={continueModalOpen}
        onOk={handleContinue}
        onCancel={() => {
          if (!continuing) {
            setContinueModalOpen(false)
            setContinueDirection('')
          }
        }}
        confirmLoading={continuing}
        okText={continuing ? t('chapter.continueOkGenerating') : t('chapter.continueOkText')}
        cancelText={t('common.cancel')}
        closable={!continuing}
        maskClosable={!continuing}
      >
        <Typography.Paragraph style={{ fontSize: 13, color: '#64748b', marginBottom: 12 }}>
          {t('chapter.continueHint')}
        </Typography.Paragraph>
        <Input.TextArea
          autoFocus
          value={continueDirection}
          onChange={(e) => setContinueDirection(e.target.value)}
          placeholder={t('chapter.continuePlaceholder')}
          autoSize={{ minRows: 3, maxRows: 6 }}
          disabled={continuing}
        />
      </Modal>

      {/* 结构建议结果 Drawer */}
      <Drawer
        title={t('chapter.structureSuggest')}
        open={structureResult !== null}
        onClose={() => setStructureResult(null)}
        width={400}
      >
        {structureResult && (
          <div>
            <div style={{ marginBottom: 16 }}>
              <Typography.Text strong style={{ fontSize: 14 }}>
                {t('chapter.suggestionLabel')}
              </Typography.Text>
              <Typography.Paragraph style={{ marginTop: 4 }}>
                {structureResult.suggestion}
              </Typography.Paragraph>
            </div>
            {structureResult.targetChapter && (
              <div style={{ marginBottom: 16 }}>
                <Typography.Text strong style={{ fontSize: 14 }}>
                  {t('chapter.moveToLabel')}
                </Typography.Text>
                <div style={{ marginTop: 4 }}>
                  <Tag color="blue" style={{ fontSize: 13 }}>
                    {structureResult.targetChapter}
                  </Tag>
                </div>
              </div>
            )}
            <div style={{ marginBottom: 16 }}>
              <Typography.Text strong style={{ fontSize: 14 }}>
                {t('chapter.reasonLabel')}
              </Typography.Text>
              <Typography.Paragraph
                style={{ marginTop: 4, fontSize: 13, color: '#475569', lineHeight: 1.7 }}
              >
                {structureResult.reason}
              </Typography.Paragraph>
            </div>
            <div style={{ marginBottom: 16 }}>
              <Typography.Text strong style={{ fontSize: 14 }}>
                {t('chapter.confidenceLabel')}
              </Typography.Text>
              <Progress
                percent={Math.round(structureResult.confidence * 100)}
                size="small"
                strokeColor={structureResult.confidence > 0.7 ? '#52c41a' : '#faad14'}
                style={{ marginTop: 4 }}
              />
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              {structureResult.targetChapterId ? (
                <Button
                  type="primary"
                  block
                  loading={applyingSuggestion}
                  onClick={handleApplySuggestion}
                >
                  {t('chapter.applySuggestion', { target: structureResult.targetChapter })}
                </Button>
              ) : (
                <Tooltip title={t('chapter.noMatchTarget')}>
                  <Button type="primary" block disabled>
                    {t('chapter.applySuggestionNoTarget')}
                  </Button>
                </Tooltip>
              )}
            </div>
            <Button
              block
              style={{ marginTop: 8 }}
              onClick={() => setStructureResult(null)}
            >
              {t('common.close')}
            </Button>
          </div>
        )}
      </Drawer>

      {/* C2：续写历史抽屉 */}
      <ContinuationHistory
        chapterId={chapter?.id ?? null}
        open={continuationHistoryOpen}
        onClose={() => setContinuationHistoryOpen(false)}
        onRestore={handleRestoreContinuation}
      />

      {/* 引用智能推荐 Modal */}
      <Modal
        title={t('chapter.recommendCitationsTitle')}
        open={recommendOpen}
        onCancel={() => {
          if (!recommendLoading) setRecommendOpen(false)
        }}
        footer={
          <Button onClick={() => setRecommendOpen(false)} disabled={recommendLoading}>
            {t('common.close')}
          </Button>
        }
        width={640}
        maskClosable={!recommendLoading}
        closable={!recommendLoading}
      >
        <Typography.Paragraph style={{ fontSize: 13, color: '#64748b', marginBottom: 12 }}>
          {t('chapter.recommendCitationsHint')}
        </Typography.Paragraph>
        {recommendLoading ? (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <Spin size="large" />
          </div>
        ) : recommendPapers.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t('chapter.recommendCitationsEmpty')}
          />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {recommendPapers.map((paper) => (
              <div
                key={paper.id}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  justifyContent: 'space-between',
                  gap: 12,
                  padding: '10px 12px',
                  border: '1px solid #e8ecf1',
                  borderRadius: 6,
                  background: '#fafbfc',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Typography.Text
                    className="pf-serif"
                    style={{ fontSize: 14, fontWeight: 500 }}
                    ellipsis
                  >
                    {paper.title}
                  </Typography.Text>
                  <div style={{ marginTop: 4, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                    <Typography.Text style={{ fontSize: 12, color: '#64748b' }}>
                      {paper.authors.slice(0, 3).join(', ')}
                      {paper.authors.length > 3 ? ' et al.' : ''}
                    </Typography.Text>
                    <Tag style={{ margin: 0, fontSize: 12 }}>{paper.year}</Tag>
                    <Tag
                      color="blue"
                      style={{ margin: 0, fontSize: 12 }}
                    >
                      {t('chapter.recommendCitationsScore')}: {(paper.score * 100).toFixed(0)}%
                    </Tag>
                  </div>
                </div>
                <Button
                  size="small"
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={() => handleInsertRecommendCitation(paper)}
                >
                  {t('chapter.recommendCitationsInsert')}
                </Button>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </div>
  )
}
