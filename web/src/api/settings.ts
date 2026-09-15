/**
 * 全局设置 API 客户端（双模型交叉复核 / 被引情感云端复核 开关）。
 *
 * 后端：mock_api/routers/settings.py
 * - GET  /api/settings  读取当前生效设置
 * - PUT  /api/settings  在线改写（运行时生效，重启后恢复默认）
 */
import http from "./client";
import type { AppSettings } from "./types";

export async function getSettings(): Promise<AppSettings> {
  const resp = await http.get<AppSettings>("/settings");
  return resp.data;
}

export async function updateSettings(settings: Partial<AppSettings>): Promise<AppSettings> {
  const resp = await http.put<AppSettings>("/settings", settings);
  return resp.data;
}
