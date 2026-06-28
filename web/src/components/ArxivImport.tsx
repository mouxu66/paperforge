import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button, Modal, Input, InputNumber, Table, Tag, message, Space } from 'antd'
import { CloudDownloadOutlined, SearchOutlined } from '@ant-design/icons'
import { searchArxiv, importArxivPapers } from '@/api/arxiv'
import type { ArxivPaperPreview } from '@/api/types'
import { usePaperStore } from '@/store/usePaperStore'

/**
 * arXiv 论文导入组件。
 *
 * - 点击按钮打开 Modal，输入关键词检索 arXiv。
 * - 勾选需要的论文后批量入库。
 * - 入库成功后自动刷新论文列表。
 */
export default function ArxivImport() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [maxResults, setMaxResults] = useState(10)
  const [searching, setSearching] = useState(false)
  const [importing, setImporting] = useState(false)
  const [items, setItems] = useState<ArxivPaperPreview[]>([])
  const [selected, setSelected] = useState<ArxivPaperPreview[]>([])
  const loadPapers = usePaperStore((s) => s.loadPapers)

  const handleSearch = async () => {
    const kw = keyword.trim()
    if (!kw) {
      message.warning(t('arxiv.keywordRequired'))
      return
    }
    setSearching(true)
    setSelected([])
    try {
      const list = await searchArxiv(kw, maxResults)
      setItems(list)
      if (list.length === 0) {
        message.info(t('arxiv.noResults'))
      }
    } catch {
      // 拦截器已提示
    } finally {
      setSearching(false)
    }
  }

  const handleImport = async () => {
    if (selected.length === 0) {
      message.warning(t('arxiv.selectAtLeastOne'))
      return
    }
    setImporting(true)
    try {
      const resp = await importArxivPapers(selected)
      if (resp.successCount > 0) {
        message.success(t('arxiv.importSuccess', { count: resp.successCount }))
        await loadPapers()
      }
      if (resp.failCount > 0) {
        message.warning(t('arxiv.importPartialFail', { count: resp.failCount }))
      }
      // 关闭弹窗并重置
      setOpen(false)
      setItems([])
      setSelected([])
      setKeyword('')
    } catch {
      // 拦截器已提示
    } finally {
      setImporting(false)
    }
  }

  const columns = [
    {
      title: t('arxiv.columnTitle'),
      dataIndex: 'title',
      key: 'title',
      ellipsis: true,
      render: (text: string, record: ArxivPaperPreview) => (
        <a href={record.pdfUrl} target="_blank" rel="noreferrer">
          {text}
        </a>
      ),
    },
    {
      title: t('arxiv.columnAuthors'),
      dataIndex: 'authors',
      key: 'authors',
      width: 180,
      ellipsis: true,
      render: (authors: string[]) => authors.join(', ') || '—',
    },
    {
      title: t('arxiv.columnYear'),
      dataIndex: 'year',
      key: 'year',
      width: 70,
      render: (y: number) => y || '—',
    },
    {
      title: t('arxiv.columnCategory'),
      dataIndex: 'category',
      key: 'category',
      width: 90,
      render: (c: string) => (c ? <Tag color="blue">{c}</Tag> : '—'),
    },
  ]

  return (
    <>
      <Button
        icon={<CloudDownloadOutlined />}
        onClick={() => setOpen(true)}
        style={{ borderColor: '#1e40af', color: '#1e40af' }}
      >
        {t('arxiv.title')}
      </Button>
      <Modal
        title={t('arxiv.modalTitle')}
        open={open}
        onCancel={() => setOpen(false)}
        width={760}
        footer={
          <Space>
            <span style={{ color: '#64748b', fontSize: 13 }}>
              {t('common.selected', { count: selected.length })}
            </span>
            <Button onClick={() => setOpen(false)}>{t('common.cancel')}</Button>
            <Button
              type="primary"
              loading={importing}
              disabled={selected.length === 0}
              onClick={handleImport}
            >
              {t('arxiv.importButton')}
            </Button>
          </Space>
        }
      >
        <Space style={{ width: '100%', marginBottom: 12 }}>
          <Input
            placeholder={t('arxiv.keywordPlaceholder')}
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onPressEnter={handleSearch}
            style={{ width: 360 }}
            prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
          />
          <Space>
            <span style={{ fontSize: 13, color: '#64748b' }}>{t('arxiv.countLabel')}</span>
            <InputNumber
              min={1}
              max={30}
              value={maxResults}
              onChange={(v) => setMaxResults(v ?? 10)}
              style={{ width: 70 }}
            />
          </Space>
          <Button type="primary" loading={searching} onClick={handleSearch}>
            {t('arxiv.searchButton')}
          </Button>
        </Space>
        <Table
          rowKey="id"
          size="small"
          columns={columns}
          dataSource={items}
          loading={searching}
          pagination={{ pageSize: 5, size: 'small' }}
          scroll={{ y: 320 }}
          rowSelection={{
            selectedRowKeys: selected.map((s) => s.id),
            onChange: (keys) => {
              setSelected(items.filter((it) => keys.includes(it.id)))
            },
          }}
        />
      </Modal>
    </>
  )
}
