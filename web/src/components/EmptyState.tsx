import { Empty } from 'antd'
import { useTranslation } from 'react-i18next'

interface Props {
  description?: string
}

export default function EmptyState({ description }: Props) {
  const { t } = useTranslation()
  return (
    <div
      style={{
        textAlign: 'center',
        padding: '64px 16px',
        color: '#9ca3af',
      }}
    >
      <Empty description={description ?? t('common.noData')} />
    </div>
  )
}
