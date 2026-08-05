import { useCallback, useMemo, useState } from "react";
import type { Paper } from "@/api/types";

/**
 * HomePage 选择模式状态 + 派生值 + 操作方法（G1 拆分 · 从 HomePage 抽出）。
 *
 * 职责：
 * - selectMode 开关 + selectedIds Set
 * - 单选 toggle / 全选 / 取消全选 / 退出选择模式
 * - 派生：selectedPapers（跨页匹配，未在当前页的显示 ID 兜底）
 *         estimatedMinutes（每篇 ~30s，5 轮 LLM 评估）
 *         isAllSelected（当前页是否全选）
 *         toggleSelectMode（进入/退出选择模式的便捷切换）
 *
 * @param items 当前页论文列表（来自 usePaperStore.items）
 */
export function useSelection(items: Paper[]) {
  const [selectMode, setSelectMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  /** 退出选择模式：同时清空已选 */
  const exitSelectMode = useCallback(() => {
    setSelectMode(false);
    setSelectedIds(new Set());
  }, []);

  /** 切换单篇选中状态 */
  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  /** 全选 / 取消全选当前页 */
  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      if (prev.size === items.length) return new Set();
      return new Set(items.map((p) => p.id));
    });
  }, [items]);

  /** 进入 / 退出选择模式的便捷切换 */
  const toggleSelectMode = useCallback(() => {
    if (selectMode) {
      setSelectMode(false);
      setSelectedIds(new Set());
    } else {
      setSelectMode(true);
    }
  }, [selectMode]);

  /** 已选论文列表（跨页匹配：未在当前页的显示 ID 兜底） */
  const selectedPapers = useMemo(() => {
    const paperMap = new Map(items.map((p) => [p.id, p]));
    return Array.from(selectedIds).map(
      (id) => paperMap.get(id) ?? ({ id, title: id, authors: [], year: 0 } as unknown as Paper),
    );
  }, [items, selectedIds]);

  /** 预计耗时（分钟）：每篇约 30 秒（5 轮 LLM 调用），至少 1 分钟 */
  const estimatedMinutes = Math.max(1, Math.ceil((selectedIds.size * 30) / 60));

  /** 当前页是否全选 */
  const isAllSelected = items.length > 0 && selectedIds.size === items.length;

  return {
    selectMode,
    selectedIds,
    selectedPapers,
    estimatedMinutes,
    isAllSelected,
    toggleSelect,
    toggleSelectAll,
    exitSelectMode,
    toggleSelectMode,
  };
}
