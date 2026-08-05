import { X, Trash2 } from "lucide-react";
import { Card, Tag, Button } from "antd";

import { useTranslation } from "react-i18next";
import type { HistoryItem } from "@/store/useHistoryStore";

/** 格式化历史时间戳为 MM-DD HH:mm */
function formatHistoryTime(ts: number): string {
  const d = new Date(ts);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

interface AskHistorySidebarProps {
  items: HistoryItem[];
  activeHistoryId: string | null;
  onSelect: (item: HistoryItem) => void;
  onRemoveItem: (id: string) => void;
  onClearAll: () => void;
}

/** 历史对话侧边栏（AskPage 左侧） */
export default function AskHistorySidebar({
  items,
  activeHistoryId,
  onSelect,
  onRemoveItem,
  onClearAll,
}: AskHistorySidebarProps) {
  const { t } = useTranslation();
  return (
    <Card
      className="pf-glass-card pf-ask-history"
      variant="borderless"
      style={{ padding: 0, position: "sticky", top: 80 }}
    >
      {/* 标题栏 */}
      <div
        style={{
          padding: "14px 16px 10px",
          borderBottom: "1px solid rgba(232,236,241,0.6)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <span
          className="pf-serif"
          style={{ fontSize: 14, fontWeight: 600, color: "var(--pf-text-primary)" }}
        >
          <span className="pf-ask-history-heading">
            <span className="pf-ask-history-mark" aria-hidden="true" />
            {t("ask.history")}
          </span>
        </span>
        <Tag style={{ marginInlineEnd: 0, fontSize: 11 }}>{items.length}</Tag>
      </div>

      {/* 历史列表 */}
      <div
        role="list"
        style={{ maxHeight: "calc(100vh - 280px)", overflowY: "auto", padding: "8px 8px" }}
      >
        {items.length === 0 ? (
          <div className="pf-ask-history-empty"
            style={{
              textAlign: "center",
              padding: "32px 12px",
              color: "var(--pf-text-placeholder)",
              fontSize: 13,
            }}
          >
            {t("ask.noHistory")}
          </div>
        ) : (
          items.map((item) => (
            <div
              key={item.id}
              className={`pf-history-item ${activeHistoryId === item.id ? "pf-history-active" : ""}`}
              role="listitem"
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: 8,
                }}
              >
                <button
                  type="button"
                  className="pf-history-select"
                  aria-pressed={activeHistoryId === item.id}
                  onClick={() => onSelect(item)}
                  style={{
                    flex: 1,
                    minWidth: 0,
                    padding: 0,
                    border: 0,
                    background: "transparent",
                    color: "inherit",
                    textAlign: "left",
                    cursor: "pointer",
                  }}
                >
                  <div
                    style={{
                      fontSize: 13,
                      color: "var(--pf-text-secondary)",
                      lineHeight: 1.5,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.query.slice(0, 20)}
                    {item.query.length > 20 ? "…" : ""}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--pf-text-placeholder)", marginTop: 2 }}>
                    {formatHistoryTime(item.timestamp)}
                  </div>
                </button>
                <Button
                  type="text"
                  size="small"
                  className="pf-history-delete"
                  aria-label={t("ask.removeHistory", "删除此历史记录")}
                  icon={
                    <X style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }} />
                  }
                  onClick={() => onRemoveItem(item.id)}
                  style={{ flexShrink: 0, padding: 2 }}
                />
              </div>
            </div>
          ))
        )}
      </div>

      {/* 清空全部 */}
      {items.length > 0 && (
        <div style={{ padding: "8px 12px 12px", borderTop: "1px solid rgba(232,236,241,0.6)" }}>
          <Button danger block size="small" icon={<Trash2 />} onClick={onClearAll}>
            {t("ask.clearAllHistory")}
          </Button>
        </div>
      )}
    </Card>
  );
}
