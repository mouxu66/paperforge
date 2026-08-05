import { createHttpClient } from "./client";
import type { ArxivPaperPreview, ArxivImportResponse } from "./types";

// arXiv 接口专用实例：搜索可能较慢，超时放宽至 30s
const arxivHttp = createHttpClient({
  timeout: 30000,
  defaultMessage: "arXiv 搜索失败",
});

/** 调用 /arxiv/search 按关键词检索 arXiv 论文（不入库，仅预览） */
export async function searchArxiv(keyword: string, maxResults = 10): Promise<ArxivPaperPreview[]> {
  const { data } = await arxivHttp.post<{ items: ArxivPaperPreview[]; total: number }>(
    "/arxiv/search",
    { keyword, maxResults },
  );
  return data.items ?? [];
}

/** 调用 /arxiv/import 将选中的 arXiv 论文批量入库 */
export async function importArxivPapers(papers: ArxivPaperPreview[]): Promise<ArxivImportResponse> {
  const { data } = await arxivHttp.post<ArxivImportResponse>("/arxiv/import", { papers });
  return data;
}
