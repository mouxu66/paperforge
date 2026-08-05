import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/**
 * Reflection 报告上传/评审历史 Store（zustand + persist）。
 *
 * 与 useDepthStore 对称设计：
 * - addUpload: 提交报告后立即添加乐观条目
 * - updateStatus: SSE 推送进度时更新状态
 * - clearCompleted: 清空已完成/失败条目
 *
 * 用途：让用户关闭 modal/页面后回来，仍能看到最近 10 条报告的评审状态。
 * 持久化：localStorage('reflection-uploads')，24h 内有效。
 */
export interface ReflectionUpload {
  /** 后端返回的 paper_id */
  paperId: string;
  /** 报告标题（用户输入的） */
  title: string;
  /** 后端 taskId（订阅 SSE 用） */
  taskId: string | null;
  /** 当前状态 */
  status: "pending" | "running" | "completed" | "failed" | "timed_out";
  /** 错误信息（失败时填充） */
  error?: string;
  /** 提交时间（ms） */
  submittedAt: number;
  /** 完成时间（ms） */
  completedAt?: number;
}

interface ReflectionState {
  /** 所有 reflection 上传记录 */
  uploads: ReflectionUpload[];

  /** 新增一条记录（提交后立即调用） */
  addUpload: (paperId: string, title: string, taskId: string | null) => void;
  /** 更新状态（SSE 推送时调用） */
  updateStatus: (paperId: string, status: ReflectionUpload["status"], error?: string) => void;
  /** 通过 taskId 更新（处理后端在创建时返回 taskId 立即订阅的场景） */
  updateByTaskId: (taskId: string, status: ReflectionUpload["status"], error?: string) => void;
  /** 移除单条 */
  removeUpload: (paperId: string) => void;
  /** 清空已完成/失败 */
  clearCompleted: () => void;
}

export const useReflectionStore = create<ReflectionState>()(
  persist(
    (set) => ({
      uploads: [],

      addUpload: (paperId, title, taskId) => {
        set((state) => {
          // 【修复】显式锁定 status 为字面量联合，避免 TS 拓宽为 string 导致 set() 返回类型不兼容
          const newUpload: ReflectionUpload = {
            paperId,
            title,
            taskId,
            status: "pending",
            submittedAt: Date.now(),
          };
          return {
            uploads: [newUpload, ...state.uploads].slice(0, 20), // 内存上限 20 条
          };
        });
      },

      updateStatus: (paperId: string, status: ReflectionUpload["status"], error?: string) => {
        set((state) => ({
          uploads: state.uploads.map((u) =>
            u.paperId === paperId
              ? {
                  ...u,
                  status,
                  error,
                  completedAt:
                    status === "completed" || status === "failed" || status === "timed_out" ? Date.now() : u.completedAt,
                }
              : u,
          ),
        }));
      },

      updateByTaskId: (taskId: string, status: ReflectionUpload["status"], error?: string) => {
        set((state) => ({
          uploads: state.uploads.map((u) =>
            u.taskId === taskId
              ? {
                  ...u,
                  status,
                  error,
                  completedAt:
                    status === "completed" || status === "failed" || status === "timed_out" ? Date.now() : u.completedAt,
                }
              : u,
          ),
        }));
      },

      removeUpload: (paperId) => {
        set((state) => ({
          uploads: state.uploads.filter((u) => u.paperId !== paperId),
        }));
      },

      clearCompleted: () => {
        set((state) => ({
          uploads: state.uploads.filter((u) => u.status === "pending" || u.status === "running"),
        }));
      },
    }),
    {
      name: "reflection-uploads",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        // 持久化时仅保留 24h 内的记录
        uploads: state.uploads.filter(
          (u) =>
            u.status === "pending" ||
            u.status === "running" ||
            Date.now() - u.submittedAt < 24 * 60 * 60 * 1000,
        ),
      }),
    },
  ),
);
