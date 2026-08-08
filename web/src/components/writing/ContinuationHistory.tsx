import { History, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Drawer, Empty, Spin, Tag, Typography, App } from "antd";

const KIND_LABEL: Record<"continue" | "rewrite", { text: string; color: string }> = {
  continue: { text: "续写", color: "blue" },
  rewrite: { text: "改写", color: "purple" },
};

import { fetchContinuations } from "@/api/writing";
import type { ContinuationRecord } from "@/api/types";

interface ContinuationHistoryProps {
  chapterId: number | null;
  open: boolean;
  onClose: () => void;
  /** 恢复某条续写内容：插入到编辑器当前光标位置 */
  onRestore: (content: string) => void;
}

/**
 * C2：续写历史抽屉 —— 展示某章节的所有续写记录，支持一键恢复。
 *
 * 打开时拉取最新列表，每条显示：生成时间、续写方向（如有）、
 * 内容预览（前 100 字符）。点击「恢复」插入到编辑器光标位置。
 */
export default function ContinuationHistory({
  chapterId,
  open,
  onClose,
  onRestore,
}: ContinuationHistoryProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [records, setRecords] = useState<ContinuationRecord[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!chapterId) return;
    setLoading(true);
    try {
      const data = await fetchContinuations(chapterId);
      setRecords(data);
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setLoading(false);
    }
  }, [chapterId]);

  useEffect(() => {
    if (open && chapterId) {
      void load();
    }
  }, [open, chapterId, load]);

  const handleRestore = (rec: ContinuationRecord) => {
    onRestore(rec.content);
    message.success(t("continuation.restored"));
    onClose();
  };

  return (
    <Drawer
      title={
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <History />
          <span className="pf-serif">{t("continuation.title")}</span>
          {records.length > 0 && <Tag color="blue">{records.length}</Tag>}
        </span>
      }
      open={open}
      onClose={onClose}
      width={420}
      extra={
        <Button size="small" icon={<RefreshCw />} onClick={() => void load()} loading={loading}>
          {t("continuation.refresh")}
        </Button>
      }
    >
      {loading && records.length === 0 ? (
        <div style={{ textAlign: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : records.length === 0 ? (
        <Empty description={t("continuation.empty")} />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {records.map((rec) => (
            <div
              key={rec.id}
              style={{
                border: "1px solid var(--pf-border)",
                borderRadius: 6,
                padding: 12,
                background: "var(--pf-bg-secondary)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 6,
                }}
              >
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {rec.createdAt}
                </Typography.Text>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <Tag color={KIND_LABEL[rec.kind]?.color ?? "default"}>
                    {KIND_LABEL[rec.kind]?.text ?? rec.kind}
                  </Tag>
                  {rec.direction && (
                    <Tag
                      color="geekblue"
                      style={{
                        margin: 0,
                        maxWidth: 200,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                      title={rec.direction}
                    >
                      {rec.direction}
                    </Tag>
                  )}
                </div>
              </div>
              <Typography.Paragraph
                style={{
                  margin: 0,
                  fontSize: 13,
                  color: "var(--pf-text-secondary)",
                  lineHeight: 1.6,
                }}
                ellipsis={{ rows: 3 }}
              >
                {rec.content.slice(0, 100)}
                {rec.content.length > 100 ? "…" : ""}
              </Typography.Paragraph>
              <div style={{ marginTop: 8, textAlign: "right" }}>
                <Button size="small" type="link" onClick={() => handleRestore(rec)}>
                  {t("continuation.restore")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </Drawer>
  );
}
