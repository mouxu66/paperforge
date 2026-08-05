import { Plus, Pencil, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button, Card, Empty, Popconfirm, Space, Typography, message } from "antd";

import { useTranslation } from "react-i18next";
import { deleteNote, fetchNotesByPaper } from "@/api/notes";
import type { Note } from "@/api/types";
import NoteEditor from "./NoteEditor";

const { Title, Text, Paragraph } = Typography;

interface NoteListProps {
  paperId: string;
}

/**
 * 笔记列表 —— 展示某篇论文下的所有笔记。
 * 支持新建 / 内联编辑 / 删除（Popconfirm 二次确认），操作后自动刷新列表。
 */
export default function NoteList({ paperId }: NoteListProps) {
  const { t } = useTranslation();
  const [notes, setNotes] = useState<Note[]>([]);
  const [loading, setLoading] = useState(true);
  // 编辑态：'new' 表示新建；具体 id 表示编辑某条；null 表示列表态
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchNotesByPaper(paperId);
      setNotes(list);
    } catch {
      // 错误提示由 axios 响应拦截器统一处理
    } finally {
      setLoading(false);
    }
  }, [paperId]);

  useEffect(() => {
    load();
  }, [load]);

  const handleDelete = async (noteId: string) => {
    try {
      await deleteNote(noteId);
      message.success(t("notes.deleted"));
      load();
    } catch {
      // 错误提示由 axios 响应拦截器统一处理
    }
  };

  // 保存成功：退出编辑态并刷新列表
  const handleSaved = () => {
    setEditing(null);
    load();
  };

  // 新建态：内联展示编辑器
  if (editing === "new") {
    return (
      <div className="pf-glass-card" style={{ padding: 16 }}>
        <NoteEditor paperId={paperId} onSave={handleSaved} onCancel={() => setEditing(null)} />
      </div>
    );
  }

  // 编辑态：内联展示编辑器（预填内容）
  const editingNote = editing ? (notes.find((n) => n.id === editing) ?? null) : null;
  if (editing && editingNote) {
    return (
      <div className="pf-glass-card" style={{ padding: 16 }}>
        <NoteEditor
          paperId={paperId}
          note={editingNote}
          onSave={handleSaved}
          onCancel={() => setEditing(null)}
        />
      </div>
    );
  }

  return (
    <div className="pf-glass-card" style={{ padding: 16 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <Title level={5} className="pf-serif" style={{ margin: 0 }}>
          {t("notes.title")}
        </Title>
        <Button type="primary" icon={<Plus />} onClick={() => setEditing("new")}>
          {t("notes.newNote")}
        </Button>
      </div>

      {loading ? (
        <Text type="secondary">{t("notes.loading")}</Text>
      ) : notes.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("notes.empty")} />
      ) : (
        <Space direction="vertical" size={12} style={{ display: "flex" }}>
          {notes.map((note) => (
            <Card
              key={note.id}
              variant="borderless"
              style={{ background: "var(--pf-bg-tertiary)" }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 12,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Paragraph
                    style={{
                      margin: 0,
                      color: "var(--pf-text-secondary)",
                      fontSize: 14,
                      lineHeight: 1.7,
                    }}
                    ellipsis={{ rows: 2 }}
                  >
                    {note.content.slice(0, 100)}
                  </Paragraph>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t("notes.updatedAt", { date: note.updatedAt })}
                  </Text>
                </div>
                <Space size={4}>
                  <Button
                    size="small"
                    type="text"
                    icon={<Pencil />}
                    onClick={() => setEditing(note.id)}
                  />
                  <Popconfirm
                    title={t("notes.confirmDelete")}
                    okText={t("common.delete")}
                    cancelText={t("common.cancel")}
                    onConfirm={() => handleDelete(note.id)}
                  >
                    <Button size="small" type="text" danger icon={<Trash2 />} />
                  </Popconfirm>
                </Space>
              </div>
            </Card>
          ))}
        </Space>
      )}
    </div>
  );
}
