import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Card, Collapse, Col, Drawer, Dropdown, Empty, Input, InputNumber, Modal, Progress, Result, Row, Space, Spin, Tag, Typography, message } from 'antd'
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  EditOutlined,
  FileMarkdownOutlined,
  FileWordOutlined,
  FileTextOutlined,
  FullscreenOutlined,
  FullscreenExitOutlined,
  AimOutlined,
  CommentOutlined,
  SaveOutlined,
  MenuOutlined,
  BookOutlined,
  QuestionCircleOutlined,
} from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  createChapter,
  deleteChapter,
  startExportTask,
  getExportProgress,
  fetchChapterTree,
  fetchWritingProject,
  fetchWordCount,
  generateChapter,
  moveChapter,
  updateChapter,
  updateWritingProject,
  saveProjectAsTemplate,
} from '@/api/writing'
import { fetchNotesByProject } from '@/api/notes'
import type { Chapter, ChapterTreeNode, ExportReference, Note, WordCountResult, WritingProject } from '@/api/types'
import { exportMarkdown, exportWord, exportLatex, markdownToHtml } from '@/utils/export'
import OutlineTree from '@/components/writing/OutlineTree'
import ChapterEditor from '@/components/writing/ChapterEditor'
import ReferenceSidebar from '@/components/writing/ReferenceSidebar'
import WritingGuide from '@/components/writing/WritingGuide'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { renderTextWithCitations } from '@/components/writing/ReferencePreview'

// P2-扩展：写作编辑器功能速览面板 —— 折叠式使用指南，帮助新用户快速理解每个按钮用途
const GUIDE_STORAGE_KEY = 'writing-guide-collapsed'
const GUIDE_PANEL_KEY = 'writing-guide'

interface GuideItem {
  /** 按钮/区域名称 */
  name: string
  /** 功能说明 */
  desc: string
}

interface GuideGroup {
  /** 区域标签文字 */
  label: string
  /** 区域标签颜色（antd Tag color） */
  color: string
  /** 该区域的功能项 */
  items: GuideItem[]
}

/** 功能速览面板 —— 折叠式使用指南 */
function WritingGuidePanel() {
  const { t } = useTranslation()
  const GUIDE_GROUPS: GuideGroup[] = [
    {
      label: t('editor.guideToolbar'),
      color: 'blue',
      items: [
        { name: t('editor.guideToolbarTitle'), desc: t('editor.guideToolbarTitleDesc') },
        { name: t('editor.guideSetTarget'), desc: t('editor.guideSetTargetDesc') },
        { name: t('editor.guideFullscreen'), desc: t('editor.guideFullscreenDesc') },
        { name: t('editor.guideSaveAsTemplate'), desc: t('editor.guideSaveAsTemplateDesc') },
        { name: t('editor.guideNotes'), desc: t('editor.guideNotesDesc') },
        { name: t('editor.guideRef'), desc: t('editor.guideRefDesc') },
      ],
    },
    {
      label: t('editor.guideOutlineTree'),
      color: 'green',
      items: [
        { name: t('editor.guideCollapseAll'), desc: t('editor.guideCollapseAllDesc') },
        { name: t('editor.guideAddChapter'), desc: t('editor.guideAddChapterDesc') },
        { name: t('editor.guideDragSort'), desc: t('editor.guideDragSortDesc') },
      ],
    },
    {
      label: t('editor.guideChapterEditor'),
      color: 'orange',
      items: [
        { name: t('editor.guideEditPreviewSplit'), desc: t('editor.guideEditPreviewSplitDesc') },
        { name: t('editor.guideVersionHistory'), desc: t('editor.guideVersionHistoryDesc') },
        { name: t('editor.guideAiGenerate'), desc: t('editor.guideAiGenerateDesc') },
        { name: t('editor.guideContinue'), desc: t('editor.guideContinueDesc') },
        { name: t('editor.guideStructureSuggest'), desc: t('editor.guideStructureSuggestDesc') },
        { name: t('editor.guideRewrite'), desc: t('editor.guideRewriteDesc') },
        { name: t('editor.guideContinueHistory'), desc: t('editor.guideContinueHistoryDesc') },
        { name: t('editor.guideSave'), desc: t('editor.guideSaveDesc') },
      ],
    },
    {
      label: t('editor.guideRefPanel'),
      color: 'purple',
      items: [
        { name: t('editor.guideChapterRefs'), desc: t('editor.guideChapterRefsDesc') },
        { name: t('editor.guideAtMention'), desc: t('editor.guideAtMentionDesc') },
      ],
    },
    {
      label: t('editor.guideEmptyState'),
      color: 'default',
      items: [
        { name: t('editor.guideStartWriting'), desc: t('editor.guideStartWritingDesc') },
      ],
    },
  ]
  // 读取 localStorage 初始展开状态（key 存的是"是否收起"）
  const [activeKey, setActiveKey] = useState<string[]>(() => {
    try {
      const collapsed = localStorage.getItem(GUIDE_STORAGE_KEY) === '1'
      return collapsed ? [] : [GUIDE_PANEL_KEY]
    } catch {
      return [GUIDE_PANEL_KEY]
    }
  })

  const handleChange = (keys: string | string[]) => {
    const arr = Array.isArray(keys) ? keys : [keys]
    setActiveKey(arr)
    try {
      const collapsed = arr.length === 0 ? '1' : '0'
      localStorage.setItem(GUIDE_STORAGE_KEY, collapsed)
    } catch {
      // localStorage 不可用时静默忽略
    }
  }

  return (
    <div style={{ marginBottom: 16 }}>
      <Collapse
        activeKey={activeKey}
        onChange={handleChange}
        items={[
          {
            key: GUIDE_PANEL_KEY,
            label: (
              <Space size={8}>
                <span style={{ fontSize: 14, fontWeight: 600, color: '#1a1a2e' }}>
                  {t('editor.guidePanelTitle')}
                </span>
              </Space>
            ),
            extra: (
              <span style={{ fontSize: 12, color: '#94a3b8' }}>{t('common.clickToExpand')}</span>
            ),
            children: (
              <div style={{ background: '#f8fafc', padding: '12px 16px', borderRadius: 6 }}>
                <Row gutter={[16, 16]}>
                  {GUIDE_GROUPS.map((group) => (
                    <Col key={group.label} xs={24} md={12} xl={8}>
                      <div
                        style={{
                          background: '#fff',
                          border: '1px solid #e8ecf1',
                          borderRadius: 6,
                          padding: '10px 12px',
                          height: '100%',
                        }}
                      >
                        <div style={{ marginBottom: 8 }}>
                          <Tag color={group.color} style={{ margin: 0 }}>
                            {group.label}
                          </Tag>
                        </div>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                          {group.items.map((item) => (
                            <div
                              key={item.name}
                              style={{ display: 'flex', flexDirection: 'column', gap: 2 }}
                            >
                              <span style={{ fontSize: 13, fontWeight: 500, color: '#1e40af' }}>
                                {item.name}
                              </span>
                              <span style={{ fontSize: 12, color: '#64748b', lineHeight: 1.5 }}>
                                {item.desc}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </Col>
                  ))}
                </Row>
              </div>
            ),
          },
        ]}
      />
    </div>
  )
}

export default function WritingEditor() {
  const { projectId } = useParams<{ projectId: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const pid = Number(projectId)

  const [project, setProject] = useState<WritingProject | null>(null)
  const [tree, setTree] = useState<ChapterTreeNode[]>([])
  const [selected, setSelected] = useState<Chapter | null>(null)
  const [loading, setLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [wordCount, setWordCount] = useState<WordCountResult | null>(null)
  const [fullscreen, setFullscreen] = useState(false)
  const [targetModalOpen, setTargetModalOpen] = useState(false)
  const [targetValue, setTargetValue] = useState(0)
  const [templateModalOpen, setTemplateModalOpen] = useState(false)
  const [templateName, setTemplateName] = useState('')
  const [templateDesc, setTemplateDesc] = useState('')
  const [notes, setNotes] = useState<Note[]>([])
  const [notesDrawerOpen, setNotesDrawerOpen] = useState(false)
  // P2-扩展：写作指南 Drawer（手动打开状态；首次自动展开由组件内部管理）
  const [writingGuideOpen, setWritingGuideOpen] = useState(false)
  // 导出进度（null 表示未在导出）
  const [exportProgress, setExportProgress] = useState<number | null>(null)
  const [exportStatus, setExportStatus] = useState<string>('')
  // P2-2: 分块进度（正在处理第 X/Y 章节）
  const [exportChapterInfo, setExportChapterInfo] = useState<{ current: number; total: number } | null>(null)
  // A3: 导出预览（null 表示未在预览）
  const [exportPreview, setExportPreview] = useState<{
    content: string
    refs: ExportReference[]
    filename: string
    format: 'markdown' | 'word' | 'latex'
  } | null>(null)
  // 移动端大纲抽屉
  const [outlineDrawerOpen, setOutlineDrawerOpen] = useState(false)
  // 引用侧边栏：被标记为「不在参考文献中显示」的论文 ID 集合
  const [hiddenRefIds, setHiddenRefIds] = useState<Set<string>>(new Set())
  // 引用侧边栏折叠状态（桌面端）
  const [refSidebarCollapsed, setRefSidebarCollapsed] = useState(false)
  // 屏幕宽度响应式：≥1024 桌面、768-1024 平板、<768 手机
  const [screenWidth, setScreenWidth] = useState(
    typeof window !== 'undefined' ? window.innerWidth : 1280,
  )
  const isDesktop = screenWidth >= 1024
  const isMobile = screenWidth < 768
  const wordCountTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadTree = useCallback(async () => {
    if (!pid || Number.isNaN(pid)) return undefined
    try {
      const data = await fetchChapterTree(pid)
      setTree(data)
      return data
    } catch {
      // 错误已由 http 拦截器提示
      return undefined
    }
  }, [pid])

  const loadWordCount = useCallback(async () => {
    if (!pid || Number.isNaN(pid)) return
    try {
      const wc = await fetchWordCount(pid)
      setWordCount(wc)
    } catch {
      // 错误已由 http 拦截器提示
    }
  }, [pid])

  const loadProject = useCallback(async () => {
    if (!pid || Number.isNaN(pid)) {
      setNotFound(true)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      const p = await fetchWritingProject(pid)
      setProject(p)
      setTargetValue(p.targetWordCount || 0)
      const t = await loadTree()
      void loadWordCount()
      // 加载项目关联笔记
      fetchNotesByProject(pid).then(setNotes).catch(() => {
        // 错误已由 http 拦截器提示
      })
      // 默认选中第一个章节（通常为「引言」）
      if (t && t.length > 0) {
        setSelected(toChapter(t[0]))
      } else {
        setSelected(null)
      }
    } catch {
      setNotFound(true)
    } finally {
      setLoading(false)
    }
  }, [pid, loadTree, loadWordCount])

  useEffect(() => {
    loadProject()
  }, [loadProject])

  /** 取大纲树第一个节点（深度优先，用于默认选中） */
  const toChapter = (node: ChapterTreeNode): Chapter => {
    const { children: _children, ...rest } = node
    void _children
    return rest
  }

  const handleSelect = (chapter: ChapterTreeNode) => {
    setSelected(toChapter(chapter))
  }

  /** 在大纲树中按 id 查找节点 */
  const findNode = (
    nodes: ChapterTreeNode[],
    id: number,
  ): ChapterTreeNode | null => {
    for (const n of nodes) {
      if (n.id === id) return n
      const found = findNode(n.children, id)
      if (found) return found
    }
    return null
  }

  const handleCreate = async (parentId: number | null, title: string) => {
    const created = await createChapter(pid, { title, parentId })
    message.success(t('editor.chapterCreated'))
    const refreshed = await loadTree()
    // 自动选中新创建的章节
    if (refreshed) {
      const node = findNode(refreshed, created.id)
      if (node) setSelected(toChapter(node))
    }
  }

  const handleDelete = async (chapter: Chapter) => {
    await deleteChapter(chapter.id)
    message.success(t('editor.chapterDeleted'))
    // 若删除的是当前选中章节，清空选中
    setSelected((cur) => (cur?.id === chapter.id ? null : cur))
    await loadTree()
  }

  const handleSave = async (chapterId: number, title: string, content: string) => {
    setSaving(true)
    try {
      const updated = await updateChapter(chapterId, { title, content })
      message.success(t('editor.saved'))
      // 同步本地选中章节（用后端返回的 updatedAt 触发编辑器同步）
      setSelected((cur) =>
        cur && cur.id === chapterId
          ? { ...cur, title, content, updatedAt: updated.updatedAt }
          : cur,
      )
      await loadTree()
      // 防抖 500ms 后刷新字数统计
      if (wordCountTimer.current) clearTimeout(wordCountTimer.current)
      wordCountTimer.current = setTimeout(() => void loadWordCount(), 500)
    } finally {
      setSaving(false)
    }
  }

  const handleMove = async (
    chapterId: number,
    parentId: number | null,
    index: number,
  ) => {
    try {
      await moveChapter(chapterId, { parentId, order: index })
      await loadTree()
    } catch {
      // 错误已由 http 拦截器提示
    }
  }

  const handleGenerate = async (chapterId: number) => {
    setGenerating(true)
    try {
      const result = await generateChapter(pid, chapterId)
      message.success(t('editor.chapterGenerated', { model: result.model }))
      // 刷新大纲树与选中章节，让编辑器加载生成后的内容
      await loadTree()
      setSelected((cur) =>
        cur && cur.id === chapterId
          ? { ...cur, content: result.content }
          : cur,
      )
      // 生成后也刷新字数
      void loadWordCount()
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setGenerating(false)
    }
  }

  /** 设定目标字数（保存到后端 + localStorage 缓存） */
  const handleSetTarget = async () => {
    try {
      const updated = await updateWritingProject(pid, {
        targetWordCount: targetValue,
      })
      setProject(updated)
      setTargetModalOpen(false)
      localStorage.setItem(`pf_target_${pid}`, String(targetValue))
      message.success(targetValue > 0 ? t('editor.targetSet', { count: targetValue }) : t('editor.targetCleared'))
    } catch {
      // 错误已由 http 拦截器提示
    }
  }

  /** 将当前项目大纲保存为自定义模板 */
  const handleSaveAsTemplate = async () => {
    const name = templateName.trim()
    if (!name) {
      message.warning(t('editor.templateNameRequired'))
      return
    }
    try {
      await saveProjectAsTemplate(pid, {
        name,
        description: templateDesc.trim(),
        chapters: [],
      })
      message.success(t('editor.templateSaved', { name }))
      setTemplateModalOpen(false)
      setTemplateName('')
      setTemplateDesc('')
    } catch {
      // 错误已由 http 拦截器提示
    }
  }

  /** 章节字数映射（id → 字数），供大纲树显示 */
  const wordCountMap = useMemo(() => {
    const map = new Map<number, number>()
    wordCount?.chapters.forEach((c) => map.set(c.id, c.wordCount))
    return map
  }, [wordCount])

  const totalWords = wordCount?.total ?? 0
  const targetWords = project?.targetWordCount ?? 0
  const targetPercent =
    targetWords > 0 ? Math.min(100, Math.round((totalWords / targetWords) * 100)) : 0
  const targetReached = targetWords > 0 && totalWords >= targetWords

  // 全屏模式：Esc 退出
  useEffect(() => {
    if (!fullscreen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreen(false)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [fullscreen])

  // 全屏模式：禁止页面滚动
  useEffect(() => {
    if (fullscreen) {
      document.body.style.overflow = 'hidden'
      return () => {
        document.body.style.overflow = ''
      }
    }
  }, [fullscreen])

  /** 切换某篇引用的「显示/隐藏」状态 */
  const handleToggleRefHidden = useCallback((paperId: string) => {
    setHiddenRefIds((prev) => {
      const next = new Set(prev)
      if (next.has(paperId)) next.delete(paperId)
      else next.add(paperId)
      return next
    })
  }, [])

  /** A3: 导出准备 —— 运行异步导出任务，完成后弹出预览 Modal（不立即下载） */
  const prepareExport = async (format: 'markdown' | 'word' | 'latex') => {
    if (!project) return
    setExportProgress(0)
    setExportStatus('pending')
    setExportChapterInfo(null)
    try {
      const { taskId } = await startExportTask(project.id)
      // 轮询进度，最多等 60 秒
      let done = false
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 400))
        const p = await getExportProgress(taskId)
        setExportProgress(p.progress)
        setExportStatus(p.status)
        // P2-2: 更新分块进度
        if (p.totalChapters > 0) {
          setExportChapterInfo({ current: p.currentChapter, total: p.totalChapters })
        }
        if (p.status === 'done') {
          const filename = p.filename.replace(/\.\w+$/, '')
          // 过滤掉被标记为隐藏的引用
          const refs = (p.references as ExportReference[]).filter(
            (r) => !hiddenRefIds.has(r.id),
          )
          // A3: 不直接下载，先弹出预览
          setExportPreview({ content: p.content, refs, filename, format })
          done = true
          break
        }
        if (p.status === 'error') {
          message.error(p.error || t('editor.exportTimeout'))
          done = true
          break
        }
      }
      if (!done) {
        message.warning(t('editor.exportTimeout'))
      }
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setExportProgress(null)
      setExportStatus('')
      setExportChapterInfo(null)
    }
  }

  /** A3: 确认导出 —— 根据预览结果执行实际下载 */
  const confirmExport = () => {
    if (!exportPreview) return
    const { content, refs, filename, format } = exportPreview
    try {
      if (format === 'markdown') {
        exportMarkdown(content, refs, filename)
        message.success(t('editor.exportedMarkdown'))
      } else if (format === 'word') {
        exportWord(markdownToHtml(content), refs, filename)
        message.success(t('editor.exportedWord'))
      } else {
        exportLatex(content, refs, filename)
        message.success(t('editor.exportedLatex'))
      }
    } finally {
      setExportPreview(null)
    }
  }

  // 屏幕宽度变化监听（用于响应式布局）
  useEffect(() => {
    const onResize = () => setScreenWidth(window.innerWidth)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    )
  }

  if (notFound || !project) {
    return (
      <Result
        status="404"
        title={t('editor.notFoundTitle')}
        subTitle={t('editor.notFoundSubtitle')}
        extra={
          <Button type="primary" onClick={() => navigate('/write')}>
            {t('editor.backToWrite')}
          </Button>
        }
      />
    )
  }

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: isMobile ? '0 8px' : 0 }}>
      {/* 顶部信息栏 */}
      <div style={{ marginBottom: 16 }}>
        <Space size={isMobile ? 6 : 12} align="center" wrap>
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/write')}
          />
          <EditOutlined style={{ color: '#1e40af' }} />
          <Typography.Title
            level={4}
            className="pf-serif"
            style={{ margin: 0, fontSize: isMobile ? 16 : undefined }}
          >
            {project.title}
          </Typography.Title>
          <Tag color="blue">{project.chapterCount} {t('write.chapterUnit')}</Tag>
          {/* 移动端/平板：显示大纲按钮 */}
          {!isDesktop && (
            <Button
              size="small"
              type="text"
              icon={<MenuOutlined />}
              onClick={() => setOutlineDrawerOpen(true)}
              title={t('editor.outline')}
            >
              {t('editor.outline')}
            </Button>
          )}
          {/* 移动端：折叠次要信息 */}
          {!isMobile && (
            <span style={{ fontSize: 13, color: '#64748b' }}>
              {t('editor.totalWords')}：<strong style={{ color: '#1a1a2e' }}>{totalWords.toLocaleString()}</strong> {t('write.wordUnit')}
            </span>
          )}
          <Button
            size="small"
            type="text"
            icon={<AimOutlined />}
            onClick={() => {
              setTargetValue(project.targetWordCount || 0)
              setTargetModalOpen(true)
            }}
            title={t('editor.setTarget')}
          >
            {!isMobile && (targetWords > 0 ? t('editor.targetWords', { count: targetWords.toLocaleString() }) : t('editor.setTargetLabel'))}
          </Button>
          <Button
            size="small"
            type="text"
            icon={<FullscreenOutlined />}
            onClick={() => setFullscreen(true)}
            title={t('editor.fullscreen')}
            disabled={!selected}
          >
            {!isMobile && t('editor.fullscreen')}
          </Button>
          <Button
            size="small"
            type="text"
            icon={<SaveOutlined />}
            onClick={() => {
              setTemplateName(project.title)
              setTemplateDesc('')
              setTemplateModalOpen(true)
            }}
            title={t('editor.saveAsTemplate')}
          >
            {!isMobile && t('editor.saveAsTemplate')}
          </Button>
          <Button
            size="small"
            type="text"
            icon={<QuestionCircleOutlined />}
            onClick={() => setWritingGuideOpen(true)}
            title={t('editor.guideWritingGuide')}
          >
            {!isMobile && t('editor.guideTitle')}
          </Button>
          <Button
            size="small"
            type="text"
            icon={<CommentOutlined />}
            onClick={() => setNotesDrawerOpen(true)}
            title={t('editor.notes')}
          >
            {!isMobile ? `${t('editor.notes')}${notes.length > 0 ? ` (${notes.length})` : ''}` : (notes.length > 0 ? ` ${notes.length}` : '')}
          </Button>
          <Button
            size="small"
            type="text"
            icon={<BookOutlined />}
            onClick={() => setRefSidebarCollapsed((v) => !v)}
            title={refSidebarCollapsed ? t('editor.showRefSidebar') : t('editor.hideRefSidebar')}
          >
            {!isMobile && t('editor.refSidebar')}
          </Button>
          {!isMobile && project.targetJournal && (
            <span style={{ fontSize: 12, color: '#94a3b8' }}>
              {t('write.targetJournal')}：{project.targetJournal}
            </span>
          )}
        </Space>
        {/* 写作目标进度条 */}
        {targetWords > 0 && (
          <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Progress
              percent={targetPercent}
              status={targetReached ? 'success' : 'active'}
              strokeColor={targetReached ? '#16a34a' : '#1e40af'}
              style={{ flex: 1, maxWidth: 400, margin: 0 }}
              size="small"
            />
            <span style={{ fontSize: 12, color: targetReached ? '#16a34a' : '#94a3b8' }}>
              {targetReached ? '🎉 ' + t('editor.targetReached') : `${totalWords.toLocaleString()} / ${targetWords.toLocaleString()}`}
            </span>
          </div>
        )}
        {project.keywords.length > 0 && (
          <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {project.keywords.map((k) => (
              <Tag key={k}>{k}</Tag>
            ))}
          </div>
        )}
      </div>

      {/* P2-扩展：功能速览面板 —— 折叠式使用指南，默认展开，状态持久化到 localStorage */}
      <WritingGuidePanel />

      {/* 主编辑区：桌面端左右分栏，移动端单栏（大纲走 Drawer） */}
      <div
        style={{
          display: 'flex',
          gap: 16,
          height: isMobile ? 'calc(100vh - 200px)' : 'calc(100vh - 240px)',
          minHeight: isMobile ? 360 : 480,
        }}
      >
        {/* 桌面端：大纲树内嵌左侧 */}
        {isDesktop && (
          <Card
            className="pf-glass-card"
            variant="borderless"
            style={{ width: 300, flexShrink: 0, padding: 0, overflow: 'hidden' }}
            styles={{ body: { padding: 0, height: '100%', display: 'flex', flexDirection: 'column' } }}
          >
            <OutlineTree
              tree={tree}
              selectedId={selected?.id ?? null}
              loading={loading}
              onSelect={handleSelect}
              onCreate={handleCreate}
              onDelete={handleDelete}
              onMove={handleMove}
              wordCountMap={wordCountMap}
            />
          </Card>
        )}

        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ flex: 1, overflow: 'hidden' }}
          styles={{ body: { padding: 0, height: '100%', display: 'flex', flexDirection: 'column' } }}
          title={
            <Space>
              <DownloadOutlined style={{ color: '#1e40af' }} />
              <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
                {t('editor.chapterEditTitle')}
              </span>
            </Space>
          }
          extra={
            <Dropdown.Button
              icon={<DownloadOutlined />}
              disabled={project.chapterCount === 0 || exportProgress !== null}
              menu={{
                items: [
                  {
                    key: 'markdown',
                    label: 'Markdown (.md)',
                    icon: <FileMarkdownOutlined />,
                  },
                  {
                    key: 'word',
                    label: 'Word (.doc)',
                    icon: <FileWordOutlined />,
                  },
                  {
                    key: 'latex',
                    label: 'LaTeX (.tex)',
                    icon: <FileTextOutlined />,
                  },
                ],
                onClick: ({ key }) => {
                  if (key === 'markdown') void prepareExport('markdown')
                  else if (key === 'word') void prepareExport('word')
                  else if (key === 'latex') void prepareExport('latex')
                },
              }}
            >
              {t('common.export')}
            </Dropdown.Button>
          }
        >
          {/* 导出进度条 */}
          {exportProgress !== null && (
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #e8ecf1' }}>
              <Progress
                percent={exportProgress}
                status={exportStatus === 'error' ? 'exception' : exportStatus === 'done' ? 'success' : 'active'}
                strokeColor="#1e40af"
                size="small"
              />
              <div style={{ marginTop: 4, fontSize: 12, color: '#64748b' }}>
                {exportStatus === 'error'
                  ? t('editor.exportTimeout')
                  : exportStatus === 'done'
                    ? t('editor.exportDone')
                    : exportChapterInfo && exportChapterInfo.total > 0
                      ? `${t('editor.exportProcessing', { current: exportChapterInfo.current, total: exportChapterInfo.total })} (${exportProgress}%)`
                      : `${t('editor.exporting')} ${exportProgress}%`}
              </div>
            </div>
          )}
          <ChapterEditor
            chapter={selected}
            saving={saving}
            generating={generating}
            onSave={handleSave}
            onGenerate={handleGenerate}
            onOutlineChanged={() => void loadTree()}
          />
        </Card>

        {/* 桌面端：引用列表侧边栏（右侧 280px，可折叠） */}
        {isDesktop && !fullscreen && !refSidebarCollapsed && (
          <Card
            className="pf-glass-card"
            variant="borderless"
            style={{ width: 280, flexShrink: 0, padding: 0, overflow: 'hidden' }}
            styles={{ body: { padding: 0, height: '100%', display: 'flex', flexDirection: 'column' } }}
          >
            <ReferenceSidebar
              content={selected?.content ?? ''}
              hiddenIds={hiddenRefIds}
              onToggleHidden={handleToggleRefHidden}
            />
          </Card>
        )}
      </div>

      {/* 移动端/平板：大纲树 Drawer */}
      {!isDesktop && (
        <Drawer
          title={<span className="pf-serif">{t('editor.outlineTitle')}</span>}
          placement="left"
          open={outlineDrawerOpen}
          onClose={() => setOutlineDrawerOpen(false)}
          width={isMobile ? 280 : 320}
          styles={{ body: { padding: 0 } }}
        >
          <div style={{ height: '100%' }}>
            <OutlineTree
              tree={tree}
              selectedId={selected?.id ?? null}
              loading={loading}
              onSelect={(node) => {
                handleSelect(node)
                setOutlineDrawerOpen(false)
              }}
              onCreate={handleCreate}
              onDelete={handleDelete}
              onMove={handleMove}
              wordCountMap={wordCountMap}
            />
          </div>
        </Drawer>
      )}

      {/* 全屏写作模式 */}
      {fullscreen && (
        <div className="pf-fullscreen-overlay">
          <div className="pf-fullscreen-toolbar">
            <span style={{ fontSize: 13, color: '#94a3b8' }}>
              {t('editor.fullscreenHint', { title: selected?.title ?? '', count: totalWords.toLocaleString() })}
            </span>
            <Button
              size="small"
              type="text"
              icon={<FullscreenExitOutlined />}
              onClick={() => setFullscreen(false)}
            >
              {t('editor.exitFullscreen')}
            </Button>
          </div>
          <div className="pf-fullscreen-editor">
            <ChapterEditor
              chapter={selected}
              saving={saving}
              generating={generating}
              onSave={handleSave}
              onGenerate={handleGenerate}
            />
          </div>
        </div>
      )}

      {/* 设定目标弹窗 */}
      <Modal
        title={t('editor.targetModalTitle')}
        open={targetModalOpen}
        onOk={handleSetTarget}
        onCancel={() => setTargetModalOpen(false)}
        okText={t('common.save')}
        cancelText={t('common.cancel')}
      >
        <div style={{ marginTop: 16 }}>
          <Typography.Text style={{ fontSize: 13, color: '#64748b' }}>
            {t('editor.targetModalHint')}
          </Typography.Text>
          <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
            <InputNumber
              min={0}
              max={1000000}
              step={100}
              value={targetValue}
              onChange={(v) => setTargetValue(v ?? 0)}
              style={{ width: 200 }}
              addonAfter={t('write.wordUnit')}
            />
            <span style={{ fontSize: 12, color: '#94a3b8' }}>
              {t('editor.targetModalCurrent', { count: totalWords.toLocaleString() })}
            </span>
          </div>
        </div>
      </Modal>

      {/* 保存为模板弹窗 */}
      <Modal
        title={t('editor.templateModalTitle')}
        open={templateModalOpen}
        onOk={handleSaveAsTemplate}
        onCancel={() => setTemplateModalOpen(false)}
        okText={t('common.save')}
        cancelText={t('common.cancel')}
      >
        <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <Typography.Text style={{ fontSize: 13, color: '#64748b' }}>
              {t('editor.templateModalHint')}
            </Typography.Text>
          </div>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('editor.templateNameLabel')}
            </Typography.Text>
            <Input
              value={templateName}
              onChange={(e) => setTemplateName(e.target.value)}
              placeholder={t('editor.templateNamePlaceholder')}
              maxLength={50}
              showCount
              style={{ marginTop: 4 }}
            />
          </div>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('editor.templateDescLabel')}
            </Typography.Text>
            <Input.TextArea
              value={templateDesc}
              onChange={(e) => setTemplateDesc(e.target.value)}
              placeholder={t('editor.templateDescPlaceholder')}
              rows={3}
              maxLength={200}
              showCount
              style={{ marginTop: 4 }}
            />
          </div>
        </div>
      </Modal>

      {/* 关联笔记抽屉 */}
      <Drawer
        title={
          <Space>
            <CommentOutlined style={{ color: '#d97706' }} />
            <span className="pf-serif">{t('editor.notesTitle')}</span>
            {notes.length > 0 && <Tag color="orange">{notes.length}</Tag>}
          </Space>
        }
        placement="right"
        open={notesDrawerOpen}
        onClose={() => setNotesDrawerOpen(false)}
        width={420}
      >
        {notes.length === 0 ? (
          <Empty
            description={t('editor.notesEmpty')}
            style={{ marginTop: 60 }}
          >
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('editor.notesEmptyHint')}
            </Typography.Text>
          </Empty>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {notes.map((n) => (
              <Card
                key={n.id}
                size="small"
                variant="borderless"
                className="pf-glass-card"
                style={{ background: 'rgba(254, 243, 199, 0.4)' }}
              >
                <Typography.Paragraph
                  ellipsis={{ rows: 4 }}
                  style={{ margin: 0, fontSize: 13, color: '#1a1a2e', whiteSpace: 'pre-wrap' }}
                >
                  {n.content || t('editor.emptyNote')}
                </Typography.Paragraph>
                <div style={{ marginTop: 8, fontSize: 11, color: '#94a3b8' }}>
                  {t('editor.noteUpdatedAt', { date: n.updatedAt })}
                </div>
              </Card>
            ))}
          </div>
        )}
      </Drawer>

      {/* A3: 导出前预览 Modal */}
      <Modal
        title={
          <Space>
            <FileMarkdownOutlined />
            <span>{t('editor.exportPreview')} {exportPreview?.format.toUpperCase()}</span>
          </Space>
        }
        open={exportPreview !== null}
        onCancel={() => setExportPreview(null)}
        onOk={confirmExport}
        okText={t('editor.exportConfirm')}
        cancelText={t('common.cancel')}
        width="80%"
        style={{ top: 20 }}
        styles={{ body: { maxHeight: '70vh', overflow: 'auto', padding: '0 24px 16px' } }}
      >
        {exportPreview && (
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {t('editor.exportPreviewHint')}
            </Typography.Text>
            <div
              className="pf-markdown"
              style={{
                marginTop: 12,
                padding: 16,
                background: '#fafafa',
                border: '1px solid #e8ecf1',
                borderRadius: 6,
              }}
            >
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
                }}
              >
                {exportPreview.content}
              </ReactMarkdown>
            </div>
            {exportPreview.refs.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <Typography.Text strong style={{ fontSize: 14 }}>
                  {t('editor.exportRefTitle', { count: exportPreview.refs.length })}
                </Typography.Text>
                <ol style={{ margin: '8px 0 0 20px', padding: 0, fontSize: 13, color: '#475569', lineHeight: 1.8 }}>
                  {exportPreview.refs.map((r) => (
                    <li key={r.id} style={{ marginBottom: 4 }}>
                      <span style={{ color: '#1e40af', fontFamily: 'monospace' }}>[{r.id}]</span>{' '}
                      {r.authors?.join(', ') || 'Unknown'}
                      {' '}
                      ({r.year || 'n.d.'}).{' '}
                      <span className="pf-serif">{r.title}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* P2-扩展：写作指南 Drawer —— 首次自动展开，用户关闭后不再自动弹出 */}
      <WritingGuide open={writingGuideOpen} onClose={() => setWritingGuideOpen(false)} />
    </div>
  )
}
