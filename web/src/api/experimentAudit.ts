/**
 * 论文实验审计（experiment-audit）API 客户端。
 *
 * 后端：mock_api/routers/experiment_audit.py
 * - 审计任务异步执行（TaskManager），前端轮询 result 端点获取状态；
 * - leakage 为同步端点，建议小样本集使用。
 */
import { createHttpClient, API_BASE, getAuthHeaders } from "./client";

const auditHttp = createHttpClient({ timeout: 120_000, defaultMessage: "审计请求失败" });

// ---- 类型 ----

export interface AuditEvidenceSource {
  type: "table" | "figure" | "text";
  table_id?: string | null;
  figure_id?: string | null;
  page?: number | null;
  snippet?: string | null;
}

export interface AuditFinding {
  finding_id: string;
  type: string;
  severity: "high" | "medium" | "low";
  title: string;
  page?: number | null;
  bbox?: number[] | null;
  claim?: string | null;
  computed?: string | null;
  tolerance?: number | null;
  method?: string;
  evidence_sources?: AuditEvidenceSource[];
  normal_explanation?: string;
  needs_human_review?: boolean;
}

export interface AuditCheckRun {
  check: string;
  status: "ok" | "failed" | "skipped";
  duration_ms?: number;
  findings?: number;
  reason?: string;
}

export interface AuditResult {
  audit_id: string;
  paper_id: string;
  paper_title: string;
  status: "pending" | "running" | "completed" | "failed";
  source_pdf_hash: string;
  findings: AuditFinding[];
  checks_run: AuditCheckRun[];
  error_message?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

export interface AuditListItem {
  audit_id: string;
  paper_id: string;
  paper_title: string;
  status: string;
  findings_count: number;
  created_at?: string | null;
}

export interface FindingTypeMeta {
  type: string;
  severity: string;
  description: string;
  example: string;
  check: string;
}

export interface LeakageResponse {
  findings: AuditFinding[];
  findings_count: number;
}

// ---- 端点 ----

/** 提交异步审计任务 */
export async function startExperimentAudit(
  paperId: string,
  checks?: string[],
): Promise<{ task_id: string; paper_id: string }> {
  const { data } = await auditHttp.post(`/experiment-audit/run/${paperId}`, {
    checks: checks ?? null,
  });
  return data;
}

/** 查询论文最新审计结果（404 表示尚无记录） */
export async function getExperimentAuditResult(paperId: string): Promise<AuditResult> {
  const { data } = await auditHttp.get<AuditResult>(
    `/experiment-audit/result/${paperId}`,
    { skipErrorToast: true },
  );
  return data;
}

/** 审计历史列表 */
export async function listExperimentAudits(
  limit = 20,
  offset = 0,
): Promise<{ total: number; items: AuditListItem[] }> {
  const { data } = await auditHttp.get("/experiment-audit/list", {
    params: { limit, offset },
  });
  return data;
}

/** P0-7 数据泄漏初筛（同步） */
export async function runLeakageCheck(payload: {
  train_dir: string;
  test_dir: string;
  exact_hash?: boolean;
  phash_threshold?: number;
}): Promise<LeakageResponse> {
  const { data } = await auditHttp.post<LeakageResponse>("/experiment-audit/leakage", payload);
  return data;
}

/** 10 种 Finding 类型目录 */
export async function getFindingTypes(): Promise<FindingTypeMeta[]> {
  const { data } = await auditHttp.get<FindingTypeMeta[]>("/experiment-audit/finding-types");
  return data;
}

/** HTML 报告地址（新标签打开，浏览器可存 PDF） */
export function auditReportUrl(auditId: string): string {
  return `${API_BASE}/experiment-audit/report/${auditId}`;
}

/** 报告带鉴权头拉取（供 blob 下载） */
export async function fetchAuditReportHtml(auditId: string): Promise<string> {
  const resp = await fetch(auditReportUrl(auditId), { headers: getAuthHeaders() });
  if (!resp.ok) throw new Error(`报告加载失败: ${resp.status}`);
  return resp.text();
}
