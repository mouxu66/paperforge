import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Form, Input, Modal, Select, Tree, Typography } from 'antd'
import type { DataNode } from 'antd/es/tree'
import type { FormInstance } from 'antd'
import { fetchTemplates, fetchUserTemplates } from '@/api/writing'
import type { Template, TemplateChapter, UserTemplate } from '@/api/types'

interface ProjectFormProps {
  /** 由父组件创建并控制的 Form 实例 */
  form: FormInstance
  open: boolean
  submitting: boolean
  onOk: () => void
  onCancel: () => void
}

/** 将用户自定义模板转换为统一展示格式（与系统模板结构一致） */
function userTemplateToTemplate(t: UserTemplate): Template {
  return { id: t.id, name: t.name, description: t.description, chapters: t.chapters }
}

/**
 * 新建 / 编辑写作项目弹窗。
 *
 * Form 实例由父组件持有，本组件仅负责渲染：
 * - 父组件在打开时调用 form.resetFields / setFieldsValue
 * - onOk 触发时由父组件调用 form.validateFields 提交
 *
 * 「模板选择」：合并系统模板 + 用户自定义模板，选模板后预览大纲结构。
 */
export default function ProjectForm({
  form,
  open,
  submitting,
  onOk,
  onCancel,
}: ProjectFormProps) {
  const { t } = useTranslation()
  const [templates, setTemplates] = useState<Template[]>([])
  const [userTemplates, setUserTemplates] = useState<UserTemplate[]>([])
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | undefined>()

  // 打开弹窗时拉取系统模板 + 用户自定义模板
  useEffect(() => {
    if (!open) return
    Promise.all([fetchTemplates().catch(() => []), fetchUserTemplates().catch(() => [])])
      .then(([sys, usr]) => {
        setTemplates(sys)
        setUserTemplates(usr)
      })
      .catch(() => {
        // 错误已由 http 拦截器提示
      })
  }, [open])

  // 关闭时重置选中模板
  useEffect(() => {
    if (!open) setSelectedTemplateId(undefined)
  }, [open])

  // 合并系统模板 + 用户自定义模板用于查找预览
  const allTemplates: Template[] = [
    ...templates,
    ...userTemplates.map(userTemplateToTemplate),
  ]
  const selectedTemplate = allTemplates.find((t) => t.id === selectedTemplateId)

  const templateToTreeData = (chapters: TemplateChapter[]): DataNode[] =>
    chapters.map((c) => ({
      key: c.title,
      title: c.title,
      children: templateToTreeData(c.children),
    }))

  return (
    <Modal
      title={t('project.createTitle')}
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      confirmLoading={submitting}
      okText={t('project.create')}
      cancelText={t('common.cancel')}
      width={560}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          name="title"
          label={t('project.titleLabel')}
          rules={[{ required: true, message: t('project.titleRequired') }]}
        >
          <Input placeholder={t('project.titlePlaceholder')} />
        </Form.Item>
        <Form.Item name="keywords" label={t('project.keywordsLabel')}>
          <Select
            mode="tags"
            placeholder={t('project.keywordsPlaceholder')}
            tokenSeparators={[',', '，']}
            style={{ width: '100%' }}
          />
        </Form.Item>
        <Form.Item name="targetJournal" label={t('project.targetJournalLabel')}>
          <Input placeholder={t('project.targetJournalPlaceholder')} />
        </Form.Item>
        <Form.Item name="templateId" label={t('project.templateLabel')}>
          <Select
            placeholder={t('project.templatePlaceholder')}
            allowClear
            onChange={(val) => setSelectedTemplateId(val)}
            options={[
              { value: '', label: t('project.blankProject') },
              ...templates.map((t) => ({ value: t.id, label: t.name })),
              ...(userTemplates.length > 0
                ? [{ value: '__divider__', label: t('project.customTemplates'), disabled: true }]
                : []),
              ...userTemplates.map((t) => ({ value: t.id, label: `★ ${t.name}` })),
            ]}
          />
        </Form.Item>
        {selectedTemplate && (
          <div
            style={{
              marginBottom: 16,
              padding: '8px 12px',
              background: 'rgba(245, 247, 251, 0.7)',
              borderRadius: 8,
              border: '1px solid #e8ecf1',
            }}
          >
            <Typography.Text
              className="pf-serif"
              style={{ fontSize: 13, fontWeight: 600, color: '#1e40af' }}
            >
              {selectedTemplate.name}
            </Typography.Text>
            <Typography.Text
              style={{ fontSize: 12, color: '#64748b', marginLeft: 8 }}
            >
              {selectedTemplate.description}
            </Typography.Text>
            <Tree
              treeData={templateToTreeData(selectedTemplate.chapters)}
              defaultExpandAll
              selectable={false}
              showLine
              style={{ marginTop: 8, fontSize: 13 }}
            />
          </div>
        )}
      </Form>
    </Modal>
  )
}
