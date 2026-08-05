import { Form, Input, Modal, Switch } from "antd";
import type { FormInstance } from "antd";
import { useTranslation } from "react-i18next";

interface AddModelModalProps {
  // 由父组件创建并控制的 Form 实例（用于 resetFields / setFieldsValue / validateFields）
  form: FormInstance;
  open: boolean;
  // 编辑模式下的模型 ID；新增时为 null
  editingId: number | null;
  submitting: boolean;
  onOk: () => void;
  onCancel: () => void;
}

/**
 * 添加 / 编辑模型弹窗。
 *
 * Form 实例由父组件持有，本组件仅负责渲染：
 * - 父组件在 openAdd / openEdit 时调用 form.resetFields / setFieldsValue
 * - onOk 触发时由父组件调用 form.validateFields 提交
 */
export default function AddModelModal({
  form,
  open,
  editingId,
  submitting,
  onOk,
  onCancel,
}: AddModelModalProps) {
  const { t } = useTranslation();
  return (
    <Modal
      title={editingId !== null ? t("models.editModel") : t("models.addModel")}
      open={open}
      onOk={onOk}
      onCancel={onCancel}
      confirmLoading={submitting}
      okText={editingId !== null ? t("common.save") : t("models.add")}
      cancelText={t("common.cancel")}
      width={560}
    >
      <Form form={form} layout="vertical" style={{ marginTop: 16 }}>
        <Form.Item
          name="displayName"
          label={t("models.displayName")}
          rules={[{ required: true, message: t("models.displayNameRequired") }]}
        >
          <Input placeholder={t("models.displayNamePlaceholder")} />
        </Form.Item>
        <Form.Item
          name="apiUrl"
          label={t("models.apiUrl")}
          rules={[{ required: true, message: t("models.apiUrlRequired") }]}
        >
          <Input placeholder={t("models.apiUrlPlaceholder")} />
        </Form.Item>
        <Form.Item name="apiKey" label={t("models.apiKeyOptional")}>
          <Input.Password placeholder={t("models.apiKeyPlaceholder")} />
        </Form.Item>
        <Form.Item
          name="modelId"
          label={t("models.modelId")}
          rules={[{ required: true, message: t("models.modelIdRequired") }]}
        >
          <Input placeholder={t("models.modelIdPlaceholder")} />
        </Form.Item>
        <Form.Item name="enabled" label={t("models.switchLabel")} valuePropName="checked">
          <Switch
            checkedChildren={t("models.switchEnabled")}
            unCheckedChildren={t("models.switchDisabled")}
          />
        </Form.Item>
        {editingId !== null && (
          <p style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }}>
            {t("models.apiKeyHint")}
          </p>
        )}
      </Form>
    </Modal>
  );
}
