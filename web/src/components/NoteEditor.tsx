import { useState } from "react";
import { Button, Input, Space, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import { createNote, updateNote } from "@/api/notes";
import type { Note } from "@/api/types";

const { Text } = Typography;

interface NoteEditorProps {
  paperId: string;
  note?: Note | null;
  onSave: () => void;
  onCancel: () => void;
}

/** 笔记编辑器 —— 新建 / 编辑共用，不带外层卡片。支持 Markdown 输入与实时字数统计。 */
export default function NoteEditor({ paperId, note, onSave, onCancel }: NoteEditorProps) {
  const { t } = useTranslation();
  const isEdit = !!note;
  const [content, setContent] = useState(note?.content ?? "");
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    const trimmed = content.trim();
    if (!trimmed) {
      message.warning(t("notes.emptyContent"));
      return;
    }
    setSaving(true);
    try {
      if (isEdit && note) {
        await updateNote(note.id, { content: trimmed });
        message.success(t("notes.updated"));
      } else {
        await createNote({ paperId, content: trimmed });
        message.success(t("notes.created"));
      }
      onSave();
    } catch {
      // 错误提示由 axios 响应拦截器统一处理
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <Input.TextArea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        placeholder={t("notes.placeholder")}
        autoSize={{ minRows: 4 }}
        style={{ fontSize: 14, lineHeight: 1.8 }}
      />
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginTop: 8,
        }}
      >
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t("notes.charCount", { count: content.length })}
        </Text>
        <Space>
          <Button onClick={onCancel}>{t("common.cancel")}</Button>
          <Button type="primary" loading={saving} onClick={handleSave}>
            {isEdit ? t("common.save") : t("notes.new")}
          </Button>
        </Space>
      </div>
    </div>
  );
}
