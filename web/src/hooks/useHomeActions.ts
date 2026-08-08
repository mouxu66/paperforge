import { useCallback, useState } from "react";
import { App } from "antd";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { invalidatePaperQueryCache } from "@/store/usePaperStore";

import { batchDeletePapers } from "@/api/papers";
import { submitDepthBatch, startSelectedV4Review } from "@/api/depth";
import type { useSelection } from "./useSelection";

/**
 * HomePage 的 3 个批量动作 handler + 各自 loading 状态（G1 拆分 · 从 HomePage 抽出）。
 *
 * 文案统一走 home.actions.* i18n key（G5 护栏），不再有硬编码中文。
 *
 * @param selection useSelection 返回值（提供 selectedIds / exitSelectMode）
 * @param addTask   useDepthStore 的 addTask（提交 DEPTH 后注册任务）
 * @param loadPapers  usePaperStore 的 loadPapers（删除成功后刷新列表）
 */
export function useHomeActions(
  selection: ReturnType<typeof useSelection>,
  addTask: (taskId: string, totalPapers: number, paperIds?: string[]) => void,
  loadPapers: () => Promise<void>,
) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { selectedIds, exitSelectMode } = selection;

  const [deleteLoading, setDeleteLoading] = useState(false);
  const [depthLoading, setDepthLoading] = useState(false);
  const [v4SelectedLoading, setV4SelectedLoading] = useState(false);

  /** 批量删除选中论文 */
  const handleBatchDelete = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setDeleteLoading(true);
    try {
      const result = await batchDeletePapers({ paper_ids: ids });
      if (result.failed_ids.length > 0) {
        message.warning(
          t("home.actions.batchDeletePartial", {
            deleted: result.deleted_count,
            failed: result.failed_ids.length,
          }),
        );
      } else {
        message.success(t("home.actions.batchDeleteSuccess", { count: result.deleted_count }));
      }
      exitSelectMode();
      invalidatePaperQueryCache();
      loadPapers();
    } catch {
      message.error(t("home.actions.batchDeleteError"));
    } finally {
      setDeleteLoading(false);
    }
  }, [selectedIds, exitSelectMode, loadPapers, t, message]);

  /** 提交选中论文到 DEPTH 多维评分 */
  const handleDepthSubmit = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setDepthLoading(true);
    try {
      const { task_id, total_papers } = await submitDepthBatch(ids);
      addTask(task_id, total_papers, ids);
      message.success(t("home.actions.depthSubmitSuccess", { count: total_papers }));
      navigate("/depth", { state: { taskId: task_id, selectedIds: ids } });
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : t("home.actions.depthSubmitError");
      message.error(msg);
    } finally {
      setDepthLoading(false);
    }
  }, [selectedIds, addTask, navigate, t, message]);

  /** 提交选中论文进行 V4.1 深度审稿 */
  const handleV4Review = useCallback(async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    setV4SelectedLoading(true);
    try {
      const res = await startSelectedV4Review(ids);
      if (res.skipped.length > 0) {
        const reasons = res.skipped
          .slice(0, 3)
          .map((s) => s.reason)
          .join("、");
        message.warning(
          t("home.actions.v4ReviewPartial", {
            submitted: res.submitted,
            skipped: res.skipped.length,
            reasons,
            etc: res.skipped.length > 3 ? t("common.etc", "等") : "",
          }),
          5,
        );
      } else {
        message.success(t("home.actions.v4ReviewSuccess", { count: res.submitted }));
      }
      exitSelectMode();
    } catch (e: unknown) {
      const detail =
        e && typeof e === "object" && "response" in e
          ? (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined;
      message.error(detail || t("home.actions.v4ReviewError"));
    } finally {
      setV4SelectedLoading(false);
    }
  }, [selectedIds, exitSelectMode, t, message]);

  return {
    handleBatchDelete,
    handleDepthSubmit,
    handleV4Review,
    deleteLoading,
    depthLoading,
    v4SelectedLoading,
  };
}
