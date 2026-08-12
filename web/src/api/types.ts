// 论文领域类型定义

export interface Paper {
  id: string; // arXiv ID
  title: string;
  authors: string[];
  year: number;
  abstract: string;
  category: string;
  tags: string[];
  citations: number;
  chunkCount: number;
  indexSize: number; // bytes
  pdfUrl: string;
  source:
    | "arxiv"
    | "pubmed"
    | "ieee"
    | "springer"
    | "upload"
    | "cnki"
    | "google_scholar"
    | "web_clipper"
    | "zotero";
  journal?: string;
  favorited?: boolean;
  // B2: Semantic Scholar 富化字段（未富化时为 null）
  influentialCitations?: number | null;
  fieldsOfStudy?: string[] | null;
  // Reflection 报告关联的原论文（仅 category='report' 时有效）
  sourcePaperId?: string | null;
  sourcePaperStatus?: string | null;
  // WP-1.2: OCR 状态与扫描件标识
  ocrStatus?: "pending" | "done" | "failed" | null;
  isScanned?: boolean | null;
}

export interface PageResult<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export type PaperSource =
  | "all"
  | "arxiv"
  | "upload"
  | "cnki"
  | "google_scholar"
  | "web_clipper"
  | "zotero"
  | "pubmed"
  | "ieee";

export interface PaperQuery {
  keyword?: string;
  category?: string;
  sort?: string;
  page?: number;
  pageSize?: number;
  source?: PaperSource; // 来源筛选
}

export interface LibraryStats {
  totalPapers: number;
  reportCount: number;
  reportAvgScore: number;
  reportAvgFidelity: number;
  totalChunks: number;
  totalSize: number;
  byCategory: { category: string; count: number }[];
  bySource: { source: string; count: number }[];
}

export interface SuggestItem {
  id: string;
  title: string;
  // FTS5 返回的 HTML 高亮片段（可能为空，为空时前端回退到 title）
  highlight?: string;
}

/** figure 级检索命中项 */
export interface UnifiedFigureHit {
  paper_id: string;
  title: string;
  page: number;
  figure_index: number;
  ocr_text?: string;
  score: number;
  fused_score: number;
  image_url: string;
}

/** 单张 figure 详情（axis_info / source_text_span / claim_validation） */
export interface PaperFigure {
  id: number;
  paperId: string;
  page: number;
  figureIndex: number;
  figureNumber: number | null;
  figurePath: string;
  ocrText: string;
  captionText: string | null;
  qwenSummary: string | null;
  source: string;
  sourceTextSpan: string | null;
  axisInfo: {
    xLabel?: string;
    yLabel?: string;
    xTicks?: number[];
    yTicks?: number[];
    legendItems?: string[];
    captionSummary?: string;
    [key: string]: unknown;
  } | null;
  claimValidation: {
    claims?: Array<{
      metric?: string;
      value?: number;
      operator?: string;
      comparator?: string;
      context?: string;
      [key: string]: unknown;
    }>;
    validated?: Array<{
      valid: boolean | null;
      metricMatched: boolean;
      reason?: string;
      [key: string]: unknown;
    }>;
  } | null;
  curvePoints: Array<{ series?: string; x?: number; y?: number }> | null;
  imageUrl?: string;
}

/** 论文 + figure 混合检索响应 */
export interface HybridSearchResponse {
  papers: Paper[];
  figures: UnifiedFigureHit[];
  fused_figures: UnifiedFigureHit[];
}

// ---------------------------------------------------------------------------
// 论文问答（PaperForge /ask 接口）
// ---------------------------------------------------------------------------
export interface AskRequest {
  question: string;
  paperIds?: string[];
}

export interface AskReference {
  id: string;
  title: string;
  authors?: string[];
  year?: number;
  /** 命中片段（可选，用于在参考文献卡片中预览） */
  snippet?: string;
}

export interface AskResponse {
  answer: string;
  references: AskReference[];
}

// ---------------------------------------------------------------------------
// 综述生成（PaperForge /generate 接口）
// ---------------------------------------------------------------------------
export interface GenerateResponse {
  /** Markdown 格式的综述正文 */
  content: string;
  /** 参考文献列表（结构与 AskReference 一致，可直接复用 ReferenceCard） */
  references: AskReference[];
}

// ---------------------------------------------------------------------------
// LLM 模型管理（自定义配置 CRUD）
// ---------------------------------------------------------------------------
export interface LLMConfig {
  id: number;
  displayName: string;
  apiUrl: string;
  apiKey: string; // 脱敏后的 key
  modelId: string;
  enabled: boolean;
  provider: string; // 自动识别的 provider
  createdAt: string;
  updatedAt: string;
}

export interface LLMConfigCreate {
  displayName: string;
  apiUrl: string;
  apiKey: string;
  modelId: string;
  enabled: boolean;
}

export interface LLMConfigUpdate {
  displayName?: string;
  apiUrl?: string;
  apiKey?: string;
  modelId?: string;
  enabled?: boolean;
}

// ---------------------------------------------------------------------------
// arXiv 论文导入
// ---------------------------------------------------------------------------
export interface ArxivPaperPreview {
  id: string;
  title: string;
  authors: string[];
  year: number;
  abstract: string;
  pdfUrl: string;
  source: string;
  category: string;
  tags: string[];
}

export interface ArxivImportResult {
  id: string;
  title: string;
  success: boolean;
  error?: string;
}

export interface ArxivImportResponse {
  results: ArxivImportResult[];
  successCount: number;
  failCount: number;
}

// ---------------------------------------------------------------------------
// Zotero 导入（B3）
// ---------------------------------------------------------------------------
export interface ZoteroImportResult {
  id: string;
  title: string;
  authors: string[];
  year: number;
  abstract: string;
  source: string;
  success: boolean;
  error?: string;
}

export interface ZoteroImportResponse {
  results: ZoteroImportResult[];
  successCount: number;
  failCount: number;
  totalFetched: number;
}

// ---------------------------------------------------------------------------
// 论文写作工作台（Writing Workbench）
// ---------------------------------------------------------------------------
export interface WritingProject {
  id: number;
  title: string;
  keywords: string[];
  targetJournal: string;
  targetWordCount: number;
  chapterCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectCreate {
  title: string;
  keywords?: string[];
  targetJournal?: string;
  templateId?: string;
}

export interface ProjectUpdate {
  title?: string;
  keywords?: string[];
  targetJournal?: string;
  targetWordCount?: number;
}

export interface Chapter {
  id: number;
  projectId: number;
  parentId: number | null;
  title: string;
  content: string;
  order: number;
  createdAt: string;
  updatedAt: string;
}

export interface ChapterTreeNode extends Chapter {
  children: ChapterTreeNode[];
}

export interface ChapterCreate {
  title: string;
  parentId?: number | null;
  content?: string;
  order?: number;
}

export interface ChapterUpdate {
  title?: string;
  content?: string;
  order?: number;
}

export interface ChapterMove {
  parentId: number | null;
  order: number;
}

export interface ChapterGenerateResult {
  chapterId: number;
  content: string;
  model: string;
}

export interface ExportReference {
  id: string;
  title: string;
  authors: string[];
  year: number;
}

export interface ExportResult {
  content: string;
  filename: string;
  references: ExportReference[];
}

// 异步导出任务
export interface ExportTaskCreate {
  taskId: string;
  status: string;
}

export interface ExportTaskProgress {
  taskId: string;
  progress: number;
  status: "pending" | "running" | "done" | "error";
  content: string;
  filename: string;
  references: ExportReference[];
  error: string;
  // P2-2: 分块进度（用于显示「正在处理第 X/Y 章节」）
  currentChapter: number;
  totalChapters: number;
}

// CSL-JSON 单条参考文献
export interface CslAuthor {
  family?: string;
  given?: string;
  literal?: string;
}

export interface CslDatePart {
  "date-parts"?: number[][];
  raw?: string;
}

export interface CslItem {
  id: string;
  type: string;
  title: string;
  author?: CslAuthor[];
  issued?: CslDatePart;
  URL?: string;
  DOI?: string;
  publisher?: string;
  "container-title"?: string;
  volume?: string;
  issue?: string;
  page?: string;
  [key: string]: unknown;
}

// 写作模板
export interface TemplateChapter {
  title: string;
  children: TemplateChapter[];
}

export interface Template {
  id: string;
  name: string;
  description: string;
  chapters: TemplateChapter[];
}

// 字数统计
export interface WordCountItem {
  id: number;
  title: string;
  wordCount: number;
}

export interface WordCountResult {
  total: number;
  chapters: WordCountItem[];
}

// ---------------------------------------------------------------------------
// 论文笔记
// ---------------------------------------------------------------------------
export interface Note {
  id: string;
  paperId: string;
  projectId: number | null;
  content: string;
  createdAt: string;
  updatedAt: string;
}

export interface NoteCreate {
  paperId: string;
  projectId?: number | null;
  content: string;
}

export interface NoteUpdate {
  content: string;
}

// 引用关系
export interface CitationRelations {
  citations: number;
  references: Paper[];
}

// ---------------------------------------------------------------------------
// PDF 实时翻译（WP-2.7）
// ---------------------------------------------------------------------------

/** 翻译响应 */
export interface TranslateResponse {
  translation: string;
}

/** 单条翻译历史记录 */
export interface TranslationHistoryItem {
  id: string;
  paperId: string;
  originalText: string;
  translatedText: string;
  targetLanguage: string;
  createdAt: string;
}

/** 翻译历史列表响应 */
export interface TranslationHistoryList {
  items: TranslationHistoryItem[];
}

/** 保存翻译历史请求 */
export interface TranslationHistoryCreate {
  original_text: string;
  translated_text: string;
  target_language: string;
}

// ---------------------------------------------------------------------------
// 引用情感 + 关系图（WP-2.2）
// ---------------------------------------------------------------------------

/** 关系图节点 */
export interface RelationGraphNode {
  id: string;
  title: string;
  category: string;
  year: number;
  sentimentScore: number | null;
  sentimentLabel: string | null;
  citations: number;
}

/** 关系图边 */
export interface RelationGraphEdge {
  source: string;
  target: string;
  type: "similarity" | "citation";
  weight: number;
  sentiment?: "support" | "criticize" | "background";
  snippet?: string;
}

/** 关系图响应 */
export interface RelationGraph {
  nodes: RelationGraphNode[];
  edges: RelationGraphEdge[];
  /** 图的类型：citation 表示基于被引情感，similarity 表示基于语义相似度降级 */
  mode: "citation" | "similarity";
}

/** 单条被引情感记录 */
export interface CitationSentiment {
  sourcePaperId: string;
  targetPaperId: string;
  sentimentLabel: "support" | "criticize" | "background";
  confidence: number;
  contextSnippet: string;
}

/** 被引情感统计 */
export interface CitationSentimentStats {
  paperId: string;
  sentiments: CitationSentiment[];
  counts: {
    support: number;
    criticize: number;
    background: number;
  };
}

// ---------------------------------------------------------------------------
// 用户自定义模板
// ---------------------------------------------------------------------------
export interface UserTemplate {
  id: string;
  name: string;
  description: string;
  chapters: TemplateChapter[];
  createdAt: string;
}

export interface UserTemplateCreate {
  name: string;
  description: string;
  chapters: TemplateChapter[];
}

// ---------------------------------------------------------------------------
// 写作统计
// ---------------------------------------------------------------------------
export interface DailyWordTrend {
  date: string;
  wordCount: number;
}

export interface WritingStats {
  totalProjects: number;
  totalWords: number;
  todayWords: number;
  trend: DailyWordTrend[];
}

// ---------------------------------------------------------------------------
// 章节历史版本
// ---------------------------------------------------------------------------
export interface ChapterVersion {
  id: string;
  chapterId: number;
  wordCount: number;
  createdAt: string;
}

export interface ChapterVersionDetail {
  id: string;
  chapterId: number;
  content: string;
  wordCount: number;
  createdAt: string;
}

export interface ChapterRestoreResult {
  chapterId: number;
  versionId: string;
  content: string;
  wordCount: number;
}

// ---------------------------------------------------------------------------
// AI 写作辅助
// ---------------------------------------------------------------------------
export interface StructureSuggestion {
  suggestion: string;
  targetChapter: string;
  /** C3：目标章节 ID（按名称匹配，未匹配到时为 null） */
  targetChapterId: number | null;
  reason: string;
  confidence: number;
}

/** C1：改写结果 */
export interface RewriteResult {
  rewritten: string;
}

/** C2：续写历史记录 */
export interface ContinuationRecord {
  id: string;
  chapterId: number;
  content: string;
  direction: string;
  /** provenance 类型：服务端自动盖戳（continue / rewrite） */
  kind: "continue" | "rewrite";
  createdAt: string;
}

/** C3：采纳结构建议的返回结果 */
export interface ApplySuggestionResult {
  chapter: Chapter;
  message: string;
}

/** 引用智能推荐：推荐论文条目 */
export interface RecommendedPaper {
  id: string;
  title: string;
  authors: string[];
  year: number;
  score: number;
}

/** 引用智能推荐响应 */
export interface RecommendCitationsResponse {
  papers: RecommendedPaper[];
}

// ---------------------------------------------------------------------------
// 批量删除
// ---------------------------------------------------------------------------

export interface BatchDeleteRequest {
  paper_ids: string[];
}

export interface BatchDeleteResponse {
  success: boolean;
  deleted_count: number;
  failed_ids: string[];
}

// ---------------------------------------------------------------------------
// 批量标签管理（WP-2.1）
// ---------------------------------------------------------------------------

export interface BatchTagRequest {
  paper_ids: string[];
  add_tags?: string[];
  remove_tags?: string[];
}

export interface BatchTagResponse {
  success: boolean;
  updated_count: number;
  failed_ids: string[];
}

export interface TagInfo {
  name: string;
  count: number;
}

export interface RenameTagRequest {
  old_name: string;
  new_name: string;
}

export interface TagMutationResponse {
  success: boolean;
  affected_count: number;
}

// ---------------------------------------------------------------------------
// 去重合并（WP-5.2）
// ---------------------------------------------------------------------------
export interface DuplicateGroupPaper {
  id: string;
  title: string;
  authors: string[];
  year: number;
  abstract: string;
  journal: string;
  pdfUrl: string;
  source: string;
  citations: number;
  tags: string[];
}

export interface DuplicateGroup {
  papers: DuplicateGroupPaper[];
}

export interface DuplicateGroupsResponse {
  groups: DuplicateGroup[];
}

export interface MergePapersRequest {
  target_id: string;
  source_ids: string[];
  field_sources: Record<string, string>;
}

export interface MergePapersResponse {
  success: boolean;
  target_id: string;
  deleted_ids: string[];
  paper: Paper | null;
}

// ---------------------------------------------------------------------------
// 跨文档对比表（WP-2.3）
// ---------------------------------------------------------------------------
export interface ComparePapersRequest {
  paper_ids: string[];
  question?: string;
}

export interface ComparisonRow {
  paper_id: string;
  title: string;
  authors: string[];
  year: number;
  method: string;
  sample_size: string;
  main_results: string;
  metrics: string;
  limitations: string;
  conclusion: string;
}

export interface ComparePapersResponse {
  rows: ComparisonRow[];
  generated_at: string;
  model: string;
}

// ---------------------------------------------------------------------------
// 指定论文对比分析（新功能）
// ---------------------------------------------------------------------------

export interface AnalyzeRequest {
  paper_ids: string[];
  topic?: string;
  weights?: {
    citation: number;
    time: number;
    similarity: number;
    journal: number;
  };
}

export interface AnalyzedPaper {
  id: string;
  title: string;
  authors: string[];
  year: number;
  citations: number;
  journal?: string;
  similarity_score?: number;
  total_score: number;
}

export interface AnalysisSummary {
  top_paper: string;
  total_citations: number;
  avg_year: number;
  distribution_by_category?: Record<string, number>;
}

export interface AnalyzeResponse {
  analyzed_papers: AnalyzedPaper[];
  summary: AnalysisSummary;
}

// ---------------------------------------------------------------------------
// DEPTH A/B 可调参数（Settings 页在线调节）
// ---------------------------------------------------------------------------
export interface DepthBoundsOverride {
  min: number;
  max: number;
}

export interface DepthSettings {
  severityFallbackThreshold: number;
  severityFatalWeight: number;
  severityMinorWeight: number;
  q5cClaimSeverityFactor: number;
  claimValidationPenaltyPerClaim: number;
  claimValidationPenaltyMax: number;
  deltaDefaultMin: number;
  deltaDefaultMax: number;
  deltaBoundsOverrides: Record<string, DepthBoundsOverride>;
}

// ---------------------------------------------------------------------------
// AI 量化评估
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// DEPTH 论文多维评分
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// DEPTH 感悟/读后/复现报告 reflection 轻量 pipeline
// ---------------------------------------------------------------------------

/** 报告中的核心观点/隐含论点 */
export interface ReflectionClaim {
  id: string; // C1, C2
  text: string;
  evidence_id?: string | null;
}

/** 从报告原文提取的支撑性片段 */
export interface ReflectionEvidenceItem {
  id: string; // E1, E2
  snippet: string;
  claim_ref?: string | null;
}

/** 反思报告的评分维度（0~1）。后端 _legacy_data_compat 会兑底为全 0。
 *  能力 4 维（understanding/analysis/innovation/evidence）由 LLM 评分；
 *  交叉评价 2 维（fidelity 报告→论文 有据性 / coverage 论文→报告 覆盖度）由向量比对计算。
 *  fidelity/coverage 为可选字段，旧数据可能缺失。 */
export interface ReflectionScores {
  understanding_accuracy: number | null;
  analysis_depth: number | null;
  innovative_insights: number | null;
  evidence_support: number | null;
  /** 报告→论文 有据性（防编造），向量比对 0~1，旧数据可能缺失 */
  fidelity?: number | null;
  /** 论文→报告 覆盖度（防遗漏核心，正确性核心指标），向量比对 0~1，旧数据可能缺失 */
  coverage?: number | null;
  average: number | null;
}

/** Reflection verdict 字面量（与后端 ReflectionResult.verdict 同步） */
export type ReflectionVerdict =
  "well_done" | "needs_evidence" | "needs_depth" | "rewrite_required" | "llm_failed" | "n/a";

/** 感悟报告评审结果完整 JSON */
export interface ReflectionResult {
  claims: ReflectionClaim[];
  evidence_pool: ReflectionEvidenceItem[];
  scores: ReflectionScores;
  summary: string;
  verdict: ReflectionVerdict | string;
  verdict_reason: string;
  /** 有据性评分 0~1（报告→论文，防编造） */
  fidelity?: number;
  /** 有据性计算状态：ok | too_short | no_paper | degraded_model */
  fidelity_status?: string;
  /** 有据性锚点句列表（top-10 按 sim 降序） */
  fidelity_anchors?: FidelityAnchor[];
  /** 疑似编造句（低相似度 + 含断言词） */
  fidelity_stray_claims?: string[];
  /** 覆盖度评分 0~1（论文→报告，防遗漏核心，正确性核心指标） */
  coverage?: number;
  /** 覆盖度计算状态：ok | too_short | no_paper | degraded_model */
  coverage_status?: string;
  /** 已被报告覆盖的论文核心要点 [{keypoint, sim}] */
  coverage_covered?: { keypoint: string; sim: number }[];
  /** 未被报告覆盖的论文核心要点（报告遗漏的核心，供前端展示） */
  coverage_uncovered?: { keypoint: string; sim: number }[];
  /** 6 维加权评分（含 fidelity + coverage），后端写入 */
  analysis_v2?: AnalysisV2;
  /** 评审失败诊断：存在时不得展示 scores/average */
  llm_failed?: boolean;
  parse_failed?: boolean;
  // ── 评审依据（审计）字段 ──
  /** 双模型交叉复核（分歧 → 建议人工复核） */
  cross_check?: CrossCheck | null;
  /** LLM 参数快照（seed / 温度 / max_tokens），供复现 */
  llm_params_snapshot?: {
    seed?: number;
    temperature?: number;
    max_tokens?: number;
    model?: string;
    provider?: string;
    base_url?: string;
    [key: string]: unknown;
  };
  effective_evidence_count?: number | null;
  hardcoded_overrides?: string[];
  evidence_rejections?: Record<string, number>;
  citation_integrity?: Record<string, unknown> | null;
  citation_override_reason?: string;
  copy_ratio?: number | null;
  copy_crushed?: boolean;
  ai_likelihood?: number | null;
  ai_likelihood_tier?: string;  llm_calls?: number;
  llm_empty?: number;

  truncated?: boolean;
  /** 本次评审实际读取的原论文预览字数（动态预算后；0/缺失 = 未注入论文参考） */
  paper_preview_chars?: number | null;
}

/** 忠实度锚点句：报告中的句子 vs 原论文相似度 */
export interface FidelityAnchor {
  sentence: string;
  sim: number;
}

/** 分数不确定性（bootstrap 95% CI；PAPERFORGE_UNCERTAINTY_GATE=1 时后端填充） */
export interface ScoreUncertainty {
  score: number | null;
  ci_low: number | null;
  ci_high: number | null;
  ci_width: number | null;
  status: "ok" | "needs_human_review" | "no_data" | "error" | string;
  note: string;
}

/** 双模型交叉复核结果（PAPERFORGE_SECOND_OPINION_ENABLED=1 且配置第二模型时后端填充） */
export interface CrossCheck {
  enabled: boolean;
  flag: "agree" | "disagreement" | "no_data";
  note?: string;
  second_provider?: string | null;
  second_model?: string | null;
  second_score?: number | null;
  second_verdict?: string | null;
  second_reason?: string;
  score_delta?: number | null;
  verdict_agree?: boolean | null;
  skipped?: string;
  error?: string;
}

/** 6 维加权评分（能力 4 维 + 交叉评价 2 维 fidelity/coverage） */
export interface AnalysisV2 {
  understanding_accuracy: number | null;
  analysis_depth: number | null;
  innovative_insights: number | null;
  evidence_support: number | null;
  fidelity: number | null;
  coverage: number | null;
  average: number | null;
  llm_failed?: boolean;
  // ── 评审依据（审计）字段：后端 reflection_pipeline 写入，前端「评审依据」面板展示 ──
  /** 分数不确定性（bootstrap CI） */
  score_uncertainty?: ScoreUncertainty | null;
  /** 硬编码规则覆盖记录（R1-R7 触发日志，解释分数为何被压制/降级） */
  hardcoded_overrides?: string[];
  effective_evidence_count?: number | null;
  /** 证据被判无效的原因分布 {ok, from_paper, not_found} */
  evidence_rejections?: Record<string, number>;
  /** 引用真值校验结果（PAPERFORGE_CITATION_VERIFY=1 时非空） */
  citation_integrity?: {
    integrity_flag?: string;
    checked?: number;
    fabricated?: number;
    inconsistent?: number;
    [key: string]: unknown;
  } | null;
  citation_override_reason?: string;
  /** AI 生成疑似度（advisory only，不影响分数） */
  ai_likelihood?: number | null;
  ai_likelihood_tier?: string;
  /** 照抄比率与封杀标记 */
  copy_ratio?: number | null;
  copy_crushed?: boolean;
  /** LLM 调用诊断（区分系统故障与报告质量问题） */
  llm_calls?: number;
  llm_empty?: number;
  parse_failed?: boolean;
  truncated?: boolean;
  /** 本次评审实际读取的原论文预览字数（动态预算后；0/缺失 = 未注入论文参考） */
  paper_preview_chars?: number | null;
  /** 原论文全文总字数（完整 PDF 抽取，非预览） */
  paper_chars?: number | null;
  /** 感悟报告全文总字数 */
  report_chars?: number | null;
  weights?: Record<string, number>;
}

/** Reflection list 项（GET /api/depth/reflection/list） */
export interface ReflectionListItem {
  id: number;
  paper_id: string;
  paper_title: string;
  status: "pending" | "running" | "completed" | "failed" | "timed_out";
  document_type: "report";
  scores: ReflectionScores;
  verdict: ReflectionVerdict | string;
  verdict_reason: string;
  summary_short: string;
  effective_evidence_count: number;
  claims_count: number;
  error_message: string | null;
  created_at: string | null;
  completed_at: string | null;
  /** 有据性评分 0~1（报告→论文，来自 reflection_result.fidelity） */
  fidelity?: number | null;
  /** 覆盖度评分 0~1（论文→报告，来自 reflection_result.coverage） */
  coverage?: number | null;
  /** LLM 故障时为 true，分数不可用 */
  llm_failed?: boolean;
}

/** Reflection list 响应 */
export interface ReflectionListResponse {
  items: ReflectionListItem[];
  total: number;
  limit: number;
  offset: number;
}

/** Reflection result 详情响应（GET /api/depth/reflection/result/{id}） */
export interface ReflectionResultResponse {
  id: number;
  paper_id: string;
  kind: "report";
  status: "pending" | "running" | "completed" | "failed" | "timed_out";
  version: string;
  result: ReflectionResult | null;
  error_message: string | null;
  created_at: string | null;
  completed_at: string | null;
}

/** Reflection upload 请求体（POST /api/depth/reflection/text，202 Accepted） */
export interface ReflectionUploadRequest {
  title: string;
  content: string;
  authors?: string[];
  year?: number | null;
  sourcePaperId?: string | null;
}

/** 报告上传后系统自动识别/导入原论文的状态摘要 */
export interface SourcePaperInfo {
  status:
    "exists" | "imported" | "rate_limited" | "no_match" | "import_failed" | "no_title" | "manual";
  paperId?: string | null;
  title?: string | null;
  source?: "arxiv" | "semantic_scholar" | null;
}

/** Reflection upload 响应 */
export interface ReflectionCreateResponse {
  paperId: string;
  taskId: string | null;
  documentType: "report";
  message: string;
  sourcePaper?: SourcePaperInfo | null;
}

/** 批量感悟上传单文件结果 */
export interface ReflectionFileBatchItem {
  filename: string;
  paperId: string | null;
  taskId: string | null;
  status: "accepted" | "skipped" | "failed";
  error: string;
  bytes: number;
  sourcePaper?: SourcePaperInfo | null;
}

/** 批量感悟上传响应 (POST /api/depth/reflection/files) */
export interface ReflectionFileBatchResponse {
  items: ReflectionFileBatchItem[];
  submitted: number;
  failed: number;
  total: number;
  message: string;
}

/** Reflection run 响应（POST /api/depth/reflection/run/{id}，手动重跑） */
export interface ReflectionRunResponse {
  task_id: string;
  status: "pending" | "running";
  paper_id: string;
  kind: "report";
  message: string;
}

/** 统一 DEPTH 评审记录项（GET /api/depth/unified/list） */
export interface UnifiedReviewItem {
  id: number;
  paper_id: string;
  paper_title: string;
  kind: "paper" | "report" | null;
  status: "pending" | "running" | "completed" | "failed" | "timed_out";
  version: string | null;
  primary_score: number | null;
  verdict: string | null;
  summary_short: string;
  created_at: string | null;
  completed_at: string | null;
}

export interface UnifiedReviewListResponse {
  items: UnifiedReviewItem[];
  total: number;
  kind: "all" | "paper" | "report";
  limit: number;
  offset: number;
}

export interface DepthScore {
  paper_id: string;
  title: string;
  type: "A" | "B" | "C" | "D";
  confidence: number; // 0~1
  has_substance: boolean; // Q0 是否有实质性贡献
  expectation: number; // Q0 期待度 0~1
  obj_score: number; // 0~1 客观指标
  novelty_score: number; // 0~1
  rigor_score: number; // 0~1
  influence_score: number; // 0~1
  reproducibility_score: number; // 0~1
  calibrated_score: number; // 0~1 主席校准分
  final_score: number; // 0~100 百分制
  verdict: "accept" | "minor_revision" | "major_revision" | "reject";
  core_contribution: string;
  keywords: string[];
  missing_items: string[];
  critique_points: string[]; // Q5a 质疑点
  defense_points: string[]; // Q5b 辩护点
  chair_reasoning: string; // Q5c 主席裁决理由
  content_source: string;
  evaluated_at?: string;
  rank?: number;
  /** 各节点采样标准差（仅 v4.1 输出，v3 不存在） */
  node_score_stds?: Record<string, number>;
}

export interface DepthBatchResponse {
  task_id: string;
  total_papers: number;
}

export interface DepthTaskStatus {
  task_id: string;
  status: "pending" | "running" | "completed" | "failed" | "timed_out";
  progress: { current: number; total: number };
  results: DepthScore[] | null;
  errors: string[];
  summary: {
    total: number;
    completed: number;
    failed: number;
    highest_score: number;
    average_score: number;
  } | null;
}

// ---------------------------------------------------------------------------
// 论文综合评分与批量对比
// ---------------------------------------------------------------------------

/** 综合评分各维度权重 */
export interface RankWeights {
  citations: number; // 引用数权重，默认 0.30
  recency: number; // 发表时间权重，默认 0.25
  similarity: number; // 主题相似度权重，默认 0.30
  journal: number; // 期刊等级权重，默认 0.15
}

/** 综合评分请求 */
export interface RankRequest {
  topic: string;
  weights?: RankWeights;
  paperIds?: string[];
  topK?: number; // 默认 20
}

/** 各维度得分 */
export interface DimensionScores {
  citations: number;
  recency: number;
  similarity: number;
  journal: number;
}

/** 带综合得分的论文条目 */
export interface RankedPaper {
  id: string;
  title: string;
  authors: string[];
  year: number;
  journal: string;
  citations: number;
  compositeScore: number;
  dimensions: DimensionScores;
}

/** 综合评分响应 */
export interface RankResponse {
  papers: RankedPaper[];
  total: number;
  topic: string;
  weights: RankWeights;
}

/** 单维度统计 */
export interface DimensionStats {
  min: number;
  max: number;
  mean: number;
  median: number;
  std: number;
}

// ---------------------------------------------------------------------------
// ZIP 压缩包批量上传
// ---------------------------------------------------------------------------

/** ZIP 包内单个失败文件信息 */
export interface ZipFailedFile {
  filename: string;
  error: string;
}

/** 已上传成功的论文条目（单篇上传 / ZIP 解包通用） */
export interface UploadedPaper {
  id: string;
  title: string;
  authors: string[];
  year: number;
  abstract: string;
  source: string;
  success: boolean;
  error: string;
}

/** ZIP 批量上传响应 */
export interface ZipUploadResponse {
  total: number;
  succeeded: number;
  failed: number;
  failed_files: ZipFailedFile[];
  papers: UploadedPaper[];
}
/** 大纲节点（递归结构） */
export interface OutlineNode {
  title: string;
  children?: OutlineNode[];
}

/** 自动生成大纲请求体 */
export interface GenerateOutlineRequest {
  projectId: number;
  topic: string;
  keywords?: string[];
}

// ---------------------------------------------------------------------------
// B4：PDF 批注/高亮
// ---------------------------------------------------------------------------
/** 高亮颜色选项 */
export type HighlightColor = "#FFEB3B" | "#4FC3F7" | "#81C784" | "#F48FB1";

/** PDF 批注（前端 localStorage 为主，后端为可选同步目标） */
export interface PdfAnnotation {
  id: string;
  paperId: string;
  page: number;
  /** 四边形坐标数组（pdfjs 选区的 quadpoints） */
  quadpoints: number[];
  color: HighlightColor | string;
  note: string;
  createdAt: string;
}

export interface PdfAnnotationCreate {
  paperId: string;
  page: number;
  quadpoints: number[];
  color: string;
  note: string;
}

export interface PdfAnnotationUpdate {
  color?: string;
  note?: string;
}
