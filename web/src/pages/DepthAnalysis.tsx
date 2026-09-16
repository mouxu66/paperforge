import { Radar, Zap, Trophy } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AriaAttributes } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, Link } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Empty,
  Progress,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
  App,
} from "antd";
import type { CheckboxProps } from "antd";

import type { DepthScore, Paper } from "@/api/types";
import { fetchPapers } from "@/api/papers";
import { pollDepthStatus, submitDepthBatch } from "@/api/depth";
import { API_BASE, getAuthHeaders } from "@/api/client";
import { useDepthStore } from "@/store/useDepthStore";
import DepthRadar from "@/components/DepthRadar";

const TYPE_COLORS: Record<string, string> = {
  A: "red",
  B: "blue",
  C: "green",
  D: "orange",
};

const VERDICT_COLORS: Record<string, string> = {
  accept: "green",
  minor_revision: "blue",
  major_revision: "orange",
  reject: "red",
};

interface VerifiedDepthTaskResponse {
  status?: string;
  error?: string;
  result?: {
    results?: DepthScore[];
    total?: number;
  };
}

function isDepthScoreResult(value: unknown): value is DepthScore {
  if (typeof value !== "object" || value === null) return false;
  const score = value as Partial<DepthScore>;
  return typeof score.paper_id === "string" && typeof score.final_score === "number";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseVerifiedResults(value: unknown): DepthScore[] {
  // 保留服务端返回的失败条目，让结果区仍能显示「失败数量」提示；
  // validResults 会在渲染前过滤掉没有 final_score 的条目。
  return Array.isArray(value)
    ? value.filter(isRecord).map((item) => item as unknown as DepthScore)
    : [];
}

function calculateDepthSummary(results: DepthScore[], reportedTotal?: number) {
  const scores = results.filter(isDepthScoreResult);
  const total = reportedTotal ?? results.length;
  return {
    total,
    completed: scores.length,
    failed: Math.max(0, total - scores.length),
    highest_score: scores.length ? Math.max(...scores.map((result) => result.final_score)) : 0,
    average_score: scores.length
      ? scores.reduce((sum, result) => sum + result.final_score, 0) / scores.length
      : 0,
  };
}

function isTerminalDepthStatus(status: string | undefined): boolean {
  return status === "completed" || status === "failed" || status === "timed_out";
}

export default function DepthAnalysis() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const location = useLocation();
  const initialIds = useMemo(() => {
    const state = location.state as { selectedIds?: string[] } | null;
    return new Set<string>(state?.selectedIds || []);
  }, [location.state]);

  const [allPapers, setAllPapers] = useState<Paper[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(initialIds);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [total, setTotal] = useState(0);

  const { addTask, updateProgress, completeTask, failTask, failTasks, tasks } = useDepthStore();
  const evaluationAbortRef = useRef<AbortController | null>(null);

  // 当前激活的任务（页面当前正在跟踪的任务）
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);

  const activeTask = tasks.find((t) => t.taskId === activeTaskId);
  const evaluating = activeTask?.status === "running";
  const progress = activeTask?.progress ?? null;
  const results = useMemo<DepthScore[]>(() => activeTask?.results ?? [], [activeTask?.results]);

  // Keep a ref to the latest tasks so polling effects don't restart on every store update.
  const tasksRef = useRef(tasks);
  useEffect(() => {
    tasksRef.current = tasks;
  }, [tasks]);
  const errors = activeTask?.errors ?? [];
  const summary = activeTask?.summary ?? null;

  // 页面加载时：检查是否有正在运行的任务，自动恢复轮询
  // 先验证后端任务仍存在，防止恢复已过期的持久化 taskId
  useEffect(() => {
    const runningTasks = tasksRef.current.filter((t) => t.status === "running");
    if (runningTasks.length === 0) return;

    const latest = runningTasks.reduce((a, b) => (a.submittedAt > b.submittedAt ? a : b));
    // 非最新的 running 任务均为上一次会话残留 → 批量标记失败（单次渲染）
    const staleItems = runningTasks
      .filter((t) => t.taskId !== latest.taskId)
      .map((t) => ({ taskId: t.taskId, errors: ["上一次会话的残留任务（服务可能已重启）"] }));
    if (staleItems.length > 0) failTasks(staleItems);

    // 验证最新任务是否仍存活；页面卸载时中止，避免迟到响应回写状态。
    const verificationAbort = new AbortController();
    const verifyAndResume = async () => {
      try {
        const resp = await fetch(`${API_BASE}/tasks/${latest.taskId}`, {
          headers: getAuthHeaders(),
          signal: verificationAbort.signal,
        });
        if (!resp.ok) {
          failTask(latest.taskId, ["任务已失效（服务重启或过期）"]);
          return;
        }
        const data = (await resp.json()) as VerifiedDepthTaskResponse;
        if (isTerminalDepthStatus(data.status)) {
          if (data.status === "completed") {
            const results = parseVerifiedResults(data.result?.results);
            // 从 results 重建 summary（不含 summary 字段时用空占位）
            completeTask(latest.taskId, results, calculateDepthSummary(results, data.result?.total));
          } else {
            failTask(latest.taskId, [data.error || "任务失败"]);
          }
          return;
        }
        setActiveTaskId(latest.taskId);
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        failTask(latest.taskId, ["验证任务状态失败（网络或服务不可用）"]);
      }
    };
    void verifyAndResume();
    // Only re-run when the store action references change; tasks are read from ref.
    return () => verificationAbort.abort();
  }, [failTasks, failTask, completeTask, setActiveTaskId]);

  // 当 activeTaskId 指向运行中任务时，恢复轮询
  useEffect(() => {
    if (!activeTaskId) return;
    const task = tasksRef.current.find((t) => t.taskId === activeTaskId);
    if (!task || task.status !== "running") {
      if (task?.status === "canceled") evaluationAbortRef.current?.abort();
      return;
    }

    const abort = new AbortController();
    evaluationAbortRef.current = abort;

    const resumePolling = async () => {
      try {
        const final = await pollDepthStatus(
          task.taskId,
          (current, total) => {
            updateProgress(task.taskId, current, total);
          },
          { signal: abort.signal },
        );
        completeTask(task.taskId, final.results || [], final.summary);
        message.success(t("depth.completed"));
      } catch (err0: unknown) {
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        if (e?.name === "AbortError") return;
        failTask(task.taskId, [e?.message || t("depth.failed")]);
        message.error(e?.message || t("depth.failed"));
      }
    };

    resumePolling();

    return () => {
      abort.abort();
      if (evaluationAbortRef.current === abort) evaluationAbortRef.current = null;
    };
  }, [activeTaskId, activeTask?.status, updateProgress, completeTask, failTask, t, message]);

  // 加载论文列表（带分页）
  const loadPapers = useCallback(
    async (p: number, ps: number) => {
      setLoading(true);
      try {
        const res = await fetchPapers({ page: p, pageSize: ps });
        setAllPapers(res.items);
        setTotal(res.total);
      } catch {
        message.error(t("depth.loadFailed"));
      } finally {
        setLoading(false);
      }
    },
    [t, message],
  );

  useEffect(() => {
    loadPapers(page, pageSize);
  }, [page, pageSize, loadPapers]);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    const pageIds = allPapers.map((p) => p.id);
    const allSelected = pageIds.every((id) => selectedIds.has(id));
    setSelectedIds((prev) => {
      const next = new Set(prev);
      pageIds.forEach((id) => {
        if (allSelected) next.delete(id);
        else next.add(id);
      });
      return next;
    });
  }, [allPapers, selectedIds]);

  const clearSelection = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const handleEvaluate = useCallback(async () => {
    if (selectedIds.size === 0) {
      message.warning(t("depth.selectAtLeastOne"));
      return;
    }

    try {
      const ids = Array.from(selectedIds);
      const { task_id, total_papers } = await submitDepthBatch(ids);

      // 注册到持久化 store，并保存原始论文 ID 供失败重试
      addTask(task_id, total_papers ?? ids.length, ids);
      setActiveTaskId(task_id);

      // 轮询统一由 activeTaskId effect 管理，避免提交函数和恢复 effect 重复轮询。
      // effect 会在组件卸载或任务取消时通过 AbortController 中止请求。
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error(e?.message || t("depth.failed"));
    }
  }, [selectedIds, t, addTask, message]);

  // 过滤掉带 error 的结果（LLM 调用失败的论文），只展示成功的结果
  const validResults = useMemo(() => {
    return results.filter((r) => r && r.final_score != null);
  }, [results]);

  const ranked = useMemo(() => {
    return [...validResults].sort((a, b) => (b.final_score ?? 0) - (a.final_score ?? 0));
  }, [validResults]);

  const rowSelection = {
    selectedRowKeys: Array.from(selectedIds),
    getCheckboxProps: (record: Paper) =>
      ({
        "aria-label": `选择 ${record.title || record.id}`,
      }) as Partial<CheckboxProps> & AriaAttributes,
    onSelect: (record: Paper) => {
      toggleSelect(record.id);
    },
    onSelectAll: () => {
      toggleSelectAll();
    },
  };

  const paperColumns = [
    {
      title: "论文",
      dataIndex: "title",
      ellipsis: true,
      render: (text: string, record: Paper) => (
        <div>
          <div style={{ fontWeight: 500 }}>{text || "-"}</div>
          <div style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
            {(record.authors || []).slice(0, 3).join(", ")}
            {record.authors && record.authors.length > 3 ? " et al." : ""}
          </div>
        </div>
      ),
    },
    {
      title: "年份",
      dataIndex: "year",
      width: 80,
      render: (year: number) => year || "—",
    },
    {
      title: "来源",
      dataIndex: "source",
      width: 90,
      render: (source: string) => <Tag>{source || "—"}</Tag>,
    },
  ];

  const resultColumns = [
    {
      title: "#",
      width: 48,
      render: (_: unknown, __: DepthScore, idx: number) => <span>{idx + 1}</span>,
    },
    {
      title: t("depth.title"),
      dataIndex: "title",
      ellipsis: true,
      render: (text: string, record: DepthScore) => (
        <div>
          <div style={{ fontWeight: 500 }}>{text || "-"}</div>
          <div style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
            {record.core_contribution || ""}
          </div>
        </div>
      ),
    },
    {
      title: t("depth.type"),
      dataIndex: "type",
      width: 90,
      render: (type: string) => (
        <Tag color={TYPE_COLORS[type] || "default"}>{t(`depth.type${type}`)}</Tag>
      ),
    },
    {
      title: t("depth.finalScore"),
      dataIndex: "final_score",
      width: 110,
      sorter: (a: DepthScore, b: DepthScore) => a.final_score - b.final_score,
      render: (score: number) => {
        const s = score ?? 0;
        return (
          <span
            style={{
              fontWeight: 700,
              color: s >= 80 ? "#52c41a" : s >= 60 ? "#faad14" : "#ff4d4f",
            }}
          >
            {s.toFixed(1)}
          </span>
        );
      },
    },
    {
      title: t("depth.verdict"),
      dataIndex: "verdict",
      width: 100,
      render: (verdict: string) => (
        <Tag color={VERDICT_COLORS[verdict] || "default"}>{t(`depth.verdict_${verdict}`)}</Tag>
      ),
    },
    {
      title: t("depth.dimensions"),
      width: 280,
      render: (_: unknown, record: DepthScore) => (
        <Space size="small" wrap>
          <Tag>
            {t("depth.noveltyShort")} {((record.novelty_score ?? 0) * 100).toFixed(0)}
          </Tag>
          <Tag>
            {t("depth.rigorShort")} {((record.rigor_score ?? 0) * 100).toFixed(0)}
          </Tag>
          <Tag>
            {t("depth.influenceShort")} {((record.influence_score ?? 0) * 100).toFixed(0)}
          </Tag>
          <Tag>
            {t("depth.reproducibilityShort")}{" "}
            {((record.reproducibility_score ?? 0) * 100).toFixed(0)}
          </Tag>
        </Space>
      ),
    },
    {
      title: t("depth.keywords"),
      dataIndex: "keywords",
      ellipsis: true,
      render: (keywords: string[]) => (
        <Space size="small" wrap>
          {(keywords ?? []).slice(0, 5).map((k) => (
            <Tag key={k}>{k}</Tag>
          ))}
        </Space>
      ),
    },
  ];

  return (
    <div className="pf-depth-workspace pf-page-wide" style={{ padding: "24px 0" }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        <Radar style={{ marginRight: 8 }} />
        {t("depth.title")}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        {t("depth.subtitle")}
      </Typography.Paragraph>
      {/* 错误结果提示 */}
      {results.length > validResults.length && (
        <Alert
          type="warning"
          title={t("depth.failedCount", { count: results.length - validResults.length })}
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      <Card className="pf-depth-selection-card" style={{ marginBottom: 16 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 12,
            flexWrap: "wrap",
            gap: 12,
          }}
        >
          <Space>
            <Typography.Text strong>已选择 {selectedIds.size} 篇论文</Typography.Text>
            {selectedIds.size > 0 && (
              <Button size="small" onClick={clearSelection}>
                清空选择
              </Button>
            )}
          </Space>
          <Button
            type="primary"
            icon={<Zap />}
            loading={evaluating}
            disabled={evaluating || selectedIds.size === 0}
            onClick={handleEvaluate}
          >
            {evaluating
              ? `${t("depth.evaluating")} (${progress?.current ?? 0}/${progress?.total ?? 0})`
              : t("depth.startEvaluate")}
          </Button>
        </div>

        {evaluating && progress && (
          <Progress
            percent={Math.round((progress.current / Math.max(progress.total, 1)) * 100)}
            status="active"
            style={{ marginBottom: 16 }}
          />
        )}

        <Spin spinning={loading}>
          <Table
            rowKey="id"
            dataSource={allPapers}
            columns={paperColumns}
            rowSelection={rowSelection}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              pageSizeOptions: ["10", "20", "50"],
              onChange: (p, ps) => {
                setPage(p);
                if (ps !== pageSize) {
                  setPageSize(ps);
                  setPage(1);
                }
              },
              showTotal: (t) => `共 ${t} 篇`,
            }}
            size="small"
            locale={{
              emptyText: (
                <Empty
                  description={
                    <span>
                      暂无论文。<Link to="/">返回首页</Link> 上传或导入论文。
                    </span>
                  }
                />
              ),
            }}
          />
        </Spin>
      </Card>

      {validResults.length > 0 && (
        <>
          <Card className="pf-depth-summary-card" style={{ marginBottom: 16 }}>
            <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: 260 }}>
                <Typography.Title level={5} style={{ marginTop: 0 }}>
                  <Trophy style={{ marginRight: 8 }} />
                  {t("depth.topPaper")}
                </Typography.Title>
                <DepthRadar score={ranked[0]} size={200} />
              </div>
              <div style={{ flex: 2, minWidth: 300 }}>
                <Typography.Title level={5} style={{ marginTop: 0 }}>
                  {t("depth.summary")}
                </Typography.Title>
                <Space size="large">
                  <div>
                    <div style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
                      {t("depth.avgScore")}
                    </div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>
                      {summary?.average_score != null
                        ? Number(summary.average_score).toFixed(1)
                        : "-"}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
                      {t("depth.maxScore")}
                    </div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>
                      {summary?.highest_score != null
                        ? Number(summary.highest_score).toFixed(1)
                        : "-"}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
                      {t("depth.completedCount")}
                    </div>
                    <div style={{ fontSize: 20, fontWeight: 700 }}>
                      {summary?.completed ?? 0}/{summary?.total ?? 0}
                    </div>
                  </div>
                </Space>
              </div>
            </div>
          </Card>

          <Card className="pf-depth-results-card">
            <Table
              rowKey="paper_id"
              dataSource={ranked}
              columns={resultColumns}
              pagination={{ pageSize: 10 }}
              size="small"
            />
          </Card>
        </>
      )}

      {(errors ?? []).length > 0 && (
        <Card className="pf-depth-error-card" style={{ marginTop: 16 }}>
          <Typography.Text type="danger">{t("depth.errors")}</Typography.Text>
          <ul style={{ margin: "8px 0 0", paddingLeft: 18, fontSize: 12, color: "#ef4444" }}>
            {(errors ?? []).slice(0, 5).map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
