import { useEffect, useState } from "react";
import { Alert, Button, Card, Form, Input, InputNumber, Space, Switch, Typography, App } from "antd";
import { getSettings, updateSettings } from "@/api/settings";
import type { AppSettings } from "@/api/types";

const { Text } = Typography;

/**
 * 双模型交叉复核 / 被引情感云端复核 开关面板。
 * 在线读写全局设置（运行时生效，重启后恢复默认），与 DepthSettingsPanel 风格一致。
 */
export default function SecondOpinionSettingsPanel() {
  const { message } = App.useApp();
  const [form] = Form.useForm<AppSettings>();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setLoading(true);
    getSettings()
      .then((s) => form.setFieldsValue(s))
      .catch(() => message.error("获取双模型设置失败"))
      .finally(() => setLoading(false));
  }, [form, message]);

  const handleSave = async (values: AppSettings) => {
    setSaving(true);
    try {
      const updated = await updateSettings(values);
      form.setFieldsValue(updated);
      message.success("双模型评审设置已保存（重启服务后恢复默认）");
    } catch {
      // http 拦截器已提示错误
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      className="pf-glass-card"
      style={{ marginBottom: 24 }}
      title={
        <Space>
          <span role="img" aria-label="second-opinion">
            🧭
          </span>
          <span>双模型评审设置</span>
        </Space>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="运行时生效"
        description="以下开关修改后立即对后续评审生效，但进程重启后会恢复 .env 默认值。"
      />
      <Form form={form} layout="vertical" onFinish={handleSave} disabled={loading}>
        <Form.Item
          name="secondOpinionEnabled"
          label={<Text strong>双模型交叉复核（本地主 + 云端终审）</Text>}
          valuePropName="checked"
          tooltip="开启后每条评审会额外请第二个云端/独立模型独立打分，分歧时标记需人工复核"
        >
          <Switch />
        </Form.Item>

        <Form.Item
          name="secondOpinionOverride"
          label={<Text strong>云端分歧时覆盖本地结果</Text>}
          valuePropName="checked"
          tooltip="默认关闭以保护已校准的本地分；开启且分歧超阈值时以云端（更强）模型结果覆盖本地"
        >
          <Switch />
        </Form.Item>

        <Form.Item
          name="secondOpinionThreshold"
          label={<Text strong>分歧阈值 |Δscore|</Text>}
          rules={[{ required: true, message: "必填" }]}
        >
          <InputNumber min={0.01} max={1} step={0.01} style={{ width: 160 }} />
        </Form.Item>

        <Form.Item
          name="secondOpinionModel"
          label={<Text strong>云端第二评审模型（文本）</Text>}
          rules={[{ required: true, message: "必填" }]}
          tooltip="注意：不要用 glm-4v-flash（视觉模型，纯文本常解析失败）；默认 glm-4-flash"
        >
          <Input placeholder="glm-4-flash" style={{ width: 280 }} />
        </Form.Item>

        <Form.Item
          name="sentimentRecheckEnabled"
          label={<Text strong>被引情感云端复核</Text>}
          valuePropName="checked"
          tooltip="本地对被引情感的低置信分类会请云端复核（仅低置信引用触发以控成本）"
        >
          <Switch />
        </Form.Item>

        <Form.Item
          name="sentimentRecheckLowConf"
          label={<Text strong>触发云端复核的本地置信度上限</Text>}
          rules={[{ required: true, message: "必填" }]}
        >
          <InputNumber min={0} max={1} step={0.05} style={{ width: 160 }} />
        </Form.Item>

        <Form.Item style={{ marginTop: 8 }}>
          <Button type="primary" htmlType="submit" loading={saving}>
            保存双模型设置
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}
