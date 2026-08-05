import { Radar, RefreshCw, Zap } from "lucide-react";
import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Card, Spin, Table, Tag, Tooltip, message } from "antd";

import { listDepthV4Reviews, startBatchV4Review, type DepthReviewV4ListItem } from "@/api/depth";
import { useTaskStore } from "@/store/useTaskStore";
import V4ReviewProgress from "./V4ReviewProgress";
import QwenLoadingBanner from "@/components/QwenLoadingBanner";
import { useQwenStatus } from "@/hooks/useQwenStatus";
import type { TaskInfo } from "@/store/useTaskStore";

export default function V4ReviewPanel() {
  const navigate = useNavigate();

  const [v4Reviews, setV4Reviews] = useState<DepthReviewV4ListItem[]>([]);
  const [v4ReviewTotal, setV4ReviewTotal] = useState(0);
  const [v4ReviewLoading, setV4ReviewLoading] = useState(false);
  const [batchReviewLoading, setBatchReviewLoading] = useState(false);
  const [batchTask, setBatchTask] = useState<TaskInfo | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const { subscribeSSE } = useTaskStore();
  const {
    status: qwenStatus,
    showHint,
    start: startQwenPoll,
    stop: stopQwenPoll,
  } = useQwenStatus();

  const fetchV4Reviews = useCallback(async (silent = false) => {
    if (!silent) setV4ReviewLoading(true);
    try {
      const res = await listDepthV4Reviews(5, 0);
      setV4Reviews(res.items);
      setV4ReviewTotal(res.total);
    } catch {
      // 静默降级
    } finally {
      if (!silent) setV4ReviewLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchV4Reviews();
    const timer = setInterval(() => fetchV4Reviews(true), 30_000);
    return () => {
      clearInterval(timer);
      unsubscribeRef.current?.();
    };
  }, [fetchV4Reviews]);

  const handleBatchReview = useCallback(async () => {
    setBatchReviewLoading(true);
    try {
      const res = await startBatchV4Review();
      if (res.task_id) {
        message.success(res.message);
        startQwenPoll(); // 开始轮询 Qwen 状态（冷启动/切换提示）
        const unsub = subscribeSSE(
          res.task_id,
          (info) => setBatchTask(info),
          (info) => {
            setBatchTask(info);
            setTimeout(() => setBatchTask(null), 3000);
            fetchV4Reviews();
            if (info.result) {
              const r = info.result as { succeeded?: number; failed?: number } | undefined;
              message.success(
                `批量审稿完成: ${r?.succeeded || 0} 成功${r?.failed ? `, ${r.failed} 失败` : ""}`,
              );
              stopQwenPoll(); // 任务完成 → 停止提示轮询
            }
          },
          (info) => {
            setBatchTask(info);
            setTimeout(() => setBatchTask(null), 5000);
            message.error(info.error || "批量审稿失败");
            stopQwenPoll(); // 任务失败 → 停止提示轮询
          },
        );
        unsubscribeRef.current = unsub;
      } else {
        message.info(res.message);
      }
    } catch (e: unknown) {
      const detail =
        e && typeof e === "object" && "response" in e
          ? (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : undefined;
      message.error(detail || "批量审稿启动失败");
    } finally {
      setBatchReviewLoading(false);
    }
  }, [subscribeSSE, fetchV4Reviews, startQwenPoll, stopQwenPoll]);

  const verdictColors: Record<string, string> = {
    accept: "green",
    minor_revision: "blue",
    major_revision: "orange",
    reject: "red",
  };

  const verdictLabels: Record<string, string> = {
    accept: "Accept",
    minor_revision: "Minor",
    major_revision: "Major",
    reject: "Reject",
  };

  return (
    <Card
      title={
        <span>
          <Radar style={{ color: "var(--pf-primary)", marginRight: 8 }} />
          V4.1 深度审稿记录
          <span
            style={{
              fontSize: 12,
              color: "var(--pf-text-placeholder)",
              marginLeft: 12,
              fontWeight: 400,
            }}
          >
            （共 {v4ReviewTotal} 条，按创新评分降序）
          </span>
        </span>
      }
      extra={
        <span style={{ display: "flex", gap: 8 }}>
          <Tooltip title="批量审完所有未审论文">
            <Button
              size="small"
              icon={<Zap />}
              loading={batchReviewLoading}
              onClick={handleBatchReview}
            >
              批量审稿
            </Button>
          </Tooltip>
          <Button size="small" icon={<RefreshCw />} onClick={() => fetchV4Reviews()} />
        </span>
      }
      style={{ marginBottom: 20 }}
    >
      <Spin spinning={v4ReviewLoading}>
        {showHint && <QwenLoadingBanner status={qwenStatus} />}
        <V4ReviewProgress task={batchTask} />
        {v4Reviews.length === 0 ? (
          <div
            style={{
              textAlign: "center",
              padding: "24px 0",
              color: "var(--pf-text-placeholder)",
            }}
          >
            暂无审稿记录，上传论文后将自动触发审稿，或点击「批量审稿」审完论文库。
          </div>
        ) : (
          <Table
            dataSource={v4Reviews}
            rowKey="id"
            size="small"
            pagination={false}
            columns={[
              { title: "论文", dataIndex: "paper_title", ellipsis: true },
              {
                title: "创新分",
                dataIndex: "novelty_score",
                width: 80,
                render: (s: number | null) =>
                  s != null ? (
                    <span
                      style={{
                        fontWeight: 700,
                        color: s >= 0.7 ? "#52c41a" : s >= 0.5 ? "#faad14" : "#ff4d4f",
                      }}
                    >
                      {(s * 100).toFixed(0)}%
                    </span>
                  ) : (
                    "-"
                  ),
              },
              {
                title: "热点契合",
                dataIndex: "hotspot_alignment_score",
                width: 90,
                render: (s: number | null) => (s != null ? `${(s * 100).toFixed(0)}%` : "-"),
              },
              {
                title: "核心贡献",
                dataIndex: "core_contribution",
                ellipsis: true,
                width: 200,
                render: (c: string | null) => c || "-",
              },
              {
                title: "裁决",
                dataIndex: "final_verdict",
                width: 100,
                render: (v: string | null) => {
                  if (!v) return "-";
                  return <Tag color={verdictColors[v] || "default"}>{verdictLabels[v] || v}</Tag>;
                },
              },
              {
                title: "时间",
                dataIndex: "completed_at",
                width: 150,
                render: (t: string | null) => (t ? new Date(t).toLocaleString() : "-"),
              },
            ]}
            onRow={(record) => ({
              onClick: () => navigate(`/depth-v4/result/${record.paper_id}?highlight=1`),
              style: { cursor: "pointer" },
            })}
          />
        )}
      </Spin>
    </Card>
  );
}
