import http, { createHttpClient } from "./client";
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  BatchDeleteRequest,
  ComparePapersRequest,
  ComparePapersResponse,
  BatchDeleteResponse,
  BatchTagRequest,
  BatchTagResponse,
  CitationSentimentStats,
  DuplicateGroupsResponse,
  HybridSearchResponse,
  LibraryStats,
  MergePapersRequest,
  MergePapersResponse,
  PageResult,
  Paper,
  PaperFigure,
  PaperQuery,
  RankRequest,
  RankResponse,
  RelationGraph,
  RenameTagRequest,
  SuggestItem,
  TagInfo,
  TagMutationResponse,
  TranslateResponse,
  TranslationHistoryCreate,
  TranslationHistoryItem,
  TranslationHistoryList,
} from "./types";

// 语义搜索专用实例：30s 超时，不挂载拦截器（调用方自行 try/catch 降级到 FTS5）
const pfHttp = createHttpClient({ timeout: 30000, withInterceptor: false });

/** 分页查询论文（支持关键词过滤、分类、排序） */
export async function fetchPapers(q: PaperQuery): Promise<PageResult<Paper>> {
  const { data } = await http.get<PageResult<Paper>>("/papers", { params: q });
  return data;
}

/** 论文详情 */
export async function fetchPaperById(id: string): Promise<Paper | null> {
  const { data } = await http.get<Paper | null>(`/papers/${id}`);
  return data;
}

/** 收藏列表（后端返回完整 Paper[]） */
export async function fetchFavorites(): Promise<Paper[]> {
  const { data } = await http.get<Paper[]>("/favorites");
  return data;
}

/** 添加收藏（持久化到后端 SQLite） */
export async function addFavorite(paperId: string): Promise<void> {
  await http.post("/favorites", { paper_id: paperId });
}

/** 取消收藏 */
export async function removeFavorite(paperId: string): Promise<void> {
  await http.delete(`/favorites/${paperId}`);
}

/** 搜索建议（按标题模糊匹配，返回前 5 条 {id, title}） */
export async function fetchSuggest(q: string): Promise<SuggestItem[]> {
  const { data } = await http.get<SuggestItem[]>("/search/suggest", { params: { q } });
  return data;
}

/** 库统计 */
export async function fetchStats(): Promise<LibraryStats> {
  const { data } = await http.get<LibraryStats>("/stats");
  return data;
}

/**
 * 语义搜索（调用 /api/search/semantic）。
 * 返回格式与 fetchPapers 完全一致，可直接复用 PaperList 渲染。
 * 调用方应 try/catch 并在失败时降级为 fetchPapers（FTS5 关键词搜索）。
 */
export async function fetchSemanticSearch(keyword: string): Promise<Paper[]> {
  const { data } = await pfHttp.post<Paper[]>("/search/semantic", { question: keyword });
  return data;
}

/**
 * 论文综合评分与排序。
 * POST /api/papers/rank
 */
export async function fetchPaperRanking(req: RankRequest): Promise<RankResponse> {
  const { data } = await pfHttp.post<RankResponse>("/papers/rank", req);
  return data;
}

/**
 * 指定论文对比分析。
 * POST /api/papers/analyze
 */
export async function fetchAnalyzePapers(req: AnalyzeRequest): Promise<AnalyzeResponse> {
  const { data } = await pfHttp.post<AnalyzeResponse>("/papers/analyze", req);
  return data;
}

/**
 * 跨文档对比表：结构化抽取方法/样本量/主要结果等。
 * POST /api/papers/compare
 */
export async function fetchComparePapers(
  req: ComparePapersRequest,
): Promise<ComparePapersResponse> {
  const { data } = await pfHttp.post<ComparePapersResponse>("/papers/compare", req);
  return data;
}

/**
 * 批量删除论文。
 * POST /api/papers/batch-delete
 */
export async function batchDeletePapers(req: BatchDeleteRequest): Promise<BatchDeleteResponse> {
  const { data } = await http.post<BatchDeleteResponse>("/papers/batch-delete", req);
  return data;
}

/**
 * 论文 + figure 混合检索。
 * POST /api/search/hybrid
 */
export async function fetchHybridSearch(query: string, topK = 24): Promise<HybridSearchResponse> {
  const { data } = await pfHttp.post<HybridSearchResponse>("/search/hybrid", {
    query,
    top_k: topK,
  });
  return data;
}

/**
 * 扫描全库，返回所有疑似重复论文组。
 * GET /api/papers/duplicates
 */
export async function fetchDuplicateGroups(): Promise<DuplicateGroupsResponse> {
  const { data } = await http.get<DuplicateGroupsResponse>("/papers/duplicates");
  return data;
}

/**
 * 合并重复论文。
 * POST /api/papers/merge
 */
export async function mergePapers(req: MergePapersRequest): Promise<MergePapersResponse> {
  const { data } = await http.post<MergePapersResponse>("/papers/merge", req);
  return data;
}

/**
 * 触发单篇论文的元数据补全（Semantic Scholar 富化）。
 * POST /api/papers/{id}/enrich-metadata
 */
export async function enrichPaperMetadata(id: string): Promise<Paper> {
  const { data } = await http.post<Paper>(`/papers/${id}/enrich-metadata`);
  return data;
}

/**
 * 预览单篇论文的元数据补全结果（不写入数据库）。
 * POST /api/papers/{id}/enrich-metadata/preview?dry_run=true
 */
export async function previewEnrichPaperMetadata(id: string): Promise<{
  original: Paper;
  enriched: Paper;
}> {
  const { data } = await http.post<{ original: Paper; enriched: Paper }>(
    `/papers/${id}/enrich-metadata/preview?dry_run=true`,
  );
  return data;
}

/**
 * 从论文全文、PDF URL 或标题中提取 DOI 并保存。
 * POST /api/papers/{id}/extract-doi
 */
export async function extractPaperDoi(id: string): Promise<Paper> {
  const { data } = await http.post<Paper>(`/papers/${id}/extract-doi`);
  return data;
}

/**
 * 将本地 PDF 文件重命名为 '{year} - {title}.pdf' 格式。
 * POST /api/papers/{id}/rename-pdf
 */
export async function renamePaperPdf(id: string): Promise<Paper> {
  const { data } = await http.post<Paper>(`/papers/${id}/rename-pdf`);
  return data;
}

/**
 * 从本地 PDF 中提取批注/高亮数量。
 * POST /api/papers/{id}/extract-annotations
 */
export async function extractPaperAnnotations(id: string): Promise<{ count: number }> {
  const { data } = await http.post<{ count: number }>(`/papers/${id}/extract-annotations`);
  return data;
}

/**
 * 翻译选中的 PDF 文本（WP-2.7）。
 * POST /api/papers/{id}/translate
 */
export async function translatePaperText(
  id: string,
  text: string,
  targetLanguage = "zh-CN",
): Promise<TranslateResponse> {
  const { data } = await http.post<TranslateResponse>(`/papers/${id}/translate`, {
    text,
    target_language: targetLanguage,
  });
  return data;
}

/**
 * 获取某篇论文的所有 figure 详情（含 axis_info / source_text_span / claim_validation）。
 * GET /api/papers/{id}/figures
 */
export async function fetchPaperFigures(id: string): Promise<PaperFigure[]> {
  const { data } = await http.get<PaperFigure[]>(`/papers/${id}/figures`);
  return data;
}

/**
 * 获取某篇论文的 PDF 翻译历史。
 * GET /api/papers/{id}/translation-history
 */
export async function fetchTranslationHistory(id: string): Promise<TranslationHistoryItem[]> {
  const { data } = await http.get<TranslationHistoryList>(`/papers/${id}/translation-history`);
  return data.items;
}

/**
 * 保存一条 PDF 翻译记录。
 * POST /api/papers/{id}/translation-history
 */
export async function saveTranslationHistory(
  id: string,
  req: TranslationHistoryCreate,
): Promise<{ id: string; success: boolean }> {
  const { data } = await http.post<{ id: string; success: boolean }>(
    `/papers/${id}/translation-history`,
    req,
  );
  return data;
}

/**
 * 删除一条 PDF 翻译记录。
 * DELETE /api/papers/{id}/translation-history/{historyId}
 */
export async function deleteTranslationHistory(id: string, historyId: string): Promise<void> {
  await http.delete(`/papers/${id}/translation-history/${historyId}`);
}

// ---------------------------------------------------------------------------
// 引用情感 + 关系图（WP-2.2）
// ---------------------------------------------------------------------------

/**
 * 获取单篇论文的引用情感统计（被引情感）。
 * GET /api/papers/{id}/sentiment
 */
export async function fetchPaperSentiment(id: string): Promise<CitationSentimentStats> {
  const { data } = await http.get<CitationSentimentStats>(`/papers/${id}/sentiment`);
  return data;
}

/**
 * 触发后台任务抽取单篇论文的被引情感。
 * POST /api/papers/{id}/extract-citation-sentiment
 */
export async function extractCitationSentiment(id: string): Promise<{
  taskId: string;
  paperId: string;
  status: string;
}> {
  const { data } = await http.post<{ taskId: string; paperId: string; status: string }>(
    `/papers/${id}/extract-citation-sentiment`,
  );
  return data;
}

/**
 * 获取论文关系图（中心论文 + 引用它的论文 + 被引情感边）。
 * GET /api/papers/{id}/relation-graph
 */
export async function fetchRelationGraph(id: string): Promise<RelationGraph> {
  const { data } = await http.get<RelationGraph>(`/papers/${id}/relation-graph`);
  return data;
}

// ---------------------------------------------------------------------------
// 批量标签管理（WP-2.1）
// ---------------------------------------------------------------------------

/** 批量给多篇论文增/删标签。POST /api/papers/batch-tag */
export async function batchTagPapers(req: BatchTagRequest): Promise<BatchTagResponse> {
  const { data } = await http.post<BatchTagResponse>("/papers/batch-tag", req);
  return data;
}

/** 聚合全库标签及计数。GET /api/papers/tags */
export async function fetchAllTags(): Promise<TagInfo[]> {
  const { data } = await http.get<TagInfo[]>("/papers/tags");
  return data;
}

/** 全库重命名标签。POST /api/papers/tags/rename */
export async function renameTag(req: RenameTagRequest): Promise<TagMutationResponse> {
  const { data } = await http.post<TagMutationResponse>("/papers/tags/rename", req);
  return data;
}

/** 全库删除指定标签。DELETE /api/papers/tags/{tag} */
export async function deleteTag(name: string): Promise<TagMutationResponse> {
  const { data } = await http.delete<TagMutationResponse>(
    `/papers/tags/${encodeURIComponent(name)}`,
  );
  return data;
}
