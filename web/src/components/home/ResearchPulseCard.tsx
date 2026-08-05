import { useState } from "react";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  LoaderCircle,
  RotateCcw,
  Trash2,
  X,
} from "lucide-react";
import { Button, Card, Popconfirm, Progress, Space, Tag, Tooltip, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { submitDepthBatch } from "@/api/depth";
import { useDepthStore, type DepthTask } from "@/store/useDepthStore";

function taskStatusColor(
  status: DepthTask["status"],
): "processing" | "success" | "error" | "warning" {
  if (status === "running") return "processing";
  if (status === "completed") return "success";
  if (status === "canceled") return "warning";
  return "error";
}

function taskStatusIcon(status: DepthTask["status"]) {
  if (status === "running") return <LoaderCircle className="pf-spin" size={13} />;
  if (status === "completed") return <CheckCircle2 size={13} />;
  return <CircleAlert size={13} />;
}

function progressPercent(task: DepthTask): number {
  if (task.progress.total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((task.progress.current / task.progress.total) * 100)));
}

export default function ResearchPulseCard() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const tasks = useDepthStore((state) => state.tasks);
  const addTask = useDepthStore((state) => state.addTask);
  const removeTask = useDepthStore((state) => state.removeTask);
  const cancelTask = useDepthStore((state) => state.cancelTask);
  const clearCompleted = useDepthStore((state) => state.clearCompleted);
  const [retryingTaskId, setRetryingTaskId] = useState<string | null>(null);

  // Empty state stays intentionally quiet: the main paper workflow remains the focus.
  if (!tasks || tasks.length === 0) return null;

  const hasRunningTask = tasks.some((task) => task.status === "running");
  const inactiveCount = tasks.filter((task) => task.status !== "running").length;
  const displayTasks = [...tasks]
    .sort((a, b) => {
      if (a.status === "running" && b.status !== "running") return -1;
      if (b.status === "running" && a.status !== "running") return 1;
      return b.submittedAt - a.submittedAt;
    })
    .slice(0, 3);

  const handleRetry = async (task: DepthTask) => {
    if (task.status !== "failed") return;
    if (!task.paperIds?.length) {
      message.warning(t("pulse.retryUnavailable"));
      return;
    }

    setRetryingTaskId(task.taskId);
    try {
      const result = await submitDepthBatch(task.paperIds);
      removeTask(task.taskId);
      addTask(result.task_id, result.total_papers, task.paperIds);
      message.success(t("pulse.retrySuccess"));
      // 首页没有独立轮询器，交给 DEPTH 页面统一恢复并轮询新任务。
      navigate("/depth", { state: { taskId: result.task_id, selectedIds: task.paperIds } });
    } catch (error: unknown) {
      message.error(error instanceof Error ? error.message : t("pulse.retryFailed"));
    } finally {
      setRetryingTaskId(null);
    }
  };

  const handleCancel = (taskId: string) => {
    cancelTask(taskId);
    message.info(t("pulse.cancelSuccess"));
  };

  return (
    <Card
      className="pf-glass-card pf-research-pulse"
      title={
        <Space size={9}>
          <span className="pf-pulse-icon" aria-hidden="true">
            <Activity size={17} />
          </span>
          <span>{t("pulse.title", "研究脉搏")}</span>
          <span
            className={`pf-pulse-live-dot${hasRunningTask ? "" : " is-idle"}`}
            aria-hidden="true"
          />
        </Space>
      }
      extra={
        <Space size={4}>
          {inactiveCount > 0 && (
            <Tooltip title={t("pulse.clearInactive", "清理已结束任务")}>
              <Button
                type="text"
                size="small"
                className="pf-pulse-clear"
                icon={<Trash2 size={14} />}
                aria-label={t("pulse.clearInactive", "清理已结束任务")}
                onClick={clearCompleted}
              />
            </Tooltip>
          )}
          <Button
            type="link"
            size="small"
            className="pf-pulse-link"
            onClick={() => navigate("/depth")}
            icon={<ArrowRight size={14} />}
            iconPosition="end"
          >
            {t("pulse.viewAll", "查看评估")}
          </Button>
        </Space>
      }
      styles={{ body: { padding: "8px 20px 16px" } }}
    >
      <div className="pf-pulse-list" data-testid="research-pulse-card">
        {displayTasks.map((task) => {
          const isRunning = task.status === "running";
          const isFailed = task.status === "failed";
          const statusLabel = t(`pulse.status.${task.status}`, task.status);
          const score = task.summary?.highest_score;
          const canRetry = isFailed && Boolean(task.paperIds?.length);

          return (
            <div className="pf-pulse-row" key={task.taskId}>
              <div className="pf-pulse-row-main">
                <div className="pf-pulse-row-title">
                  <Typography.Text ellipsis={{ tooltip: task.taskId }}>
                    {t("pulse.task", "DEPTH 评估任务")} · {task.taskId.slice(0, 8)}
                  </Typography.Text>
                  <Space size={6} className="pf-pulse-row-actions">
                    <Tag color={taskStatusColor(task.status)} icon={taskStatusIcon(task.status)}>
                      {statusLabel}
                    </Tag>
                    {isRunning && (
                      <Popconfirm
                        title={t("pulse.cancelTitle", "取消这项评估？")}
                        description={t(
                          "pulse.cancelDescription",
                          "将停止前端追踪；后端已开始的 worker 可能仍会继续运行。",
                        )}
                        okText={t("common.confirm")}
                        cancelText={t("common.cancel")}
                        onConfirm={() => handleCancel(task.taskId)}
                      >
                        <Tooltip title={t("pulse.cancel", "取消评估")}>
                          <Button
                            type="text"
                            size="small"
                            className="pf-pulse-action pf-pulse-cancel"
                            icon={<X size={14} />}
                            aria-label={t("pulse.cancel", "取消评估")}
                          />
                        </Tooltip>
                      </Popconfirm>
                    )}
                    {isFailed && (
                      <Tooltip
                        title={
                          canRetry
                            ? t("pulse.retry", "重试评估")
                            : t("pulse.retryUnavailable", "缺少原始论文信息，无法重试")
                        }
                      >
                        <Button
                          type="text"
                          size="small"
                          className="pf-pulse-action pf-pulse-retry"
                          icon={<RotateCcw size={14} />}
                          aria-label={t("pulse.retry", "重试评估")}
                          disabled={!canRetry || retryingTaskId === task.taskId}
                          loading={retryingTaskId === task.taskId}
                          onClick={() => void handleRetry(task)}
                        />
                      </Tooltip>
                    )}
                  </Space>
                </div>
                {isRunning ? (
                  <Progress
                    percent={progressPercent(task)}
                    size="small"
                    status="active"
                    strokeColor="var(--pf-primary)"
                    format={(percent) => `${task.progress.current}/${task.progress.total} · ${percent}%`}
                  />
                ) : (
                  <Typography.Text type="secondary" className="pf-pulse-detail">
                    {task.status === "completed" && score != null
                      ? t("pulse.completedDetail", "最高分 {{score}} · {{count}} 篇完成", {
                          score: score.toFixed(1),
                          count: task.summary?.completed ?? 0,
                        })
                      : task.status === "failed"
                        ? t("pulse.failedDetail", "{{count}} 个错误，需要复核", {
                            count: task.errors.length,
                          })
                        : task.status === "canceled"
                          ? t("pulse.canceledDetail", "已停止前端追踪")
                          : t("pulse.noSummary", "暂无汇总数据")}
                  </Typography.Text>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
