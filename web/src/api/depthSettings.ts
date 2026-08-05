import http from "./client";
import type { DepthSettings } from "./types";

export async function getDepthSettings(): Promise<DepthSettings> {
  const resp = await http.get<DepthSettings>("/depth/settings");
  return resp.data;
}

function camelToSnakeCase(str: string): string {
  return str.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

export async function updateDepthSettings(
  settings: Partial<DepthSettings>
): Promise<DepthSettings> {
  const snakeCase: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(settings)) {
    snakeCase[camelToSnakeCase(key)] = value;
  }
  const resp = await http.put<DepthSettings>("/depth/settings", snakeCase);
  return resp.data;
}
