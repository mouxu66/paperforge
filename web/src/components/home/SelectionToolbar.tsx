import { CheckSquare, X, Trash2, Radar, Tags, Zap } from "lucide-react";
import { Button } from "antd";
import { useTranslation } from "react-i18next";

import type { Paper } from "@/api/types";

interface SelectionToolbarProps {
  selectedIds: Set<string>;
  items: Paper[];
  onToggleSelectAll: () => void;
  onExitSelectMode: () => void;
  onV4Review: () => void;
  onDepthEval: () => void;
  onBatchTag: () => void;
  onDelete: () => void;
  v4Loading?: boolean;
  depthLoading?: boolean;
  /** 当前是否为感悟报告视图，用于隐藏论文专属批量操作 */
  isReportView?: boolean;
}

export default function SelectionToolbar({
  selectedIds,
  items,
  onToggleSelectAll,
  onExitSelectMode,
  onV4Review,
  onDepthEval,
  onBatchTag,
  onDelete,
  v4Loading,
  depthLoading,
  isReportView,
}: SelectionToolbarProps) {
  const { t } = useTranslation();
  const isAllSelected = items.length > 0 && selectedIds.size === items.length;

  return (
    <div
      style={{
        marginBottom: 12,
        padding: "8px 16px",
        background: "var(--pf-primary-soft)",
        borderRadius: 8,
        border: "1px solid var(--pf-border-light)",
        display: "flex",
        alignItems: "center",
        gap: 12,
      }}
    >
      <Button
        size="small"
        icon={<CheckSquare />}
        type={isAllSelected ? "primary" : "default"}
        onClick={onToggleSelectAll}
      >
        {isAllSelected
          ? t("selection.deselectAll", "取消全选")
          : t("selection.selectAll", "全选")}
      </Button>
      <span style={{ fontSize: 14, color: "var(--pf-primary)" }}>
        {t("common.selected", { count: selectedIds.size })}
      </span>
      <div style={{ flex: 1 }} />      {!isReportView && selectedIds.size >= 1 && (
        <Button
          type="primary"
          size="small"
          icon={<Zap />}
          loading={v4Loading}
          onClick={onV4Review}
        >
          {t("selection.v4Review", "V4.1 深度审稿（{{count}} 篇）", { count: selectedIds.size })}
        </Button>
      )}

      {!isReportView && selectedIds.size >= 1 && (
        <Button
          size="small"
          icon={<Radar />}
          loading={depthLoading}
          onClick={onDepthEval}
        >
          {t("selection.depthEval", "DEPTH 评估（{{count}} 篇）", { count: selectedIds.size })}
        </Button>
      )}

      {selectedIds.size >= 1 && (
        <Button size="small" icon={<Tags />} onClick={onBatchTag}>
          {t("selection.batchTag", "批量打标签（{{count}} 篇）", { count: selectedIds.size })}
        </Button>
      )}

      {selectedIds.size >= 1 && (
        <Button danger size="small" icon={<Trash2 />} onClick={onDelete}>
          {t("selection.deleteSelected", "删除选中")}
        </Button>
      )}

      <Button type="text" size="small" icon={<X />} onClick={onExitSelectMode}>
        {t("selection.cancelSelection", "取消选择")}
      </Button>
    </div>
  );
}
