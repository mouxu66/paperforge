import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Upload, message, Progress, Tag, Tooltip } from 'antd'
import type { UploadFile } from 'antd'
import {
  InboxOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons'
import http from '@/api/client'
import { usePaperStore } from '@/store/usePaperStore'

const { Dragger } = Upload

/** 单文件上传状态 */
type FileStatus = 'pending' | 'uploading' | 'success' | 'failed'

interface UploadItem {
  /** 前端生成的唯一 id（uid） */
  uid: string
  name: string
  status: FileStatus
  /** 上传百分比 0-100 */
  percent: number
  /** 后端返回的解析标题（成功时） */
  title?: string
  /** 失败原因 */
  error?: string
}

const STATUS_ICON: Record<FileStatus, JSX.Element> = {
  pending: <ClockCircleOutlined style={{ color: '#94a3b8' }} />,
  uploading: <LoadingOutlined style={{ color: '#1e40af' }} />,
  success: <CheckCircleOutlined style={{ color: '#16a34a' }} />,
  failed: <CloseCircleOutlined style={{ color: '#dc2626' }} />,
}

/** 生成简易 uid */
const genUid = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`

/**
 * PDF 拖拽上传组件（多文件逐个上传 + 单文件进度）。
 *
 * - 支持 .pdf 多选拖拽上传。
 * - 逐个文件调用 /upload-paper，实时显示每个文件的上传百分比与解析状态。
 * - 全部完成后刷新论文列表。
 * - 成功/失败分别 message 提示。
 */
export default function UploadPaper() {
  const { t } = useTranslation()
  const [items, setItems] = useState<UploadItem[]>([])
  const [uploading, setUploading] = useState(false)
  const loadPapers = usePaperStore((s) => s.loadPapers)

  const STATUS_TEXT: Record<FileStatus, string> = {
    pending: t('upload.statusWaiting'),
    uploading: t('upload.statusUploading'),
    success: t('upload.statusImported'),
    failed: t('upload.statusFailed'),
  }

  /** 更新指定 uid 的项 */
  const updateItem = (uid: string, patch: Partial<UploadItem>) => {
    setItems((prev) => prev.map((it) => (it.uid === uid ? { ...it, ...patch } : it)))
  }

  /** 逐个文件上传 */
  const uploadOne = async (file: File, uid: string): Promise<boolean> => {
    const formData = new FormData()
    formData.append('file', file)
    updateItem(uid, { status: 'uploading', percent: 10 })
    try {
      const { data } = await http.post('/upload-paper', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000,
        onUploadProgress: (e: any) => {
          if (e.total) {
            const percent = Math.round((e.loaded / e.total) * 100)
            updateItem(uid, { percent: Math.min(percent, 90) })
          }
        },
      })
      if (data?.success) {
        updateItem(uid, {
          status: 'success',
          percent: 100,
          title: data.title || file.name,
        })
        return true
      }
      // 后端返回 success=false（解析失败）
      updateItem(uid, {
        status: 'failed',
        percent: 100,
        error: data?.error || t('upload.parseFailed'),
      })
      return false
    } catch (e: any) {
      const detail = e?.response?.data?.detail || e?.message || t('upload.uploadFailed')
      updateItem(uid, { status: 'failed', percent: 100, error: detail })
      return false
    }
  }

  const handleUpload = async (files: File[]) => {
    if (!files.length) return
    // 初始化条目列表
    const newItems: UploadItem[] = files.map((f) => ({
      uid: genUid(),
      name: f.name,
      status: 'pending',
      percent: 0,
    }))
    setItems((prev) => [...prev, ...newItems])
    setUploading(true)

    let successCount = 0
    let failCount = 0
    // 串行上传，避免后端并发解析占用过多资源
    for (let i = 0; i < files.length; i++) {
      const ok = await uploadOne(files[i], newItems[i].uid)
      if (ok) successCount++
      else failCount++
    }

    if (successCount > 0) {
      message.success(t('upload.importSuccess', { count: successCount }))
      await loadPapers()
    }
    if (failCount > 0) {
      message.error(t('upload.importFail', { count: failCount }))
    }
    setUploading(false)
  }

  /** 清空列表（仅在非上传中允许） */
  const clearList = () => {
    if (!uploading) setItems([])
  }

  const draggerProps = {
    name: 'files',
    multiple: true,
    accept: '.pdf',
    fileList: [] as UploadFile[],
    beforeUpload: (_file: File, fileList: File[]) => {
      // 拦截 antd 默认上传，改为手动批量提交
      handleUpload(fileList)
      return false // 阻止 antd 自动上传
    },
    onDrop(e: React.DragEvent) {
      const files = Array.from(e.dataTransfer.files).filter((f) =>
        f.name.toLowerCase().endsWith('.pdf'),
      )
      if (files.length) handleUpload(files)
    },
  }

  // 总体进度：已完成数 / 总数
  const totalCount = items.length
  const doneCount = items.filter((it) => it.status === 'success' || it.status === 'failed').length
  const overallPercent = totalCount === 0 ? 0 : Math.round((doneCount / totalCount) * 100)
  const hasFailed = items.some((it) => it.status === 'failed')

  return (
    <div style={{ marginBottom: 16 }}>
      <Dragger {...draggerProps} style={{ padding: '12px 8px' }}>
        <p className="ant-upload-drag-icon">
          <InboxOutlined style={{ color: '#1e40af', fontSize: 36 }} />
        </p>
        <p className="ant-upload-text" style={{ fontSize: 14, color: '#1a1a2e' }}>
          {t('upload.dragHint')}
        </p>
        <p className="ant-upload-hint" style={{ color: '#94a3b8', fontSize: 12 }}>
          {t('upload.dragDesc')}
        </p>
      </Dragger>

      {totalCount > 0 && (
        <>
          <div
            style={{
              marginTop: 10,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <span style={{ fontSize: 13, color: '#64748b' }}>
              {t('upload.totalProgress', { done: doneCount, total: totalCount })}
              {uploading && t('upload.uploading')}
            </span>
            {!uploading && (
              <a
                onClick={clearList}
                style={{ fontSize: 12, color: '#64748b' }}
                className="pf-link"
              >
                {t('upload.clearList')}
              </a>
            )}
          </div>
          <Progress
            percent={overallPercent}
            status={uploading ? 'active' : hasFailed ? 'exception' : 'success'}
            style={{ marginTop: 4 }}
          />

          <div style={{ marginTop: 8 }}>
            {items.map((it) => (
              <div
                key={it.uid}
                className={`pf-upload-item pf-upload-${it.status}`}
              >
                {STATUS_ICON[it.status]}
                <Tooltip title={it.name} mouseEnterDelay={0.5}>
                  <span className="pf-upload-name">
                    {it.status === 'success' && it.title ? it.title : it.name}
                  </span>
                </Tooltip>
                {it.status === 'uploading' && (
                  <span className="pf-upload-status">{it.percent}%</span>
                )}
                {it.status === 'failed' && it.error && (
                  <Tooltip title={it.error}>
                    <Tag color="red" style={{ marginInlineStart: 0 }}>
                      {STATUS_TEXT[it.status]}
                    </Tag>
                  </Tooltip>
                )}
                {it.status !== 'uploading' && it.status !== 'failed' && (
                  <span className="pf-upload-status">{STATUS_TEXT[it.status]}</span>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
