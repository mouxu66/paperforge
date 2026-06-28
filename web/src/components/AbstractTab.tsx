import { Descriptions, Typography, Divider, Tag } from 'antd'
import { DatabaseOutlined, FileTextOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import type { Paper } from '@/api/types'
import { SOURCE_COLOR } from '@/utils/constants'
import { formatSize } from '@/utils/format'

const { Title, Text } = Typography

interface AbstractTabProps {
  paper: Paper
}

/** 摘要详情面板：展示论文元信息 + 摘要正文 */
export default function AbstractTab({ paper }: AbstractTabProps) {
  const { t } = useTranslation()
  return (
    <div>
      <Descriptions
        column={2}
        size="small"
        labelStyle={{
          fontWeight: 600,
          color: '#1a1a2e',
          width: 90,
        }}
        contentStyle={{ color: '#475569', lineHeight: 1.8 }}
      >
        <Descriptions.Item label={t('paper.authors')} span={2}>
          <Text style={{ color: '#666', lineHeight: 1.8 }}>
            {paper.authors.join(', ')}
          </Text>
        </Descriptions.Item>
        <Descriptions.Item label={t('paper.year')}>
          {paper.journal || 'arXiv preprint'}
        </Descriptions.Item>
        <Descriptions.Item label={t('paper.year')}>{paper.year}</Descriptions.Item>
        <Descriptions.Item label={t('paper.source')}>
          <Tag color={SOURCE_COLOR[paper.source]}>
            {paper.source.toUpperCase()}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label={t('paper.citations')}>
          {paper.citations.toLocaleString()}
        </Descriptions.Item>
        <Descriptions.Item label="ID">{paper.id}</Descriptions.Item>
        <Descriptions.Item label={t('paper.textChunks')}>
          <DatabaseOutlined /> {paper.chunkCount}
        </Descriptions.Item>
        <Descriptions.Item label={t('paper.indexSize')}>
          <FileTextOutlined /> {formatSize(paper.indexSize)}
        </Descriptions.Item>
      </Descriptions>

      <Divider />

      <Title level={5} className="pf-serif" style={{ marginBottom: 12 }}>
        {t('paper.abstract')}
      </Title>
      <p
        style={{
          color: '#334155',
          fontSize: 15,
          lineHeight: 1.9,
          textAlign: 'justify',
          margin: 0,
        }}
      >
        {paper.abstract}
      </p>
    </div>
  )
}
