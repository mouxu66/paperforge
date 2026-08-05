import http from "./client";
import type {
  ApplySuggestionResult,
  ChapterCreate,
  ChapterGenerateResult,
  ChapterMove,
  ChapterRestoreResult,
  ChapterTreeNode,
  ChapterUpdate,
  ChapterVersion,
  ChapterVersionDetail,
  Chapter,
  ContinuationRecord,
  CslItem,
  ExportResult,
  ExportTaskCreate,
  ExportTaskProgress,
  GenerateOutlineRequest,
  OutlineNode,
  ProjectCreate,
  ProjectUpdate,
  RecommendCitationsResponse,
  RewriteResult,
  StructureSuggestion,
  Template,
  UserTemplate,
  UserTemplateCreate,
  WordCountResult,
  WritingProject,
  WritingStats,
} from "./types";

import { API_BASE, getAuthHeaders } from "./client";

// SSE 流式接口的 baseURL（与 axios 实例一致）
const SSE_BASE = API_BASE;

// ---------------------------------------------------------------------------
// 写作模板（系统预设）
// ---------------------------------------------------------------------------

/** 获取所有写作模板（预设大纲结构） */
export async function fetchTemplates(): Promise<Template[]> {
  const { data } = await http.get<Template[]>("/writing/templates");
  return data;
}

// ---------------------------------------------------------------------------
// 用户自定义模板
// ---------------------------------------------------------------------------

/** 获取所有用户自定义模板 */
export async function fetchUserTemplates(): Promise<UserTemplate[]> {
  const { data } = await http.get<UserTemplate[]>("/writing/templates/user");
  return data;
}

/** 新建用户自定义模板 */
export async function createUserTemplate(payload: UserTemplateCreate): Promise<UserTemplate> {
  const { data } = await http.post<UserTemplate>("/writing/templates/user", payload);
  return data;
}

/** 将当前项目的大纲结构保存为自定义模板 */
export async function saveProjectAsTemplate(
  projectId: number,
  payload: UserTemplateCreate,
): Promise<UserTemplate> {
  const { data } = await http.post<UserTemplate>(
    `/writing/projects/${projectId}/save-as-template`,
    payload,
  );
  return data;
}

/** 删除用户自定义模板 */
export async function deleteUserTemplate(templateId: string): Promise<void> {
  await http.delete(`/writing/templates/user/${templateId}`);
}

// ---------------------------------------------------------------------------
// 写作统计
// ---------------------------------------------------------------------------

/** 获取写作统计看板数据（总项目数、总字数、今日新增、近 7 天趋势） */
export async function fetchWritingStats(): Promise<WritingStats> {
  const { data } = await http.get<WritingStats>("/writing/stats");
  return data;
}

// ---------------------------------------------------------------------------
// 项目 CRUD
// ---------------------------------------------------------------------------

/** 列出所有写作项目 */
export async function fetchWritingProjects(): Promise<WritingProject[]> {
  const { data } = await http.get<WritingProject[]>("/writing/projects");
  return data;
}

/** 获取单个写作项目 */
export async function fetchWritingProject(id: number): Promise<WritingProject> {
  const { data } = await http.get<WritingProject>(`/writing/projects/${id}`);
  return data;
}

/** 新建写作项目（后端自动创建「引言」章节） */
export async function createWritingProject(payload: ProjectCreate): Promise<WritingProject> {
  const { data } = await http.post<WritingProject>("/writing/projects", payload);
  return data;
}

/** 更新写作项目元数据 */
export async function updateWritingProject(
  id: number,
  payload: ProjectUpdate,
): Promise<WritingProject> {
  const { data } = await http.put<WritingProject>(`/writing/projects/${id}`, payload);
  return data;
}

/** 删除写作项目及其所有章节 */
export async function deleteWritingProject(id: number): Promise<void> {
  await http.delete(`/writing/projects/${id}`);
}

// ---------------------------------------------------------------------------
// 章节 CRUD
// ---------------------------------------------------------------------------

/** 获取项目章节大纲树（递归结构） */
export async function fetchChapterTree(projectId: number): Promise<ChapterTreeNode[]> {
  const { data } = await http.get<ChapterTreeNode[]>(`/writing/projects/${projectId}/chapters`);
  return data;
}

/** 新增章节（parentId 指定时挂到对应父章节下） */
export async function createChapter(projectId: number, payload: ChapterCreate): Promise<Chapter> {
  const { data } = await http.post<Chapter>(`/writing/projects/${projectId}/chapters`, payload);
  return data;
}

/** 更新章节（标题 / 内容 / 顺序） */
export async function updateChapter(chapterId: number, payload: ChapterUpdate): Promise<Chapter> {
  const { data } = await http.put<Chapter>(`/writing/chapters/${chapterId}`, payload);
  return data;
}

/** 删除章节及其所有子孙节点 */
export async function deleteChapter(chapterId: number): Promise<void> {
  await http.delete(`/writing/chapters/${chapterId}`);
}

/** 移动 / 拖拽重排章节（更新 parentId 与 order） */
export async function moveChapter(chapterId: number, payload: ChapterMove): Promise<Chapter> {
  const { data } = await http.put<Chapter>(`/writing/chapters/${chapterId}/move`, payload);
  return data;
}

/** AI 生成章节内容（后端调用 LLM 生成初稿并自动保存） */
export async function generateChapter(
  projectId: number,
  chapterId: number,
): Promise<ChapterGenerateResult> {
  const { data } = await http.post<ChapterGenerateResult>(
    `/writing/projects/${projectId}/chapters/${chapterId}/generate`,
  );
  return data;
}

// ---------------------------------------------------------------------------
// 导出（异步任务）
// ---------------------------------------------------------------------------

/** 启动异步导出任务，返回 task_id */
export async function startExportTask(projectId: number): Promise<ExportTaskCreate> {
  const { data } = await http.post<ExportTaskCreate>(`/writing/projects/${projectId}/export`);
  return data;
}

/** 轮询导出任务进度 */
export async function getExportProgress(taskId: string): Promise<ExportTaskProgress> {
  const { data } = await http.get<ExportTaskProgress>(`/writing/export/${taskId}/progress`);
  return data;
}

/** 获取项目的 CSL-JSON 参考文献列表 */
export async function exportProjectCsl(projectId: number): Promise<CslItem[]> {
  const { data } = await http.get<CslItem[]>("/writing/projects/" + projectId + "/export/csl");
  return data;
}

/** 兼容旧调用：同步导出（内部轮询直到完成或失败） */
export async function exportWritingProject(projectId: number): Promise<ExportResult> {
  const { taskId } = await startExportTask(projectId);
  // 轮询直到任务完成或失败（最多等 60 秒）
  for (let i = 0; i < 120; i++) {
    await new Promise((r) => setTimeout(r, 500));
    const p = await getExportProgress(taskId);
    if (p.status === "done") {
      return {
        content: p.content,
        filename: p.filename,
        references: p.references,
      };
    }
    if (p.status === "error") {
      throw new Error(p.error || "导出失败");
    }
  }
  throw new Error("导出超时");
}

/** 获取项目字数统计（总字数 + 各章节字数） */
export async function fetchWordCount(projectId: number): Promise<WordCountResult> {
  const { data } = await http.get<WordCountResult>(`/writing/projects/${projectId}/word-count`);
  return data;
}

// ---------------------------------------------------------------------------
// 章节历史版本
// ---------------------------------------------------------------------------

/** 获取章节历史版本列表（按时间倒序，最多 10 个） */
export async function fetchChapterVersions(chapterId: number): Promise<ChapterVersion[]> {
  const { data } = await http.get<ChapterVersion[]>(`/writing/chapters/${chapterId}/versions`);
  return data;
}

/** 获取某个历史版本的完整内容 */
export async function fetchChapterVersion(
  chapterId: number,
  versionId: string,
): Promise<ChapterVersionDetail> {
  const { data } = await http.get<ChapterVersionDetail>(
    `/writing/chapters/${chapterId}/versions/${versionId}`,
  );
  return data;
}

/** 恢复章节到指定历史版本 */
export async function restoreChapterVersion(
  chapterId: number,
  versionId: string,
): Promise<ChapterRestoreResult> {
  const { data } = await http.post<ChapterRestoreResult>(
    `/writing/chapters/${chapterId}/restore/${versionId}`,
  );
  return data;
}

// ---------------------------------------------------------------------------
// AI 写作辅助（续写 + 结构建议）
// ---------------------------------------------------------------------------

/**
 * 智能续写（SSE 流式）。
 * 通过 fetch 读取 text/event-stream，逐 token 回调 onToken。
 * 流结束调用 onDone，出错调用 onError。
 */
export async function continueWritingStream(
  chapterId: number,
  direction: string,
  onToken: (text: string) => void,
  onDone: () => void,
  onError: (msg: string) => void,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${SSE_BASE}/writing/chapters/${chapterId}/continue`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify({ direction }),
    });
  } catch {
    onError("网络请求失败，请检查后端是否运行");
    return;
  }

  if (!resp.ok || !resp.body) {
    onError(`请求失败（${resp.status}）`);
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 以 \n\n 分隔事件
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        try {
          const payload = JSON.parse(line.slice(6));
          if (payload.type === "token") onToken(payload.data);
          else if (payload.type === "error") onError(payload.data);
          else if (payload.type === "done") onDone();
        } catch {
          // 忽略解析失败的行
        }
      }
    }
  } catch {
    onError("流式读取中断");
  }
}

/** 结构建议：分析当前章节内容与大纲，返回调整建议 */
export async function suggestStructure(chapterId: number): Promise<StructureSuggestion> {
  const { data } = await http.post<StructureSuggestion>(
    `/writing/chapters/${chapterId}/suggest-structure`,
  );
  return data;
}

/** C1：全文改写 —— 基于章节上下文润色选中的文本（非流式） */
export async function rewriteText(chapterId: number, text: string): Promise<RewriteResult> {
  const { data } = await http.post<RewriteResult>(
    `/writing/chapters/${chapterId}/rewrite`,
    { text },
    // 改写可能耗时较长，给 120s
  );
  return data;
}

/** C2：获取章节续写历史（按时间倒序，最多 10 条） */
export async function fetchContinuations(chapterId: number): Promise<ContinuationRecord[]> {
  const { data } = await http.get<ContinuationRecord[]>(
    `/writing/chapters/${chapterId}/continuations`,
  );
  return data;
}

/** C2：保存一条续写历史（续写成功后自动调用） */
export async function saveContinuation(
  chapterId: number,
  content: string,
  direction: string,
): Promise<ContinuationRecord> {
  const { data } = await http.post<ContinuationRecord>(
    `/writing/chapters/${chapterId}/continuations`,
    { content, direction },
  );
  return data;
}

/** C3：采纳结构建议 —— 将当前章节移动到目标章节下 */
export async function applySuggestion(
  chapterId: number,
  targetChapterId: number,
): Promise<ApplySuggestionResult> {
  const { data } = await http.post<ApplySuggestionResult>(
    `/writing/chapters/${chapterId}/apply-suggestion`,
    { target_chapter_id: targetChapterId },
  );
  return data;
}

/** 引用智能推荐：基于章节内容检索相关论文 */
export async function recommendCitations(
  chapterId: number,
  content: string,
): Promise<RecommendCitationsResponse> {
  const { data } = await http.post<RecommendCitationsResponse>(
    `/writing/chapters/${chapterId}/recommend-citations`,
    { content },
  );
  return data;
}

// ---------------------------------------------------------------------------
// 学术诚信护栏（#1 来源标记 / #2 引用防伪造）
// ---------------------------------------------------------------------------

/** 护栏 #2：校验章节正文 [@paper_id] 引用是否命中真实论文库，返回疑似伪造列表 */
export interface CitationValidation {
  chapter_id: number;
  cited: string[];
  valid: string[];
  invalid: string[];
  has_invalid: boolean;
}

export async function validateChapterCitations(chapterId: number): Promise<CitationValidation> {
  const { data } = await http.get<CitationValidation>(
    `/writing/chapters/${chapterId}/validate-citations`,
  );
  return data;
}

/** 护栏 #2：校验传入的实时文本（含 AI 刚插入、未落库内容）引用真实性 */
export async function validateChapterCitationsText(
  chapterId: number,
  content: string,
): Promise<CitationValidation> {
  const { data } = await http.post<CitationValidation>(
    `/writing/chapters/${chapterId}/validate-citations`,
    { content },
  );
  return data;
}

/** 护栏 #1：返回项目 AI 辅助使用量，供导出前预览「AI 使用声明」 */
export interface ProjectAiUsage {
  has_ai: boolean;
  continuation_count: number;
  rewrite_count: number;
  ai_chars: number;
  chapters_with_ai: number;
}

export async function getProjectAiUsage(projectId: number): Promise<ProjectAiUsage> {
  const { data } = await http.get<ProjectAiUsage>(`/writing/projects/${projectId}/ai-usage`);
  return data;
}

/**
 * P1：自动生成大纲（SSE 流式）。
 * 通过 fetch 读取 text/event-stream：
 *   - chunk  事件 → onChunk(text) 展示增量内容
 *   - done   事件 → onDone(outline) 返回完整大纲结构
 *   - error  事件 → onError(msg) 展示错误
 * 同时处理 HTTP 503（LLM 不可用）/ 404（项目不存在）等非流式错误。
 */
export async function generateOutline(
  payload: GenerateOutlineRequest,
  onChunk: (text: string) => void,
  onDone: (outline: OutlineNode[]) => void,
  onError: (msg: string) => void,
): Promise<void> {
  let resp: Response;
  try {
    resp = await fetch(`${SSE_BASE}/writing/generate-outline`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify(payload),
    });
  } catch {
    onError("网络请求失败，请检查后端是否运行");
    return;
  }

  if (!resp.ok || !resp.body) {
    if (resp.status === 503) {
      onError("LLM 服务不可用，请先在「模型管理」中配置并启用模型");
    } else if (resp.status === 404) {
      onError("目标项目不存在");
    } else {
      onError(`请求失败（${resp.status}）`);
    }
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 以 \n\n 分隔事件
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        try {
          const evt = JSON.parse(line.slice(6));
          if (evt.type === "chunk") onChunk(evt.content);
          else if (evt.type === "done") onDone(evt.outline ?? []);
          else if (evt.type === "error") onError(evt.message ?? "生成失败");
        } catch {
          // 忽略解析失败的行
        }
      }
    }
  } catch {
    onError("流式读取中断");
  }
}
