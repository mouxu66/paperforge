/**
 * 感悟报告「AI 使用声明 + 真实性报告」API 客户端。
 *
 * 对应后端 mock_api/routers/reports.py 的两个端点：
 * - GET  /api/reports/{paper_id}/integrity       → JSON 一页纸数据
 * - POST /api/reports/{paper_id}/integrity/export?format=docx|html → 文件流
 *
 * 报告是证据性文档，后端确定性生成；前端只负责预览与下载。
 */
// 用 httpSilent：错误不自动 toast，由 Modal 统一呈现（避免 toast + 行内 Result 双重提示）
import { httpSilent } from "./client";
import { downloadBlob } from "@/utils/export";

/** 后端 build_integrity_report 的 JSON 契约（字段与 mock_api/integrity_report.py 对齐） */
export interface IntegrityReport {
  report_type: string;
  paper_id: string;
  generated_at: string;
  evaluated_at: string;
  student: { id: string; name: string };
  title: string;
  report_chars?: number | null;
  paper_chars?: number | null;
  source_paper: {
    paper_id: string;
    title: string;
    author: string;
    year: number;
    source: string;
  } | null;
  ai_use: {
    mode: string;
    provenance_available: boolean;
    declaration_text: string;
    advisory: {
      ai_likelihood: number;
      tier: string;
      tier_label: string;
      signals: Record<string, unknown>;
      note: string;
    } | null;
    advisory_note: string;
  };
  authenticity: {
    fidelity: number | null;
    fidelity_status: string;
    coverage: number | null;
    coverage_status: string;
    coverage_covered: string[];
    coverage_uncovered: string[];
    copy_ratio: number | null;
    copy_sentences: string[];
    stray_claims: string[];
    anchors: string[];
    sections_present: string[];
  };
  scoring: {
    scores: Record<string, number | null>;
    weights: Record<string, number>;
    average: number | null;
    verdict: string;
    verdict_label: string;
    verdict_reason: string;
    trusted: boolean;
    trust_warnings: string[];
    hardcoded_overrides: string[];
    effective_evidence_count: number | null;
    evidence_rejections: Record<string, number>;
  };
  comments: unknown;
}

/** 拉取单篇报告的 JSON 数据（供一页纸预览） */
export async function getIntegrityReport(paperId: string): Promise<IntegrityReport> {
  const { data } = await httpSilent.get<IntegrityReport>(
    `/reports/${encodeURIComponent(paperId)}/integrity`,
  );
  return data;
}

/** 从 Content-Disposition 解析服务端文件名（filename*=UTF-8'' 优先，回退 filename） */
function parseContentDisposition(cd: string, fallback: string): string {
  const star = cd.match(/filename\*=UTF-8''([^;]+)/i);
  const plain = cd.match(/filename="?([^";]+)"?/i);
  if (star) {
    try {
      return decodeURIComponent(star[1]);
    } catch {
      // 非法百分号编码 → 回退明文 filename
    }
  }
  return plain ? plain[1] : fallback;
}

/** 拉取导出文件流，返回 (blob, 服务端建议的文件名) */
export async function fetchIntegrityExport(
  paperId: string,
  format: "docx" | "html",
): Promise<{ blob: Blob; filename: string }> {
  const { data, headers } = await httpSilent.post(
    `/reports/${encodeURIComponent(paperId)}/integrity/export`,
    null,
    {
      params: { format },
      responseType: "blob",
    },
  );
  const cd = String(headers?.["content-disposition"] || "");
  const filename = parseContentDisposition(
    cd,
    `${paperId}_诚信报告.${format === "html" ? "html" : "docx"}`,
  );
  return { blob: data as Blob, filename };
}

/** 下载 docx 版诚信报告 */
export async function downloadIntegrityDocx(paperId: string): Promise<void> {
  const { blob, filename } = await fetchIntegrityExport(paperId, "docx");
  downloadBlob(blob, filename);
}

/** 拉取打印版 HTML（供新窗口打印 / 存 PDF） */
export async function fetchIntegrityHtml(paperId: string): Promise<string> {
  const { blob } = await fetchIntegrityExport(paperId, "html");
  return blob.text();
}

// ============================================================================
// 全班批量导出（zip，每生一份 docx）—— 复用后端 export_tasks 异步任务基建
// 流程：start → 轮询 progress（最多 5 分钟）→ 完成后 download 并自动下载
// ============================================================================

/** 启动全班批量打包，返回 task_id */
export async function startIntegrityBatchExport(): Promise<{ task_id: string; status: string }> {
  const { data } = await httpSilent.post<{ task_id: string; status: string }>(
    "/reports/integrity/export-batch",
  );
  return data;
}

/** 批量导出任务进度（status: pending | running | done | error，progress 0-100） */
export async function getIntegrityBatchProgress(taskId: string): Promise<{
  taskId: string;
  progress: number;
  status: "pending" | "running" | "done" | "error";
  error: string;
}> {
  const { data } = await httpSilent.get<{
    taskId: string;
    progress: number;
    status: "pending" | "running" | "done" | "error";
    error: string;
  }>(`/reports/integrity/export-batch/${encodeURIComponent(taskId)}/progress`);
  return data;
}

/** 轮询直到打包完成，返回 zip 文件流 */
export async function pollIntegrityBatchZip(
  taskId: string,
  onProgress?: (percent: number) => void,
  timeoutMs = 300_000,
): Promise<{ blob: Blob; filename: string }> {
  const deadline = Date.now() + timeoutMs;
  let task = await getIntegrityBatchProgress(taskId);
  while (task.status !== "done" && task.status !== "error") {
    if (Date.now() > deadline) {
      throw new Error("批量导出超时，请重试");
    }
    onProgress?.(task.progress);
    await new Promise((r) => setTimeout(r, 1000));
    task = await getIntegrityBatchProgress(taskId);
  }
  if (task.status === "error") {
    throw new Error(task.error || "批量导出失败");
  }
  onProgress?.(100);
  const { data, headers } = await httpSilent.get(
    `/reports/integrity/export-batch/${encodeURIComponent(taskId)}/download`,
    { responseType: "blob" },
  );
  const cd = String(headers?.["content-disposition"] || "");
  const filename = parseContentDisposition(cd, `诚信报告_全班.zip`);
  return { blob: data as Blob, filename };
}

/** 一键导出全班诚信报告并下载 zip（启动任务 → 轮询 → 自动下载） */
export async function downloadIntegrityBatchZip(
  onProgress?: (percent: number) => void,
): Promise<void> {
  const { task_id } = await startIntegrityBatchExport();
  const { blob, filename } = await pollIntegrityBatchZip(task_id, onProgress);
  downloadBlob(blob, filename);
}
