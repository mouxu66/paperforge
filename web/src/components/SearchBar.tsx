import { useEffect, useRef, useState } from 'react'
import { AutoComplete, Select, Space, Input, Switch, Tooltip } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { fetchSuggest } from '@/api/papers'
import type { SuggestItem } from '@/api/types'
import { SORT_OPTIONS } from '@/utils/constants'

interface Props {
  keyword: string
  sort: string
  semantic: boolean
  onKeyword: (v: string) => void
  onSort: (v: string) => void
  onSemantic: (v: boolean) => void
}

export default function SearchBar({ keyword, sort, semantic, onKeyword, onSort, onSemantic }: Props) {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [options, setOptions] = useState<{ value: string; label: React.ReactNode }[]>([])
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 输入变化时触发搜索建议（300ms 防抖，value.length >= 2 才请求）
  const handleSearch = (value: string) => {
    onKeyword(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)

    if (value.trim().length < 2) {
      setOptions([])
      return
    }

    debounceRef.current = setTimeout(async () => {
      try {
        const items: SuggestItem[] = await fetchSuggest(value)
        setOptions(
          items.map((it) => ({
            value: it.id,
            label: (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '2px 0',
                }}
              >
                <span
                  style={{
                    fontSize: 11,
                    color: '#94a3b8',
                    fontFamily: 'monospace',
                    flexShrink: 0,
                  }}
                >
                  {it.id}
                </span>
                {/* 后端 highlight 字段包含 <mark> 标签，用 dangerouslySetInnerHTML 渲染高亮 */}
                <span
                  style={{ color: '#334155', fontSize: 13, lineHeight: 1.4 }}
                  dangerouslySetInnerHTML={{
                    __html: it.highlight || it.title,
                  }}
                />
              </div>
            ),
          })),
        )
      } catch {
        setOptions([])
      }
    }, 300)
  }

  // 选中下拉项：跳转到详情页
  const handleSelect = (paperId: string) => {
    navigate(`/paper/${paperId}`)
  }

  // 组件卸载时清理定时器
  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [])

  return (
    <Space style={{ width: '100%' }} size={12}>
      <AutoComplete
        value={keyword}
        options={options}
        onSearch={handleSearch}
        onSelect={handleSelect}
        style={{ width: 420 }}
        size="large"
        allowClear
        onChange={(v: string) => onKeyword(v)}
      >
        <Input
          allowClear
          prefix={<SearchOutlined style={{ color: '#94a3b8' }} />}
          placeholder={t('search.placeholder')}
          style={{ borderRadius: 8 }}
          // 回车触发列表过滤（onKeyword 已在 onSearch 中同步，回车保持现有搜索逻辑）
          onPressEnter={() => onKeyword(keyword)}
        />
      </AutoComplete>
      <Select
        value={sort}
        onChange={onSort}
        size="large"
        style={{ width: 150 }}
        options={SORT_OPTIONS.map((o) => ({ label: o.label, value: o.value }))}
      />
      <Tooltip title={semantic ? t('search.semanticMode') : t('search.keywordMode')}>
        <Switch
          checkedChildren={t('search.semanticLabel')}
          unCheckedChildren={t('search.keywordLabel')}
          checked={semantic}
          onChange={onSemantic}
        />
      </Tooltip>
    </Space>
  )
}
