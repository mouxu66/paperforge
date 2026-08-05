import { createHttpClient } from "./client";
import type { ZoteroImportResponse } from "./types";

// Zotero 接口专用实例：拉取文献库可能较慢，超时放宽至 30s
const zoteroHttp = createHttpClient({
  timeout: 30000,
  defaultMessage: "Zotero 导入失败",
});

/** 调用 /zotero/import 从 Zotero 用户库导入文献 */
export async function importFromZotero(
  userId: string,
  apiKey: string = "",
): Promise<ZoteroImportResponse> {
  const { data } = await zoteroHttp.post<ZoteroImportResponse>("/zotero/import", {
    userId,
    apiKey,
  });
  return data;
}
