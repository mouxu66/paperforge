/**
 * DEPTH v4.1 论文深度审稿 API 客户端（统一 600s / 10min 超时）。
 *
 * 设计原则：
 * - 所有 depth / v4 端点共享同一 depthHttp，不被任何 15s 默认值限定。
 * - 批提交路径（submitDepthBatch / startBatchV4Review / startSelectedV4Review）
 *   单次 HTTP 请求本身极快返回（任务走 TaskManager 后台 worker），
 *   600s 主要是为下游可选的 pollDepthStatus 提供足够窗口，让轮询遇慢 worker
 *   不被快速 reject。
 * - 查询侧（listDepthV4Papers / listDepthV4Reviews 等聚合查询）在论文量大时
 *   Python 侧聚合 + 去重 + 排序耗时可为分钟级，同样受益于 600s。
 * - 单次审稿（startDepthV4Review）也走同一客户端，避免批量页里被误杀。
 * - v3 端点（submitDepthBatch / getDepthTaskStatus / pollDepthStatus）原本走默认
 *   15s 客户端，迁到 depthHttp 后使用一致 600s。
 *
 * 异步契约：
 * - 所有“提交”类接口都是异步的：通过 task_id 在 useTaskStore.subscribeSSE 订阅，
 *   或自行 GET /depth/status/{task_id} 轮询。
 * - HTTP 响应本身极快（O(100ms)），耗时发生在后台 worker；600s 是请求级超时兜底，
 *   并非任务完成时限。
 */
import { createHttpClient } from "./client";
import type { BatchDeleteResponse, DepthBatchResponse, DepthTaskStatus } from "./types";

const depthHttp = createHttpClient({ timeout: 600_000, defaultMessage: "审稿请求失败" });

// ---- v4.1 类型 ----

export interface EvidenceItem {
  id: string;
  content: string;
  contentZh?: string;
  contentEn?: string;
  section: string;
  keywords: string[];
  severity?: "fatal" | "minor";
}

export interface CritiquePointV4 {
  point: string;
  severity: "fatal" | "minor";
}

export interface FinalVerdictV4 {
  final_verdict: string;
  calibrated_score: number;
  override_reason: string;
  llm_verdict: string;
  base_score: number;
  weights: Record<string, number>;
  evidence_checks: Record<string, boolean>;
  node_score_stds?: Record<string, number>;
  // v4.2 图文一致性（QF）结果
  figure_coverage?: "analyzed" | "missing" | "disabled";
  figure_consistency_score?: number;
  figure_flags?: string[];
  figure_evidence_count?: number;
  qf_reasoning?: string;
  // M0 矢量渲染开关：由服务端 /api/depth/v4/result/{paper_id} 注入
  m0_active?: boolean;
}

export interface DepthReviewV4Result {
  id: string;
  paper_id: string;
  status: "pending" | "running" | "completed" | "failed" | "timed_out";
  q0_result: {
    has_substance: boolean;
    expectation: number;
    reasoning: string;
    evidence: string;
  } | null;
  q1_result: {
    type: string;
    secondary_type: string;
    confidence: number;
    reasoning: string;
  } | null;
  evidence_pool: EvidenceItem[] | null;
  q2_result: {
    novelty_score: number;
    hotspot_alignment_score: number;
    core_contribution: string;
    reasoning: string;
    evidence_id: string;
  } | null;
  q3_result: {
    rigor_score: number;
    missing_items: string[];
    reasoning: string;
  } | null;
  q4_result: {
    influence_score: number;
    reproducibility_score: number;
    reasoning: string;
  } | null;
  q5a_result: {
    critique_points: CritiquePointV4[];
  } | null;
  q5b_result: {
    defense_points: string[];
  } | null;
  q5c_result: {
    reasoning: string;
    calibrated_score: number;
    delta: number;
    delta_missing: boolean;
    llm_verdict: string;
  } | null;
  final_verdict: FinalVerdictV4 | null;
  /** v4.1 适配层：calibrated_score × 100，与 DepthRadar 头条分口径一致 */
  final_score: number | null;
  /** 各节点采样标准差（key 示例：Q2:novelty_score / Q5c） */
  node_score_stds: Record<string, number>;
  error_message: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface DepthReviewV4ListItem {
  id: string;
  paper_id: string;
  paper_title: string;
  status: string;
  novelty_score: number | null;
  hotspot_alignment_score: number | null;
  core_contribution: string | null;
  final_verdict: string | null;
  created_at: string | null;
  completed_at: string | null;
}

export interface DepthReviewV4ListResponse {
  items: DepthReviewV4ListItem[];
  total: number;
  limit: number;
  offset: number;
}

// ---- v4.1 按论文聚合的列表项（去重重复重审记录） ----

export interface DepthReviewV4PaperItem {
  paper_id: string;
  paper_title: string;
  total_reviews: number;
  completed_count: number;
  failed_count: number;
  pending_count: number;
  timed_out_count: number;
  latest_status: "pending" | "running" | "completed" | "failed" | "timed_out";
  latest_record_id: string;
  // 最新一次**已完成**审稿的得分/裁决（若从未完成则为 null）。
  latest_novelty_score: number | null;
  latest_hotspot_score: number | null;
  latest_verdict: string | null;
  // 「失败但尚未成功」信号 → 前端可用于标红「需要重审」
  has_unresolved_failure: boolean;
  last_attempted_at: string | null;
  last_completed_at: string | null;
}

export interface DepthReviewV4PaperListResponse {
  items: DepthReviewV4PaperItem[];
  total: number;
  limit: number;
  offset: number;
}

// ---- v3 API（同样走 depthHttp，避免轮询退到 15s 被推后退）

/**
 * 批量提交 DEPTH 评估。
 * @remarks 异步批提交；返回 {task_id, total_papers}。真正评审在后台 worker，
 * 用 pollDepthStatus / useTaskStore.subscribeSSE 跟踪进度，**不要 await 拿结果**。
 */
export async function submitDepthBatch(paperIds: string[]): Promise<DepthBatchResponse> {
  const { data } = await depthHttp.post<DepthBatchResponse>("/depth/batch", {
    paper_ids: paperIds,
  });
  return data;
}

/**
 * 查询 DEPTH 任务状态（轮询一次）。
 * @remarks 由 pollDepthStatus 以 3s 间隔调用；走 depthHttp 以容忍单次较慢状态查询。
 */
export async function getDepthTaskStatus(
  taskId: string,
  options?: { signal?: AbortSignal },
): Promise<DepthTaskStatus> {
  const { data } = await depthHttp.get<DepthTaskStatus>(`/depth/status/${taskId}`, {
    signal: options?.signal,
  });
  return data;
}

/**
 * 轮询批量任务进度，直到完成。
 * @remarks 返回的 Promise 仅在 status='completed' 时 resolve；遇任何网络错调的 reject。
 * 依赖 getDepthTaskStatus 以 depthHttp 调用，单次轮询限 600s。
 */
export async function pollDepthStatus(
  taskId: string,
  onProgress?: (current: number, total: number) => void,
  options?: { signal?: AbortSignal },
): Promise<DepthTaskStatus> {
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let settled = false;

    const abortError = () => {
      const error = new Error("DEPTH 任务轮询已取消");
      error.name = "AbortError";
      return error;
    };

    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      options?.signal?.removeEventListener("abort", onAbort);
      callback();
    };

    const onAbort = () => finish(() => reject(abortError()));

    if (options?.signal?.aborted) {
      onAbort();
      return;
    }
    options?.signal?.addEventListener("abort", onAbort, { once: true });

    const poll = async () => {
      if (settled || options?.signal?.aborted) return;
      try {
        const data = await getDepthTaskStatus(taskId, { signal: options?.signal });
        if (settled) return;
        onProgress?.(data.progress.current, data.progress.total);

        if (data.status === "completed") {
          finish(() => resolve(data));
          return;
        }
        if (data.status === "failed" || data.status === "timed_out") {
          finish(() =>
            reject(
              new Error(
                data.errors?.[0] ||
                  (data.status === "timed_out" ? "DEPTH 任务超时" : "DEPTH 任务失败"),
              ),
            ),
          );
          return;
        }
        timer = setTimeout(poll, 3000);
      } catch (err0: unknown) {
        if (settled) return;
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        finish(() => reject(e));
      }
    };
    void poll();
  });
}

// ---- v4.1 API ----

/**
 * 提交单篇 DEPTH v4.1 审稿任务。
 *
 * ⚠️ 异步：后台执行 9 节点 DAG，平均 30~60s/篇。返回 {task_id}，需通过
 * useTaskStore.subscribeSSE 或继续调用 getDepthResult() 跟踪结果。
 */
export async function startDepthV4Review(
  paperId: string,
): Promise<{ task_id: string; status: string; paper_id: string; message: string }> {
  const { data } = await depthHttp.post<{
    task_id: string;
    status: string;
    paper_id: string;
    message: string;
  }>(`/depth/v4/review/${paperId}`);
  return data;
}

/** 查询论文最新的 DEPTH v4.1 审稿结果 */
export async function getDepthV4Result(
  paperId: string,
  options?: { signal?: AbortSignal },
): Promise<DepthReviewV4Result> {
  const { data } = await depthHttp.get<DepthReviewV4Result>(`/depth/v4/result/${paperId}`, {
    signal: options?.signal,
  });
  return data;
}

/** 分页列出所有 DEPTH v4.1 审稿记录 */
export async function listDepthV4Reviews(
  limit = 20,
  offset = 0,
): Promise<DepthReviewV4ListResponse> {
  const { data } = await depthHttp.get<DepthReviewV4ListResponse>("/depth/v4/list", {
    params: { limit, offset },
  });
  return data;
}

/** 分页列出 DEPTH v4.1 审稿过的论文（**按论文聚合**，去重重复重审记录）。
 *
 * 与 listDepthV4Reviews 的区别：
 * - listDepthV4Reviews：每次审稿尝试返回一行（同一论文多次重审会显示多条）
 * - listDepthV4Papers：每篇论文仅返回一行聚合数据，适合「xx 深度审稿记录」概览表
 *
 * 适用场景：首页/列表页希望按论文展示最新裁决 + 重审尝试次数 + 失败计数。
 */
export async function listDepthV4Papers(
  limit = 20,
  offset = 0,
): Promise<DepthReviewV4PaperListResponse> {
  const { data } = await depthHttp.get<DepthReviewV4PaperListResponse>("/depth/v4/papers", {
    params: { limit, offset },
  });
  return data;
}

/** 按论文ID批量查询深度评审分数 */
export async function getDepthScoresByPaperIds(
  paperIds: string[],
): Promise<Record<string, { verdict: string | null; novelty_score: number | null }>> {
  if (paperIds.length === 0) return {};
  const { data } = await depthHttp.get<{ scores: Record<string, { verdict: string | null; novelty_score: number | null }> }>(
    "/depth/v4/scores",
    { params: { paper_ids: paperIds.join(",") } },
  );
  return data.scores;
}

/**
 * 全库批量审稿：将所有未审稿论文入 V4.1 审稿队列。
 *
 * ⚠️ 异步批提交：返回 {task_id}（部分场景可能为 null 表示「没有待审稿」）。
 * 实际执行通过 TaskManager → ThreadPoolExecutor 在后台跑，单篇 ~30~60s，
 * N 篇论文需等待 N / 并发数 × 单篇耗时。**务必用 useTaskStore.subscribeSSE
 * 或 GET /status/{task_id} 轮询进度**，不可期待本次请求 await 拿到结果。
 */
export async function startBatchV4Review(): Promise<{
  task_id: string | null;
  message: string;
  total: number;
}> {
  const { data } = await depthHttp.post<{ task_id: string | null; message: string; total: number }>(
    "/depth/v4/review-batch",
  );
  return data;
}

/**
 * 从列表自选论文批量重审（`POST /api/depth/v4/review-selected`）。
 *
 * ⚠️ 异步批提交：返回 {task_ids, submitted, skipped, total} —— 每个 paper_id
 * 一个独立 task_id。跨页勾选 N=50 论文时，已在 pending/running 的论文会被
 * `skipped` 字段列出，submitted 数为真正新开的任务数。调用方应：
 *   1. 展示「提交 X，跳过 Y」的即时反馈（HTTP 200 内极快返回）。
 *   2. 用 useTaskStore.subscribeSSE(task_ids) 订阅每条任务的完成事件，
 *      或在 listDepthV4Papers() 的下一次轮询中观察 latest_status 变化。
 */
export async function startSelectedV4Review(paperIds: string[]): Promise<{
  task_ids: string[];
  submitted: number;
  skipped: { paper_id: string; reason: string }[];
  total: number;
  message: string;
}> {
  const { data } = await depthHttp.post<{
    task_ids: string[];
    submitted: number;
    skipped: { paper_id: string; reason: string }[];
    total: number;
    message: string;
  }>("/depth/v4/review-selected", { paper_ids: paperIds });
  return data;
}

/** 删除单条深度审稿记录（v4.1 / reflection 通用） */
export async function deleteDepthReview(
  reviewId: string,
): Promise<{ success: boolean; deleted_id: string }> {
  const { data } = await depthHttp.delete<{ success: boolean; deleted_id: string }>(
    `/depth/review/${reviewId}`,
  );
  return data;
}

/**
 * 通用批量删除审稿记录。
 *
 * 服务端支持 `paper_ids`（每篇论文最新一条）或 `review_ids`（直接指定记录 ID）。
 * 前端组件按场景选择 wrapper：
 * - batchDeleteDepthReviews(paperIds) → 按论文删除
 * - batchDeleteReflectionReviews(reviewIds) → 按记录 ID 删除
 *
 * 服务端兜底拒绝删除进行中的审稿（409 Conflict），保留 dedup 自愈不变量。
 */
export async function batchDeleteReviews(
  ids: string[],
  idKey: "paper_ids" | "review_ids" = "paper_ids",
): Promise<BatchDeleteResponse> {
  const { data } = await depthHttp.post<BatchDeleteResponse>("/depth/reviews/batch-delete", {
    [idKey]: ids,
  });
  return data;
}

/**
 * 批量删除深度审稿记录（服务端解析 paper_ids → 每篇论文最新一条记录）。
 *
 * 前端只传论文 ID 列表（与表格 rowKey='paper_id' 对齐，不需要先查 latest_record_id），
 * 服务端在 `BatchDeleteReviewRequest.paper_ids` 字段接收后自动解析为每篇论文的
 * 最新审稿记录，再走 savepoint+flush 事务删除逻辑。跨分页选择时仍可正确删除。
 */
export async function batchDeleteDepthReviews(paperIds: string[]): Promise<BatchDeleteResponse> {
  return batchDeleteReviews(paperIds, "paper_ids");
}
