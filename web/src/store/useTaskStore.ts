import { create } from "zustand";
import { API_BASE, getAuthHeaders, getApiToken } from "@/api/client";

/**
 * 通用异步任务状态 Store。
 *
 * 提供：
 * 1. submit(): 提交后台任务，返回 taskId
 * 2. subscribe(): 建立 SSE 连接，实时接收进度
 * 3. poll(): 轮询模式回退（SSE 不可用时）
 * 4. 内存缓存运行中/最近完成的任务状态
 */

export interface TaskInfo {
  id: string;
  type: string;
  status: "pending" | "running" | "completed" | "failed" | "timed_out";
  progress: number;
  progressMessage: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: string;
  createdAt: string;
  completedAt?: string;
}

interface TaskState {
  /** 当前活跃任务列表 */
  tasks: TaskInfo[];
  /** 最近完成的任务（保留最近 10 条） */
  recent: TaskInfo[];

  /** 提交新任务 */
  submit: (type: string, params: Record<string, unknown>) => Promise<string>;

  /** 通过 SSE 订阅任务进度 */
  subscribeSSE: (
    taskId: string,
    onUpdate?: (info: TaskInfo) => void,
    onDone?: (info: TaskInfo) => void,
    onError?: (info: TaskInfo) => void,
  ) => () => void;

  /** 轮询模式：查询单个任务状态 */
  poll: (taskId: string, signal?: AbortSignal) => Promise<TaskInfo | null>;

  /** 加载活跃任务列表 */
  loadActiveTasks: () => Promise<void>;
}

/** SSE 断连后轮询超时（毫秒），超过此时间自动停止轮询 */
const POLL_TIMEOUT_MS = 30_000;
/** 轮询间隔（毫秒） */
const POLL_INTERVAL_MS = 2000;

export const useTaskStore = create<TaskState>((set, get) => ({
  tasks: [],
  recent: [],

  submit: async (type, params) => {
    const resp = await fetch(`${API_BASE}/tasks`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ type, params }),
    });
    if (!resp.ok) throw new Error("提交任务失败");
    const data = await resp.json();
    // 通用任务 API 规范字段为 taskId；兼容尚未升级的后端/代理返回 task_id。
    const taskId = data.taskId ?? data.task_id;
    if (typeof taskId !== "string" || !taskId) {
      throw new Error("提交任务响应缺少 taskId");
    }

    // 乐观添加
    const newTask: TaskInfo = {
      id: taskId,
      type,
      status: "pending",
      progress: 0,
      progressMessage: "等待执行...",
      params,
      createdAt: new Date().toISOString(),
    };
    set((s) => ({ tasks: [...s.tasks, newTask] }));

    return taskId;
  },

  subscribeSSE: (taskId, onUpdate, onDone, onError) => {
    let disposed = false;
    const token = getApiToken();
    const url = token
      ? `${API_BASE}/tasks/${taskId}/stream?token=${encodeURIComponent(token)}`
      : `${API_BASE}/tasks/${taskId}/stream`;
    const eventSource = new EventSource(url);

    // 轮询相关的清理句柄（提升到外部作用域，确保 unsubscribe 可访问）
    let pollInterval: ReturnType<typeof setInterval> | null = null;
    let pollTimeout: ReturnType<typeof setTimeout> | null = null;
    let pollAbort: AbortController | null = null;
    const clearPolling = () => {
      if (pollInterval != null) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
      if (pollTimeout != null) {
        clearTimeout(pollTimeout);
        pollTimeout = null;
      }
      if (pollAbort) {
        pollAbort.abort();
        pollAbort = null;
      }
    };

    const moveTerminalTaskToRecent = (info: TaskInfo) => {
      set((state) => {
        const task = state.tasks.find((item) => item.id === taskId);
        if (!task) return state;
        const updatedTask = { ...task, ...info, type: task.type, createdAt: task.createdAt };
        return {
          tasks: state.tasks.filter((item) => item.id !== taskId),
          recent: [updatedTask, ...state.recent].slice(0, 10),
        };
      });
    };

    eventSource.onmessage = (event) => {
      if (disposed) return;
      if (event.data === "[DONE]") {
        disposed = true;
        clearPolling();
        eventSource.close();
        return;
      }
      try {
        const data = JSON.parse(event.data);
        const info: TaskInfo = {
          id: data.taskId,
          type: "",
          status: data.status,
          progress: data.progress || 0,
          progressMessage: data.progressMessage || "",
          result: data.result,
          error: data.error,
          createdAt: "",
        };

        // 更新 store
        set((s) => ({
          tasks: s.tasks.map((t) =>
            t.id === taskId ? { ...t, ...info, type: t.type, createdAt: t.createdAt } : t,
          ),
        }));

        if (info.status === "completed" || info.status === "failed" || info.status === "timed_out") {
          disposed = true;
          clearPolling();
          moveTerminalTaskToRecent(info);
          if (info.status === "completed") onDone?.(info);
          else onError?.(info);
          eventSource.close();
        } else {
          onUpdate?.(info);
        }
      } catch {
        // ignore parse errors
      }
    };

    eventSource.onerror = () => {
      if (disposed) return;
      // SSE 连接失败时回退到轮询
      eventSource.close();

      // 防止重复触发 onerror 导致多个轮询并行
      if (pollAbort) return;

      pollAbort = new AbortController();

      // 启动超时计时（可重置）：每次成功 poll 后刷新
      const startPollTimeout = () => {
        pollTimeout = setTimeout(() => {
          const currentTask = get().tasks.find((task) => task.id === taskId);
          const timeoutMessage = "状态查询暂时无响应，请稍后重试";
          // 这是客户端无法取得状态，不代表后端任务失败；保留 running，
          // 避免把网络故障误报成业务失败并触发错误/重试流程。
          clearPolling();
          set((state) => ({
            tasks: state.tasks.map((task) =>
              task.id === taskId && task.status === "running"
                ? { ...task, progressMessage: timeoutMessage, error: timeoutMessage }
                : task,
            ),
          }));
          onUpdate?.({
            id: taskId,
            type: currentTask?.type || "",
            status: "running",
            progress: currentTask?.progress || 0,
            progressMessage: "状态查询暂时无响应，请稍后重试",
            params: currentTask?.params,
            result: currentTask?.result,
            error: timeoutMessage,
            createdAt: currentTask?.createdAt || "",
          });
        }, POLL_TIMEOUT_MS);
      };
      startPollTimeout();

      pollInterval = setInterval(async () => {
        if (disposed || !pollAbort) {
          clearPolling();
          return;
        }
        try {
          const activePoll = pollAbort;
          const info = await get().poll(taskId, activePoll.signal);
          if (disposed || pollAbort !== activePoll) return;
          if (info) {
            // 每次成功 poll 后刷新超时计时，避免长时间运行的任务被误判超时
            if (pollTimeout) clearTimeout(pollTimeout);
            startPollTimeout();
            onUpdate?.(info);
            if (info.status === "completed" || info.status === "failed" || info.status === "timed_out") {
              disposed = true;
              clearPolling();
              moveTerminalTaskToRecent(info);
              if (info.status === "completed") onDone?.(info);
              else onError?.(info);
            }
          }
        } catch {
          // 被 abort 或网络错误 → 停止轮询
          clearPolling();
        }
      }, POLL_INTERVAL_MS);
    };

    // 返回取消函数：清理 EventSource + 轮询定时器 + 超时定时器
    return () => {
      disposed = true;
      eventSource.close();
      clearPolling();
    };
  },

  poll: async (taskId, signal) => {
    try {
      const resp = await fetch(`${API_BASE}/tasks/${taskId}`, {
        signal,
        headers: getAuthHeaders(),
      });
      if (!resp.ok) return null;
      const data = await resp.json();
      const info: TaskInfo = {
        id: data.id,
        type: data.type,
        status: data.status,
        progress: data.progress,
        progressMessage: data.progressMessage || "",
        params: data.params,
        result: data.result,
        error: data.error,
        createdAt: data.createdAt || "",
        completedAt: data.completedAt,
      };

      if (signal?.aborted) return null;
      set((s) => ({
        tasks: s.tasks.map((t) => (t.id === taskId ? { ...t, ...info } : t)),
      }));

      return info;
    } catch {
      return null;
    }
  },

  loadActiveTasks: async () => {
    const headers = getAuthHeaders();
    const fetchByStatus = async (status: "pending" | "running"): Promise<TaskInfo[] | null> => {
      try {
        const resp = await fetch(`${API_BASE}/tasks?status=${status}&limit=100`, { headers });
        if (!resp.ok) return null;
        const data = await resp.json();
        return (data.items || []) as TaskInfo[];
      } catch {
        return null;
      }
    };

    const [pendingTasks, runningTasks] = await Promise.all([
      fetchByStatus("pending"),
      fetchByStatus("running"),
    ]);
    if (pendingTasks === null && runningTasks === null) return;

    const activeTasks = new Map(
      get()
        .tasks.filter((task) => task.status === "pending" || task.status === "running")
        .map((task) => [task.id, task]),
    );
    for (const [status, fetched] of [
      ["pending", pendingTasks],
      ["running", runningTasks],
    ] as const) {
      if (fetched === null) continue;
      for (const [id, task] of activeTasks) {
        if (task.status === status) activeTasks.delete(id);
      }
      for (const task of fetched) {
        if (task.status === "pending" || task.status === "running") {
          activeTasks.set(task.id, task);
        }
      }
    }
    set({ tasks: [...activeTasks.values()] });
  },
}));
