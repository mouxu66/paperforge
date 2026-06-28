import { useEffect, useState, useCallback } from 'react'
import { Button, Card, Form, Space, Table, message } from 'antd'
import { PlusOutlined, ThunderboltOutlined } from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { fetchModels, createModel, updateModel, deleteModel } from '@/api/models'
import type { LLMConfig, LLMConfigCreate } from '@/api/types'
import { useModelStore } from '@/store/useModelStore'
import AddModelModal from '@/components/AddModelModal'
import { getModelsColumns } from '@/components/modelsColumns'

export default function ModelsPage() {
  const [form] = Form.useForm()
  const { t } = useTranslation()
  const [models, setModels] = useState<LLMConfig[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const loadModelStore = useModelStore((s) => s.load)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchModels()
      setModels(data)
    } catch {
      // client.ts interceptor handles error
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const openAdd = () => {
    form.resetFields()
    form.setFieldsValue({ enabled: true })
    setEditingId(null)
    setModalOpen(true)
  }

  const openEdit = (record: LLMConfig) => {
    form.setFieldsValue({
      displayName: record.displayName,
      apiUrl: record.apiUrl,
      apiKey: '',
      modelId: record.modelId,
      enabled: record.enabled,
    })
    setEditingId(record.id)
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSubmitting(true)
      if (editingId !== null) {
        await updateModel(editingId, {
          displayName: values.displayName,
          apiUrl: values.apiUrl,
          apiKey: values.apiKey || undefined,
          modelId: values.modelId,
          enabled: values.enabled,
        })
        message.success(t('models.updated'))
      } else {
        const req: LLMConfigCreate = {
          displayName: values.displayName,
          apiUrl: values.apiUrl,
          apiKey: values.apiKey || '',
          modelId: values.modelId,
          enabled: values.enabled,
        }
        await createModel(req)
        message.success(t('models.added'))
      }
      setModalOpen(false)
      await load()
      await loadModelStore()
    } catch {
      // validation or API error
    } finally {
      setSubmitting(false)
    }
  }

  const handleToggleEnabled = async (record: LLMConfig, enabled: boolean) => {
    try {
      await updateModel(record.id, { enabled })
      message.success(enabled ? t('models.enabled') : t('models.disabled'))
      await load()
      await loadModelStore()
    } catch {
      // ignored
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteModel(id)
      message.success(t('models.deleted'))
      await load()
      await loadModelStore()
    } catch {
      // ignored
    }
  }

  const columns = getModelsColumns(t, {
    onToggleEnabled: handleToggleEnabled,
    onEdit: openEdit,
    onDelete: handleDelete,
  })

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '24px 0' }}>
      <Card
        className="pf-glass-card"
        title={
          <Space>
            <ThunderboltOutlined style={{ color: '#1e40af' }} />
            <span className="pf-serif" style={{ fontSize: 20, fontWeight: 600 }}>
              {t('models.title')}
            </span>
          </Space>
        }
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
            {t('models.add')}
          </Button>
        }
      >
        <p style={{ color: '#64748b', fontSize: 13, marginBottom: 16 }}>
          {t('models.description')}
        </p>
        <Table
          dataSource={models}
          columns={columns}
          rowKey="id"
          loading={loading}
          pagination={false}
          size="middle"
          locale={{ emptyText: t('models.empty') }}
        />
      </Card>

      <AddModelModal
        form={form}
        open={modalOpen}
        editingId={editingId}
        submitting={submitting}
        onOk={handleSubmit}
        onCancel={() => setModalOpen(false)}
      />
    </div>
  )
}
