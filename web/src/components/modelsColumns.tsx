import { Trash2, Pencil } from "lucide-react";
import type { ColumnsType } from "antd/es/table";
import { Button, Popconfirm, Space, Switch, Tag, Tooltip } from "antd";

import type { TFunction } from "i18next";
import type { LLMConfig } from "@/api/types";

// Provider 名称到 Tag 颜色的映射
const providerColor: Record<string, string> = {
  openai: "green",
  zhipu: "blue",
  deepseek: "purple",
};

// 列定义需要依赖的回调函数集合
interface ModelsColumnActions {
  onToggleEnabled: (record: LLMConfig, enabled: boolean) => void;
  onEdit: (record: LLMConfig) => void;
  onDelete: (id: number) => void;
}

/**
 * 构建模型管理表格的列定义。
 *
 * 由于列中包含交互回调（启用切换、编辑、删除），
 * 因此通过 actions 参数将父组件的处理函数注入进来。
 */
export function getModelsColumns(
  t: TFunction,
  actions: ModelsColumnActions,
): ColumnsType<LLMConfig> {
  const { onToggleEnabled, onEdit, onDelete } = actions;

  return [
    {
      title: t("models.columnDisplayName"),
      dataIndex: "displayName",
      key: "displayName",
      width: 180,
      render: (text: string, record: LLMConfig) => (
        <span
          style={{
            fontWeight: record.enabled ? 600 : 400,
            color: record.enabled ? "var(--pf-text-primary)" : "var(--pf-text-placeholder)",
          }}
        >
          {text}
        </span>
      ),
    },
    {
      title: t("models.columnApiUrl"),
      dataIndex: "apiUrl",
      key: "apiUrl",
      ellipsis: true,
      render: (url: string) => (
        <Tooltip title={url}>
          <span style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>{url}</span>
        </Tooltip>
      ),
    },
    {
      title: t("models.columnModelId"),
      dataIndex: "modelId",
      key: "modelId",
      width: 140,
      render: (text: string) => <code style={{ fontSize: 12 }}>{text}</code>,
    },
    {
      title: "Provider",
      dataIndex: "provider",
      key: "provider",
      width: 100,
      render: (p: string) => <Tag color={providerColor[p] || "default"}>{p}</Tag>,
    },
    {
      title: "API Key",
      dataIndex: "apiKey",
      key: "apiKey",
      width: 120,
      render: (key: string) => (
        <span
          style={{ fontSize: 12, color: "var(--pf-text-placeholder)", fontFamily: "monospace" }}
        >
          {key || t("models.notConfigured")}
        </span>
      ),
    },
    {
      title: t("models.columnEnabled"),
      dataIndex: "enabled",
      key: "enabled",
      width: 70,
      render: (enabled: boolean, record: LLMConfig) => (
        <Switch
          checked={enabled}
          size="small"
          onChange={(checked) => onToggleEnabled(record, checked)}
        />
      ),
    },
    {
      title: t("models.columnActions"),
      key: "action",
      width: 100,
      render: (_: unknown, record: LLMConfig) => (
        <Space size={4}>
          <Button type="text" size="small" icon={<Pencil />} onClick={() => onEdit(record)} />
          <Popconfirm
            title={t("models.confirmDeleteTitle")}
            onConfirm={() => onDelete(record.id)}
            okText={t("common.delete")}
            cancelText={t("common.cancel")}
          >
            <Button type="text" size="small" danger icon={<Trash2 />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];
}
