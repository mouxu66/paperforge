import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { DepthScore } from "@/api/types";

/** 单个 DEPTH 评估任务的状态 */
export interface DepthTask {
  /** 后端返回的任务 ID */
  taskId: string;
  /** 本次评估的论文 ID，用于失败重试 */
  paperIds: string[];
  /** 任务状态；canceled 表示前端停止追踪（后端 worker 可能仍在运行） */
  status: "running" | "completed" | "failed" | "canceled";
  /** 进度 */
  progress: { current: number; total: number };
  /** 评估结果（完成后填充） */
  results: DepthScore[] | null;
  /** 汇总信息（完成后填充） */
  summary: {
    total: number;
    completed: number;
    failed: number;
    highest_score: number;
    average_score: number;
  } | null;
  /** 错误列表 */
  errors: string[];
  /** 提交时间（ms） */
  submittedAt: number;
}

interface DepthState {
  /** 所有评估任务 */
  tasks: DepthTask[];

  /** 新增一个运行中的任务 */
  addTask: (taskId: string, totalPapers: number, paperIds?: string[]) => void;
  /** 更新任务进度 */
  updateProgress: (taskId: string, current: number, total: number) => void;
  /** 标记任务完成 */
  completeTask: (taskId: string, results: DepthScore[], summary: DepthTask["summary"]) => void;
  /** 标记任务失败 */
  failTask: (taskId: string, errors: string[]) => void;
  /** 批量标记任务失败（单次渲染，避免循环中多次 set） */
  failTasks: (items: Array<{ taskId: string; errors: string[] }>) => void;
  /** 移除单个任务 */
  removeTask: (taskId: string) => void;
  /** 标记任务为已取消（仅停止前端追踪，不代表后端 worker 已终止） */
  cancelTask: (taskId: string) => void;
  /** 清除所有已完成/失败/已取消的任务，保留运行中的任务 */
  clearCompleted: () => void;
}

const VALID_STATUSES = new Set<DepthTask["status"]>([
  "running",
  "completed",
  "failed",
  "canceled",
]);

/** 将旧版本 localStorage 中的任务安全归一化为当前契约。 */
function normalizePersistedTask(raw: unknown): DepthTask | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as Partial<DepthTask>;
  if (typeof value.taskId !== "string" || value.taskId.length === 0) return null;

  const status = VALID_STATUSES.has(value.status as DepthTask["status"])
    ? (value.status as DepthTask["status"])
    : "failed";
  const progress = value.progress && typeof value.progress === "object" ? value.progress : {};
  const progressValue = progress as { current?: unknown; total?: unknown };
  const current =
    typeof progressValue.current === "number" && Number.isFinite(progressValue.current)
      ? Math.max(0, progressValue.current)
      : 0;
  const total =
    typeof progressValue.total === "number" && Number.isFinite(progressValue.total)
      ? Math.max(0, progressValue.total)
      : 0;
  const submittedAt =
    typeof value.submittedAt === "number" && Number.isFinite(value.submittedAt)
      ? value.submittedAt
      : Date.now();
  const rawSummary = value.summary;
  const summary =
    rawSummary && typeof rawSummary === "object"
      ? (rawSummary as Record<string, unknown>)
      : null;
  const hasValidSummary =
    summary !== null &&
    ["total", "completed", "failed", "highest_score", "average_score"].every(
      (key) => typeof summary[key] === "number" && Number.isFinite(summary[key] as number),
    );

  return {
    taskId: value.taskId,
    paperIds: Array.isArray(value.paperIds)
      ? value.paperIds.filter((id): id is string => typeof id === "string")
      : [],
    status,
    progress: { current, total },
    results: Array.isArray(value.results)
      ? value.results.filter((item): item is DepthScore => Boolean(item) && typeof item === "object")
      : null,
    summary: hasValidSummary
      ? {
          total: summary.total as number,
          completed: summary.completed as number,
          failed: summary.failed as number,
          highest_score: summary.highest_score as number,
          average_score: summary.average_score as number,
        }
      : null,
    errors: Array.isArray(value.errors)
      ? value.errors.filter((error): error is string => typeof error === "string")
      : [],
    submittedAt,
  };
}

export const useDepthStore = create<DepthState>()(
  persist(
    (set) => ({
      tasks: [],

      addTask: (taskId, totalPapers, paperIds = []) => {
        set((state) => ({
          tasks: [
            ...state.tasks,
            {
              taskId,
              paperIds: [...paperIds],
              status: "running",
              progress: { current: 0, total: totalPapers },
              results: null,
              summary: null,
              errors: [],
              submittedAt: Date.now(),
            },
          ],
        }));
      },

      updateProgress: (taskId, current, total) => {
        set((state) => ({
          tasks: state.tasks.map((t) =>
            t.taskId === taskId && t.status === "running"
              ? { ...t, progress: { current, total } }
              : t,
          ),
        }));
      },

      completeTask: (taskId, results, summary) => {
        set((state) => ({
          tasks: state.tasks.map((t) =>
            t.taskId === taskId && t.status === "running"
              ? { ...t, status: "completed", results, summary, errors: [] }
              : t,
          ),
        }));
      },

      failTask: (taskId, errors) => {
        set((state) => ({
          tasks: state.tasks.map((t) =>
            t.taskId === taskId && t.status === "running" ? { ...t, status: "failed", errors } : t,
          ),
        }));
      },

      failTasks: (items) => {
        if (items.length === 0) return;
        const failedIds = new Set(items.map((i) => i.taskId));
        const errorMap = new Map(items.map((i) => [i.taskId, i.errors]));
        set((state) => ({
          tasks: state.tasks.map((t) =>
            failedIds.has(t.taskId) && t.status === "running"
              ? { ...t, status: "failed" as const, errors: errorMap.get(t.taskId) || [] }
              : t,
          ),
        }));
      },

      removeTask: (taskId) => {
        set((state) => ({
          tasks: state.tasks.filter((t) => t.taskId !== taskId),
        }));
      },

      cancelTask: (taskId) => {
        set((state) => ({
          tasks: state.tasks.map((t) =>
            t.taskId === taskId && t.status === "running"
              ? { ...t, status: "canceled" as const, errors: [] }
              : t,
          ),
        }));
      },

      clearCompleted: () => {
        set((state) => ({
          tasks: state.tasks.filter((t) => t.status === "running"),
        }));
      },
    }),
    {
      name: "depth-tasks",
      storage: createJSONStorage(() => localStorage),
      version: 1,
      migrate: (persistedState: unknown) => {
        const state = persistedState as { tasks?: unknown } | null;
        return {
          tasks: Array.isArray(state?.tasks)
            ? state.tasks.map(normalizePersistedTask).filter((task): task is DepthTask => task !== null)
            : [],
        };
      },
      // 持久化时仅保留任务列表；任务时间超过 24h 的自动清除
      // JSDOM tests should control hydration explicitly; automatic async
      // rehydration otherwise updates subscribed components outside act().
      // Production still hydrates from App's startup effect below.
      skipHydration: import.meta.env.MODE === "test",
      partialize: (state) => ({
        tasks: state.tasks.filter(
          (t) => t.status === "running" || Date.now() - t.submittedAt < 24 * 60 * 60 * 1000,
        ),
      }),
    },
  ),
);
