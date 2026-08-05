import { createHttpClient } from "./client";
import type { GenerateResponse } from "./types";

// 综述生成专用实例：检索+长文本生成，超时放宽至 3 分钟
const generateHttp = createHttpClient({
  timeout: 180000,
  defaultMessage: "生成综述失败",
});

/** 调用 /generate 接口生成综述初稿 */
export async function fetchGenerate(topic: string): Promise<GenerateResponse> {
  const { data } = await generateHttp.post<GenerateResponse>("/generate", { topic });
  return data;
}
