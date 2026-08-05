import { httpSilent } from "./client";

export interface QwenStatusEvent {
  kind: "ready" | "loading" | "switching" | "idle";
  eta_seconds: number;
  state:
    | "idle"
    | "text_active"
    | "vision_active"
    | "switching_to_text"
    // 兼容旧后端状态值
    | "qwen_active"
    | "ocr_active";
}

export interface QwenStatus {
  managed: boolean;
  ready: boolean;
  vram_exclusive: boolean;
  event: QwenStatusEvent;
}

/** 查询 Qwen/llama-server 托管状态 + 最近切换事件（供「模型加载中」提示轮询）。 */
export async function getQwenStatus(): Promise<QwenStatus> {
  const { data } = await httpSilent.get<QwenStatus>("/qwen/status");
  return data;
}

/** 单条显存调度事件。 */
export interface VramEvent {
  id: string;
  kind: string;
  message: string;
  ts: number;
}

/** 获取最近的显存调度事件列表（供设置页「VRAM 事件」面板）。 */
export async function getQwenEvents(limit: number): Promise<{ events: VramEvent[] }> {
  const { data } = await httpSilent.get<{ events: VramEvent[] }>(`/qwen/events?limit=${limit}`);
  return data;
}

/** 清空显存调度事件记录。 */
export async function clearQwenEvents(): Promise<void> {
  await httpSilent.post("/qwen/events/clear");
}
