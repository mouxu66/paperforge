/**
 * DEPTH reflection 报告评审 API 客户端。
 *
 * 与 v4.1 paper review 不同：
 * - reflection 是单次 LLM 调用（轻量 pipeline），不要求论文有 PDF/全文
 * - POST /text 返回 202 + taskId，需通过 useTaskStore.subscribeSSE 订阅进度
 * - GET /list 按 kind='report' 过滤（kind 字段在 unified/list 中可显式指定）
 *
 * 客户端分层（两套实例）：
 * - reflectionHttp——300s / 5min，路径：单文件上传 / 创建感悟报告 / 重跑评审 /
 *   列表 / 结果查询（几乎都是简短 LLM 调用 + 中量查询）。
 * - reflectionBatchHttp——600s / 10min，路径：多文件批量上传（uploadReflectionReports）。
 *   抽换路径下 N>10 个扫描版 PDF 走 OCR 降级，总量可达分钟级，300s 不够。
 *
 * ⚠️ 所有「创建 / 重跑」类接口都是异步的：服务端返回 202 Accepted + taskId，
 * 必须通过 useTaskStore.subscribeSSE 订阅进度，调用方不能期待 await 拿到最终结果。
 */
import { createHttpClient } from "./client";
import { batchDeleteReviews } from "./depth";
import type {
  AnalysisV2,
  BatchDeleteResponse,
  FidelityAnchor,
  ReflectionClaim,
  ReflectionCreateResponse,
  ReflectionEvidenceItem,
  ReflectionFileBatchItem,
  ReflectionFileBatchResponse,
  ReflectionListItem,
  ReflectionListResponse,
  ReflectionResult,
  ReflectionResultResponse,
  ReflectionRunResponse,
  ReflectionScores,
  ReflectionUploadRequest,
  ReflectionVerdict,
  SourcePaperInfo,
  UnifiedReviewItem,
  UnifiedReviewListResponse,
} from "./types";

// Re-export 所有 reflection 相关类型，方便组件从 '@/api/reflection' 一处导入
export type {
  AnalysisV2,
  FidelityAnchor,
  ReflectionClaim,
  ReflectionEvidenceItem,
  ReflectionFileBatchItem,
  ReflectionFileBatchResponse,
  ReflectionScores,
  ReflectionVerdict,
  ReflectionResult,
  ReflectionListItem,
  ReflectionListResponse,
  ReflectionResultResponse,
  ReflectionUploadRequest,
  ReflectionCreateResponse,
  ReflectionRunResponse,
  SourcePaperInfo,
  UnifiedReviewItem,
  UnifiedReviewListResponse,
};

const reflectionHttp = createHttpClient({ timeout: 300_000, defaultMessage: "感悟评审请求失败" });
// 批文件上传专用——N>10 个扫描版 PDF + OCR 降级场景下需越过 300s 基线。
const reflectionBatchHttp = createHttpClient({
  timeout: 600_000,
  defaultMessage: "批量上传请求失败",
});

/**
 * 创建感悟报告并触发轻量 reflection 评审。
 *
 * ⚠️ 异步：服务端 HTTP 202 + taskId。HTTP 调用在 O(100ms) 内返回，
 * 真正的 LLM 评审（单次调用 + 评分 + 写库）在 TaskManager 后台 worker 上执行。
 * 必须通过 useTaskStore.subscribeSSE(taskId) 订阅进度；调用方不应凭本返回值
 * 判定评审完成。
 *
 * 失败时 axios 拦截器会自动 message.error 并 reject。
 */
export async function createReflectionReport(
  payload: ReflectionUploadRequest,
): Promise<ReflectionCreateResponse> {
  const { data } = await reflectionHttp.post<ReflectionCreateResponse>(
    "/depth/reflection/text",
    payload,
  );
  return data;
}

/**
 * 上传文件创建感悟报告（PDF / DOCX / TXT / MD）。
 *
 * 走与 /text 完全相同的 reflection pipeline：
 * 1. 后端按扩展名分发抽取（PDF → pypdf, DOCX → python-docx, 文本类 → UTF-8 解码）
 * 2. 抽取结果与 /text 一致：sha1(content)[:12] 生成 paper_id，写 papers + FTS5
 * 3. TaskManager 提交 reflection_review → 返回 taskId 走 SSE/轮询
 *
 * ⚠️ 异步：返回 202 + taskId，需要 useTaskStore.subscribeSSE 订阅完成事件。
 * HTTP 调用本身很快，耗时发生在后端入库 + 单次 LLM 调用阶段。
 *
 * 表单字段：title / authorsCsv / year / sourcePaperId 均可选；后端从文件 metadata 兜底。
 * 300s 超时：单 PDF 解析 + 入库 + 任务调度通常 < 10s，留 ~290s buffer 给
 * 慢 LLM 网关 / OCR 降级路径。
 *
 * 上传进度：通过 onUploadProgress 回调向调用方报告 0–95% 的实时上传进度。
 * 0–95% 由 axios XHR 的 progress 事件驱动；95–100% 留给服务端处理（入库 + LLM 评审），
 * 组件层在 axios promise resolve 后手动设 95%，再让 SSE 进度事件接管剩余 5%。
 * 之所以封顶 95%：避免在服务端未响应时已显示「上传完成」造成误导。
 */
export async function uploadReflectionReport(
  file: File,
  meta?: {
    title?: string;
    authorsCsv?: string;
    year?: number | null;
    sourcePaperId?: string | null;
  },
  onUploadProgress?: (percent: number) => void,
): Promise<ReflectionCreateResponse> {
  const form = new FormData();
  form.append("file", file, file.name);
  if (meta?.title) form.append("title", meta.title);
  if (meta?.authorsCsv) form.append("authors_csv", meta.authorsCsv);
  if (meta?.year) form.append("year", String(meta.year));
  if (meta?.sourcePaperId) form.append("sourcePaperId", meta.sourcePaperId);
  const { data } = await reflectionHttp.post<ReflectionCreateResponse>(
    "/depth/reflection/file",
    form,
    {
      // multipart 上传：必须让 axios 自动设置 Content-Type（含 boundary）
      headers: { "Content-Type": "multipart/form-data" },
      // 透传 XHR 上传进度事件。axios 在底层 XHR 的 progress 事件中传入
      // { loaded, total, bytes?: number }，由调用方决定如何显示。
      onUploadProgress: onUploadProgress
        ? (event) => {
            if (event.total && event.total > 0) {
              const percent = Math.round((event.loaded / event.total) * 100);
              onUploadProgress(Math.min(percent, 95));
            }
          }
        : undefined,
    },
  );
  return data;
}

/**
 * 批量上传感悟报告（多文件 picker）。
 *
 * 走与单文件版 /file 完全相同的 reflection pipeline（_process_one_reflection_file helper）：
 * 1. 后端逐个文件按扩展名抽取 + 去重 + 入库 + FTS5
 * 2. TaskManager 提交 reflection_review → 返回 taskId 走 SSE/轮询
 * 3. 单文件失败不影响其他文件，items 列表中独立显示失败状态
 *
 * ⚠️ 异步批提交：返回 202 + items 列表（每条目包含独立 taskId）。总 HTTP 耗时
 * 取决于 N 个文件的服务端抽取时间（DOCX 表抽取 + PDF OCR 降级都可能分钟级），
 * **调用方不可期待 await 拿到最终评分**，应通过 useTaskStore.subscribeSSE
 * 订阅各 taskId 的完成事件，或在 items 中读到 status field 更新后再 refresh 列表。
 *
 * 表单字段 title / authorsCsv / year 是所有文件共享的元数据（不能按文件区分）。
 * 如需逐文件自定义元数据，请改用 uploadReflectionReport() 循环调用。
 *
 * onUploadProgress: 整体上传进度（0-95%），95-100% 留给服务端处理。
 */
export async function uploadReflectionReports(
  files: File[],
  meta?: {
    title?: string;
    authorsCsv?: string;
    year?: number | null;
    sourcePaperId?: string | null;
  },
  onUploadProgress?: (percent: number) => void,
): Promise<ReflectionFileBatchResponse> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f, f.name);
  }
  if (meta?.title) form.append("title", meta.title);
  if (meta?.authorsCsv) form.append("authors_csv", meta.authorsCsv);
  if (meta?.year) form.append("year", String(meta.year));
  if (meta?.sourcePaperId) form.append("sourcePaperId", meta.sourcePaperId);
  const { data } = await reflectionBatchHttp.post<ReflectionFileBatchResponse>(
    "/depth/reflection/files",
    form,
    {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: onUploadProgress
        ? (event) => {
            if (event.total && event.total > 0) {
              const percent = Math.round((event.loaded / event.total) * 100);
              onUploadProgress(Math.min(percent, 95));
            }
          }
        : undefined,
    },
  );
  return data;
}

/**
 * 手动重跑 reflection 评审（针对已存在的 report 论文）。
 *
 * ⚠️ 异步：与 createReflectionReport 区别在于「不创建新论文」，仅重跑评审。
 * 返回 taskId 必须通过 useTaskStore.subscribeSSE 订阅。HTTP 409 表示已有
 * 进行中的任务（dedup 写一致保护）。
 */
export async function startReflectionReview(paperId: string): Promise<ReflectionRunResponse> {
  const { data } = await reflectionHttp.post<ReflectionRunResponse>(
    `/depth/reflection/run/${paperId}`,
  );
  return data;
}

/**
 * 分页列出 reflection 评审记录（按 average 评分降序）。
 */
export async function listReflectionReviews(
  limit = 20,
  offset = 0,
): Promise<ReflectionListResponse> {
  const { data } = await reflectionHttp.get<ReflectionListResponse>("/depth/reflection/list", {
    params: { limit, offset },
  });
  return data;
}

/**
 * 查询论文最新一条 reflection 评审完整结果（含 claims/evidence_pool/summary）。
 */
export async function getReflectionResult(paperId: string): Promise<ReflectionResultResponse> {
  const { data } = await reflectionHttp.get<ReflectionResultResponse>(
    `/depth/reflection/result/${paperId}`,
  );
  return data;
}

/**
 * 删除单条 reflection 评审记录（复用统一的 depth/review 删除端点）。
 */
export async function deleteReflectionReview(
  reviewId: string,
): Promise<{ success: boolean; deleted_id: string }> {
  const { data } = await reflectionHttp.delete<{ success: boolean; deleted_id: string }>(
    `/depth/review/${reviewId}`,
  );
  return data;
}

/**
 * 批量删除 reflection 评审记录（与 DELETE 单条复用同一个后端端点）。
 *
 * 后端一次性提交 review_ids 列表，单事务删除，避免逐条 delete 引发的 SQLite
 * 长事务 / 日志频繁切换。响应包含 deleted_count + failed_ids，便于前端展示分项
 * 结果（部分失败时不弹错误，但仍 Toast 告知）。
 */
export async function batchDeleteReflectionReviews(
  reviewIds: string[],
): Promise<BatchDeleteResponse> {
  return batchDeleteReviews(reviewIds, "review_ids");
}

/**
 * 统一列出 DEPTH 评审记录（按 kind 过滤）。
 *
 * - kind='paper'  → V4.1 九节点 DAG 评审
 * - kind='report' → reflection 轻量 pipeline 评审
 * - kind='all'    → 全部
 */
export async function listUnifiedReviews(
  kind: "all" | "paper" | "report" = "all",
  limit = 20,
  offset = 0,
): Promise<UnifiedReviewListResponse> {
  const { data } = await reflectionHttp.get<UnifiedReviewListResponse>("/depth/unified/list", {
    params: { kind, limit, offset },
  });
  return data;
}
