import { Tags, Trash2, Pencil } from "lucide-react";
/**
 * 标签管理组件（WP-2.1）
 *
 * - BatchTagModal：选择模式批量打标签（增/删标签到选中论文）
 * - TagManagerModal：全库标签管理（列表 + 重命名 + 删除）
 *
 * 复用 useTagStore 缓存的标签列表作为输入建议。
 */
import { useEffect, useState } from "react";
import { Modal, Select, Button, Space, Tag, Popconfirm, message, Empty, Input } from "antd";

import { useTranslation } from "react-i18next";
import { useTagStore } from "@/store/useTagStore";
import { batchTagPapers } from "@/api/papers";

// ---------------------------------------------------------------------------
// 批量打标签 Modal
// ---------------------------------------------------------------------------
interface BatchTagModalProps {
  open: boolean;
  selectedIds: string[];
  onClose: () => void;
  onSuccess?: () => void;
}

export function BatchTagModal({ open, selectedIds, onClose, onSuccess }: BatchTagModalProps) {
  const { t } = useTranslation();
  const { tags, load } = useTagStore();
  const [addTags, setAddTags] = useState<string[]>([]);
  const [removeTags, setRemoveTags] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (open) {
      void load();
      setAddTags([]);
      setRemoveTags([]);
    }
  }, [open, load]);

  const allNames = tags.map((t) => t.name);

  const handleSubmit = async () => {
    if (addTags.length === 0 && removeTags.length === 0) {
      message.warning(t("tag.batch.empty", "请至少添加或移除一个标签"));
      return;
    }
    setSubmitting(true);
    try {
      const res = await batchTagPapers({
        paper_ids: selectedIds,
        add_tags: addTags,
        remove_tags: removeTags,
      });
      if (res.success) {
        message.success(
          t("tag.batch.success", "已为 {{count}} 篇论文更新标签", { count: res.updated_count }),
        );
      } else {
        message.warning(
          t("tag.batch.partial", "更新 {{count}} 篇，{{failed}} 篇失败", {
            count: res.updated_count,
            failed: res.failed_ids.length,
          }),
        );
      }
      onSuccess?.();
      onClose();
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error(e?.response?.data?.detail || t("tag.batch.error", "批量打标签失败"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={t("tag.batch.title", "批量打标签")}
      open={open}
      onCancel={onClose}
      onOk={handleSubmit}
      confirmLoading={submitting}
      okText={t("tag.batch.apply", "应用到 {{count}} 篇", { count: selectedIds.length })}
      destroyOnHidden
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <div>
          <div style={{ marginBottom: 6, fontSize: 13, color: "var(--pf-text-secondary)" }}>
            {t("tag.batch.addLabel", "添加标签")}
          </div>
          <Select
            mode="tags"
            style={{ width: "100%" }}
            placeholder={t("tag.batch.addPlaceholder", "输入或选择要添加的标签")}
            value={addTags}
            onChange={setAddTags}
            options={allNames.map((n) => ({ label: n, value: n }))}
            tokenSeparators={[",", " "]}
          />
        </div>
        <div>
          <div style={{ marginBottom: 6, fontSize: 13, color: "var(--pf-text-secondary)" }}>
            {t("tag.batch.removeLabel", "移除标签（可选）")}
          </div>
          <Select
            mode="multiple"
            style={{ width: "100%" }}
            placeholder={t("tag.batch.removePlaceholder", "选择要从选中论文移除的标签")}
            value={removeTags}
            onChange={setRemoveTags}
            options={allNames.map((n) => ({ label: n, value: n }))}
          />
        </div>
        <div style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
          {t("tag.batch.hint", "将对选中的 {{count}} 篇论文批量操作，不影响其他论文。", {
            count: selectedIds.length,
          })}
        </div>
      </Space>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// 标签管理面板 Modal（全库列表 + 重命名 + 删除）
// ---------------------------------------------------------------------------
export function TagManagerModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const { tags, loading, load, rename, remove } = useTagStore();
  const [editing, setEditing] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");

  useEffect(() => {
    if (open) {
      void load();
      setEditing(null);
    }
  }, [open, load]);

  const startEdit = (name: string) => {
    setEditing(name);
    setEditValue(name);
  };

  const confirmRename = async () => {
    if (!editing || !editValue.trim()) return;
    const ok = await rename({ old_name: editing, new_name: editValue.trim() });
    if (ok) {
      message.success(t("tag.rename.success", "已重命名"));
      setEditing(null);
    } else {
      message.error(t("tag.rename.error", "重命名失败"));
    }
  };

  const confirmDelete = async (name: string) => {
    const ok = await remove(name);
    if (ok) {
      message.success(t("tag.delete.success", "已删除标签"));
    } else {
      message.error(t("tag.delete.error", "删除失败"));
    }
  };

  return (
    <Modal
      title={
        <span>
          <Tags style={{ marginRight: 8 }} />
          {t("tag.manager.title", "标签管理")}
        </span>
      }
      open={open}
      onCancel={onClose}
      footer={null}
      width={520}
      destroyOnHidden
    >
      {loading && tags.length === 0 ? (
        <div style={{ textAlign: "center", padding: 24 }}>...</div>
      ) : tags.length === 0 ? (
        <Empty description={t("tag.manager.empty", "暂无标签，上传论文后可在详情页添加")} />
      ) : (
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          {tags.map((tag) => (
            <div
              key={tag.name}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "6px 8px",
                borderRadius: 6,
                background: "var(--pf-bg-secondary)",
              }}
            >
              {editing === tag.name ? (
                <Space size="small">
                  <Input
                    size="small"
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onPressEnter={confirmRename}
                    style={{ width: 160 }}
                    autoFocus
                  />
                  <Button size="small" type="primary" onClick={confirmRename}>
                    {t("common.ok", "确定")}
                  </Button>
                  <Button size="small" onClick={() => setEditing(null)}>
                    {t("common.cancel", "取消")}
                  </Button>
                </Space>
              ) : (
                <Space size="small">
                  <Tag color="blue">{tag.name}</Tag>
                  <span style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
                    {t("tag.manager.count", "{{count}} 篇", { count: tag.count })}
                  </span>
                </Space>
              )}
              {editing !== tag.name && (
                <Space size="small">
                  <Button
                    size="small"
                    type="text"
                    icon={<Pencil />}
                    onClick={() => startEdit(tag.name)}
                  />
                  <Popconfirm
                    title={t("tag.delete.confirm", "确认从全库删除该标签？")}
                    onConfirm={() => confirmDelete(tag.name)}
                  >
                    <Button size="small" type="text" danger icon={<Trash2 />} />
                  </Popconfirm>
                </Space>
              )}
            </div>
          ))}
        </Space>
      )}
    </Modal>
  );
}
