import { Card, Select, Input, Button, Tag } from 'antd'
import { FilterOutlined, SendOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import type { Paper } from '@/api/types'

const { TextArea } = Input

interface AskInputProps {
  query: string
  onQueryChange: (v: string) => void
  loading: boolean
  onSubmit: () => void
  paperOptions: Paper[]
  selectedIds: string[]
  onSelectedIdsChange: (ids: string[]) => void
}

/** 提问输入区：论文范围选择 + 文本框 + 提交按钮 */
export default function AskInput({
  query,
  onQueryChange,
  loading,
  onSubmit,
  paperOptions,
  selectedIds,
  onSelectedIdsChange,
}: AskInputProps) {
  const { t } = useTranslation()
  // Select 选项：标题 + 年份，便于搜索定位
  const selectOptions = paperOptions.map((p) => ({
    value: p.id,
    label: `${p.title} (${p.year})`,
  }))

  return (
    <Card className="pf-glass-card" variant="borderless" style={{ marginBottom: 20, padding: 8 }}>
      {/* 论文范围选择 */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
          <FilterOutlined style={{ color: '#1e40af', fontSize: 13 }} />
          <span style={{ fontSize: 13, fontWeight: 500, color: '#475569' }}>{t('ask.paperScope')}</span>
          <Tag
            color={selectedIds.length > 0 ? 'blue' : 'default'}
            style={{ marginInlineStart: 0, fontSize: 12 }}
          >
            {selectedIds.length > 0 ? t('ask.selectedCount', { count: selectedIds.length }) : t('ask.allPapers')}
          </Tag>
        </div>
        <Select
          mode="multiple"
          showSearch
          allowClear
          optionFilterProp="label"
          placeholder={t('ask.searchPaperPlaceholder')}
          value={selectedIds}
          onChange={onSelectedIdsChange}
          options={selectOptions}
          maxTagCount={3}
          style={{ width: '100%' }}
          size="large"
        />
      </div>

      <TextArea
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder={t('ask.inputPlaceholder')}
        autoSize={{ minRows: 3, maxRows: 6 }}
        onPressEnter={(e) => {
          if (!e.shiftKey) {
            e.preventDefault()
            onSubmit()
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
          {t('ask.inputHint')}
        </span>
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={onSubmit}
          loading={loading}
        >
          {t('ask.submit')}
        </Button>
      </div>
    </Card>
  )
}
