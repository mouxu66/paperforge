import { useEffect, useRef, useState } from 'react'
import { Typography, Modal, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { askPaperStream } from '@/api/ask'
import { fetchPapers } from '@/api/papers'
import type { AskResponse, AskReference, Paper } from '@/api/types'
import AskHistorySidebar from '@/components/AskHistorySidebar'
import AskInput from '@/components/AskInput'
import AskResult from '@/components/AskResult'
import { useHistoryStore, type HistoryItem } from '@/store/useHistoryStore'

export default function AskPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<AskResponse | null>(null)
  // 论文范围限定
  const [paperOptions, setPaperOptions] = useState<Paper[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  // 历史记录当前选中项
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null)
  // 流式问答状态
  const [streaming, setStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [streamingRefs, setStreamingRefs] = useState<AskReference[]>([])
  const streamingTextRef = useRef('')
  const streamingRefsRef = useRef<AskReference[]>([])

  const historyItems = useHistoryStore((s) => s.items)
  const addHistory = useHistoryStore((s) => s.add)
  const removeHistory = useHistoryStore((s) => s.remove)
  const clearHistory = useHistoryStore((s) => s.clear)

  // 挂载时拉取全部论文，供范围选择下拉使用
  useEffect(() => {
    fetchPapers({ page: 1, pageSize: 1000 })
      .then((res) => setPaperOptions(res.items))
      .catch(() => {
        // 错误已在 client.ts 拦截器统一提示，此处静默
      })
  }, [])

  const handleSubmit = async () => {
    const q = query.trim()
    if (!q || loading || streaming) return
    setLoading(true)
    setResult(null)
    setStreaming(false)
    setStreamingText('')
    setStreamingRefs([])
    streamingTextRef.current = ''
    streamingRefsRef.current = []
    setActiveHistoryId(null)
    try {
      await askPaperStream(
        {
          question: q,
          paperIds: selectedIds.length > 0 ? selectedIds : undefined,
        },
        {
          onRefs: (refs) => {
            setLoading(false)
            setStreaming(true)
            setStreamingRefs(refs)
            streamingRefsRef.current = refs
          },
          onToken: (token) => {
            streamingTextRef.current += token
            setStreamingText(streamingTextRef.current)
          },
          onError: (error) => {
            setStreaming(false)
            setLoading(false)
            message.error(error)
          },
          onDone: () => {
            setStreaming(false)
            const finalResult: AskResponse = {
              answer: streamingTextRef.current,
              references: streamingRefsRef.current,
            }
            setResult(finalResult)
            addHistory({
              query: q,
              answer: finalResult.answer,
              references: finalResult.references,
              timestamp: Date.now(),
              selectedIds,
            })
          },
        },
      )
    } catch (e: any) {
      const detail = e?.message || t('ask.askFailed')
      message.error(detail)
    } finally {
      setLoading(false)
    }
  }

  // 点击历史项：回填 query + 渲染 answer/references + 回填论文范围 + 高亮该项
  const handleHistoryClick = (item: HistoryItem) => {
    setQuery(item.query)
    setResult({ answer: item.answer, references: item.references })
    setSelectedIds(item.selectedIds || [])
    setActiveHistoryId(item.id)
  }

  const handleClearAll = () => {
    Modal.confirm({
      title: t('ask.clearHistoryTitle'),
      content: t('ask.clearHistoryContent'),
      okText: t('ask.clearHistoryOk'),
      okType: 'danger',
      cancelText: t('ask.cancel'),
      onOk: () => {
        clearHistory()
        setActiveHistoryId(null)
      },
    })
  }

  // 删除单条历史：若删除的是当前选中项，则取消高亮
  const handleRemoveItem = (id: string) => {
    removeHistory(id)
    if (activeHistoryId === id) setActiveHistoryId(null)
  }

  return (
    <div style={{ display: 'flex', gap: 20, maxWidth: 1180, margin: '0 auto' }}>
      {/* 历史对话侧边栏 */}
      <div style={{ width: 260, flexShrink: 0 }}>
        <AskHistorySidebar
          items={historyItems}
          activeHistoryId={activeHistoryId}
          onSelect={handleHistoryClick}
          onRemoveItem={handleRemoveItem}
          onClearAll={handleClearAll}
        />
      </div>

      {/* 主内容区 */}
      <div style={{ flex: 1, maxWidth: 900 }}>
        <div style={{ marginBottom: 20 }}>
          <Typography.Title level={3} className="pf-page-title" style={{ marginBottom: 0 }}>
            {t('ask.title')}
          </Typography.Title>
          <div className="pf-subtitle">{t('ask.subtitle')}</div>
        </div>

        {/* 提问输入区 */}
        <AskInput
          query={query}
          onQueryChange={setQuery}
          loading={loading}
          onSubmit={handleSubmit}
          paperOptions={paperOptions}
          selectedIds={selectedIds}
          onSelectedIdsChange={setSelectedIds}
        />

        {/* 结果展示区 */}
        <AskResult
          result={result}
          loading={loading}
          onNavigate={navigate}
          streaming={streaming}
          streamingText={streamingText}
          streamingRefs={streamingRefs}
        />
      </div>
    </div>
  )
}
