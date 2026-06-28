// 论文领域类型定义

export interface Paper {
  id: string // arXiv ID
  title: string
  authors: string[]
  year: number
  abstract: string
  category: string
  tags: string[]
  citations: number
  chunkCount: number
  indexSize: number // bytes
  pdfUrl: string
  source: 'arxiv' | 'pubmed' | 'ieee' | 'springer'
  journal?: string
  favorited?: boolean
  // B2: Semantic Scholar 富化字段（未富化时为 null）
  influentialCitations?: number | null
  fieldsOfStudy?: string[] | null
}

export interface PageResult<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}

export interface PaperQuery {
  keyword?: string
  category?: string
  sort?: string
  page?: number
  pageSize?: number
  source?: string
}

export interface LibraryStats {
  totalPapers: number
  totalChunks: number
  totalSize: number
  byCategory: { category: string; count: number }[]
}

export interface SuggestItem {
  id: string
  title: string
  // FTS5 返回的 HTML 高亮片段（可能为空，为空时前端回退到 title）
  highlight?: string
}

// ---------------------------------------------------------------------------
// 论文问答（PaperForge /ask 接口）
// ---------------------------------------------------------------------------
export interface AskRequest {
  question: string
  paperIds?: string[]
}

export interface AskReference {
  id: string
  title: string
  authors?: string[]
  year?: number
  /** 命中片段（可选，用于在参考文献卡片中预览） */
  snippet?: string
}

export interface AskResponse {
  answer: string
  references: AskReference[]
}

// ---------------------------------------------------------------------------
// 综述生成（PaperForge /generate 接口）
// ---------------------------------------------------------------------------
export interface GenerateResponse {
  /** Markdown 格式的综述正文 */
  content: string
  /** 参考文献列表（结构与 AskReference 一致，可直接复用 ReferenceCard） */
  references: AskReference[]
}

// ---------------------------------------------------------------------------
// LLM 模型管理（自定义配置 CRUD）
// ---------------------------------------------------------------------------
export interface LLMConfig {
  id: number
  displayName: string
  apiUrl: string
  apiKey: string // 脱敏后的 key
  modelId: string
  enabled: boolean
  provider: string // 自动识别的 provider
  createdAt: string
  updatedAt: string
}

export interface LLMConfigCreate {
  displayName: string
  apiUrl: string
  apiKey: string
  modelId: string
  enabled: boolean
}

export interface LLMConfigUpdate {
  displayName?: string
  apiUrl?: string
  apiKey?: string
  modelId?: string
  enabled?: boolean
}

// ---------------------------------------------------------------------------
// arXiv 论文导入
// ---------------------------------------------------------------------------
export interface ArxivPaperPreview {
  id: string
  title: string
  authors: string[]
  year: number
  abstract: string
  pdfUrl: string
  source: string
  category: string
  tags: string[]
}

export interface ArxivImportResult {
  id: string
  title: string
  success: boolean
  error?: string
}

export interface ArxivImportResponse {
  results: ArxivImportResult[]
  successCount: number
  failCount: number
}

// ---------------------------------------------------------------------------
// Zotero 导入（B3）
// ---------------------------------------------------------------------------
export interface ZoteroImportResult {
  id: string
  title: string
  authors: string[]
  year: number
  abstract: string
  source: string
  success: boolean
  error?: string
}

export interface ZoteroImportResponse {
  results: ZoteroImportResult[]
  successCount: number
  failCount: number
  totalFetched: number
}

// ---------------------------------------------------------------------------
// 论文写作工作台（Writing Workbench）
// ---------------------------------------------------------------------------
export interface WritingProject {
  id: number
  title: string
  keywords: string[]
  targetJournal: string
  targetWordCount: number
  chapterCount: number
  createdAt: string
  updatedAt: string
}

export interface ProjectCreate {
  title: string
  keywords?: string[]
  targetJournal?: string
  templateId?: string
}

export interface ProjectUpdate {
  title?: string
  keywords?: string[]
  targetJournal?: string
  targetWordCount?: number
}

export interface Chapter {
  id: number
  projectId: number
  parentId: number | null
  title: string
  content: string
  order: number
  createdAt: string
  updatedAt: string
}

export interface ChapterTreeNode extends Chapter {
  children: ChapterTreeNode[]
}

export interface ChapterCreate {
  title: string
  parentId?: number | null
  content?: string
  order?: number
}

export interface ChapterUpdate {
  title?: string
  content?: string
  order?: number
}

export interface ChapterMove {
  parentId: number | null
  order: number
}

export interface ChapterGenerateResult {
  chapterId: number
  content: string
  model: string
}

export interface ExportReference {
  id: string
  title: string
  authors: string[]
  year: number
}

export interface ExportResult {
  content: string
  filename: string
  references: ExportReference[]
}

// 异步导出任务
export interface ExportTaskCreate {
  taskId: string
  status: string
}

export interface ExportTaskProgress {
  taskId: string
  progress: number
  status: 'pending' | 'running' | 'done' | 'error'
  content: string
  filename: string
  references: ExportReference[]
  error: string
  // P2-2: 分块进度（用于显示「正在处理第 X/Y 章节」）
  currentChapter: number
  totalChapters: number
}

// 写作模板
export interface TemplateChapter {
  title: string
  children: TemplateChapter[]
}

export interface Template {
  id: string
  name: string
  description: string
  chapters: TemplateChapter[]
}

// 字数统计
export interface WordCountItem {
  id: number
  title: string
  wordCount: number
}

export interface WordCountResult {
  total: number
  chapters: WordCountItem[]
}

// ---------------------------------------------------------------------------
// 论文笔记
// ---------------------------------------------------------------------------
export interface Note {
  id: string
  paperId: string
  projectId: number | null
  content: string
  createdAt: string
  updatedAt: string
}

export interface NoteCreate {
  paperId: string
  projectId?: number | null
  content: string
}

export interface NoteUpdate {
  content: string
}

// 引用关系
export interface CitationRelations {
  citations: number
  references: Paper[]
}

// ---------------------------------------------------------------------------
// 用户自定义模板
// ---------------------------------------------------------------------------
export interface UserTemplate {
  id: string
  name: string
  description: string
  chapters: TemplateChapter[]
  createdAt: string
}

export interface UserTemplateCreate {
  name: string
  description: string
  chapters: TemplateChapter[]
}

// ---------------------------------------------------------------------------
// 写作统计
// ---------------------------------------------------------------------------
export interface DailyWordTrend {
  date: string
  wordCount: number
}

export interface WritingStats {
  totalProjects: number
  totalWords: number
  todayWords: number
  trend: DailyWordTrend[]
}

// ---------------------------------------------------------------------------
// 章节历史版本
// ---------------------------------------------------------------------------
export interface ChapterVersion {
  id: string
  chapterId: number
  wordCount: number
  createdAt: string
}

export interface ChapterVersionDetail {
  id: string
  chapterId: number
  content: string
  wordCount: number
  createdAt: string
}

export interface ChapterRestoreResult {
  chapterId: number
  versionId: string
  content: string
  wordCount: number
}

// ---------------------------------------------------------------------------
// AI 写作辅助
// ---------------------------------------------------------------------------
export interface StructureSuggestion {
  suggestion: string
  targetChapter: string
  /** C3：目标章节 ID（按名称匹配，未匹配到时为 null） */
  targetChapterId: number | null
  reason: string
  confidence: number
}

/** C1：改写结果 */
export interface RewriteResult {
  rewritten: string
}

/** C2：续写历史记录 */
export interface ContinuationRecord {
  id: string
  chapterId: number
  content: string
  direction: string
  createdAt: string
}

/** C3：采纳结构建议的返回结果 */
export interface ApplySuggestionResult {
  chapter: Chapter
  message: string
}

/** 引用智能推荐：推荐论文条目 */
export interface RecommendedPaper {
  id: string
  title: string
  authors: string[]
  year: number
  score: number
}

/** 引用智能推荐响应 */
export interface RecommendCitationsResponse {
  papers: RecommendedPaper[]
}

// ---------------------------------------------------------------------------
// P1：自动生成大纲
// ---------------------------------------------------------------------------
/** 大纲节点（递归结构） */
export interface OutlineNode {
  title: string
  children?: OutlineNode[]
}

/** 自动生成大纲请求体 */
export interface GenerateOutlineRequest {
  projectId: number
  topic: string
  keywords?: string[]
}

// ---------------------------------------------------------------------------
// B4：PDF 批注/高亮
// ---------------------------------------------------------------------------
/** 高亮颜色选项 */
export type HighlightColor = '#FFEB3B' | '#4FC3F7' | '#81C784' | '#F48FB1'

/** PDF 批注（前端 localStorage 为主，后端为可选同步目标） */
export interface PdfAnnotation {
  id: string
  paperId: string
  page: number
  /** 四边形坐标数组（pdfjs 选区的 quadpoints） */
  quadpoints: number[]
  color: HighlightColor | string
  note: string
  createdAt: string
}

export interface PdfAnnotationCreate {
  paperId: string
  page: number
  quadpoints: number[]
  color: string
  note: string
}

export interface PdfAnnotationUpdate {
  color?: string
  note?: string
}
