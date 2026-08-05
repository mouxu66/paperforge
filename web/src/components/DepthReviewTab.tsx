import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Empty,
  Skeleton,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { RefreshCw, Zap, FileSearch, TriangleAlert } from "lucide-react";

import {
  getDepthV4Result,
  startDepthV4Review,
  type DepthReviewV4Result,
} from "@/api/depth";
import FigureConsistencyCard from "./FigureConsistencyCard";
import FigureDetailsList from "./FigureDetailsList";
import ScoreBar from "./ScoreBar";

const { Title, Text } = Typography;

const VERDICT_COLORS: Record<string, string> = {
  accept: "green",
  minor_revision: "blue",
  major_revision: "orange",
  reject: "red",
};

interface DepthReviewTabProps {
  paperId: string;
}

export default function DepthReviewTab({ paperId }: DepthReviewTabProps) {
  const { t } = useTranslation();
  const [result, setResult] = useState<DepthReviewV4Result | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const navigate = useNavigate();

  const verdictLabels: Record<string, string> = {
    accept: t("depth.verdictAccept", "接收 Accept"),
    minor_revision: t("depth.verdictMinor", "小修 Minor Revision"),
    major_revision: t("depth.verdictMajor", "大修 Major Revision"),
    reject: t("depth.verdictReject", "拒稿 Reject"),
  };

  const fetchResult = useCallback(
    async (isRefresh = false) => {
      if (fetchingRef.current) return;
      fetchingRef.current = true;
      if (!isRefresh) setLoading(true);
      setRefreshing(isRefresh);
      setError(null);
      abortRef.current = new AbortController();
      try {
        const res = await getDepthV4Result(paperId, { signal: abortRef.current.signal });
        if (!mountedRef.current) return;
        setResult(res);
      } catch (err0: unknown) {
        if (!mountedRef.current) return;
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        if (e?.name === "CanceledError" || e?.name === "AbortError") {
          return;
        }
        if (e?.response?.status === 404) {
          setError("no_review");
        } else {
          setError(e?.response?.data?.detail || t("depth.fetchError", "获取审稿结果失败"));
        }
      } finally {
        if (mountedRef.current) {
          setLoading(false);
          setRefreshing(false);
        }
        fetchingRef.current = false;
      }
    },
    [paperId, t],
  );

  useEffect(() => {
    mountedRef.current = true;
    fetchResult();
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
    };
  }, [fetchResult]);

  useEffect(() => {
    if (result?.status !== "running" && result?.status !== "pending") return;
    const timer = setInterval(() => fetchResult(true), 5000);
    return () => clearInterval(timer);
  }, [result?.status, fetchResult]);

  const handleStartReview = async () => {
    setStarting(true);
    try {
      const res = await startDepthV4Review(paperId);
      message.success(t("depth.reviewStarted", { taskId: res.task_id }));
      fetchResult(true);
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error(e?.response?.data?.detail || t("depth.reviewStartFailed", "启动审稿失败"));
    } finally {
      setStarting(false);
    }
  };

  if (loading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }

  if (error === "no_review") {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t("depth.noReview", "该论文暂无 DEPTH v4.1 审稿结果")}
      >
        <Button type="primary" icon={<Zap />} loading={starting} onClick={handleStartReview}>
          {t("depth.startReview", "启动深度审稿")}
        </Button>
      </Empty>
    );
  }

  if (error) {
    return (
      <Alert
        type="error"
        message={t("depth.errorTitle", "获取审稿结果失败")}
        description={error}
        action={
          <Button size="small" icon={<RefreshCw />} onClick={() => fetchResult(true)}>
            {t("common.retry", "重试")}
          </Button>
        }
      />
    );
  }

  if (!result) return null;

  const fv = result.final_verdict;
  const isActive = result.status === "running" || result.status === "pending";

  return (
    <Spin spinning={refreshing}>
      <Space direction="vertical" style={{ width: "100%" }} size="middle">
        <Space wrap>
          {result.status === "running" || result.status === "pending" ? (
            <Tag color="blue">{t("depth.statusRunning", "审稿中")}</Tag>
          ) : result.status === "completed" ? (
            <Tag color="green">{t("depth.statusCompleted", "审稿完成")}</Tag>
          ) : result.status === "timed_out" ? (
            <Tag color="orange" icon={<TriangleAlert />}>
              {t("depth.statusTimedOut", "审稿超时")}
            </Tag>
          ) : (
            <Tag color="red">{t("depth.statusFailed", "审稿失败")}</Tag>
          )}
          <Button
            size="small"
            icon={<RefreshCw />}
            onClick={() => fetchResult(true)}
            loading={refreshing}
          >
            {t("common.refresh", "刷新")}
          </Button>
          <Button
            size="small"
            icon={<Zap />}
            onClick={handleStartReview}
            loading={starting}
            disabled={isActive}
          >
            {t("depth.restartReview", "重新审稿")}
          </Button>
          <Button
            size="small"
            onClick={() => navigate(`/depth-v4/result/${paperId}?highlight=1`)}
          >
            {t("depth.fullReport", "查看完整报告")}
          </Button>
        </Space>

        {result.status === "running" && (
          <Alert
            type="info"
            message={t("depth.runningMessage", "审稿正在进行中，请稍候...")}
            showIcon
          />
        )}

        {result.status === "failed" && (
          <Alert
            type="error"
            message={t("depth.failedTitle", "审稿失败")}
            description={result.error_message || t("depth.unknownError", "未知错误")}
            showIcon
          />
        )}

        {result.status === "timed_out" && (
          <Alert
            type="warning"
            message={t("depth.statusTimedOut", "审稿超时")}
            description={
              result.error_message ||
              t("depth.timedOutMessage", "任务超过最大执行时长，未生成可用结果；可以重新审稿。")
            }
            showIcon
          />
        )}

        {result.status === "completed" && fv && (
          <>
            <Card>
              <Title level={4} style={{ marginBottom: 8 }}>
                {t("depth.overallScore", "综合得分 {{score}}%", {
                  score: (fv.calibrated_score * 100).toFixed(0),
                })}
              </Title>
              <Tag
                color={VERDICT_COLORS[fv.final_verdict] || "default"}
                style={{ fontSize: 14, padding: "2px 12px" }}
              >
                {verdictLabels[fv.final_verdict] || fv.final_verdict}
              </Tag>
            </Card>

            <Card title={t("depth.dimensionScores", "各维度评分")} style={{ marginBottom: 16 }}>
              <ScoreBar
                label={t("depth.novelty", "创新分")}
                score={result.q2_result?.novelty_score ?? 0}
                color="#1677ff"
              />
              <ScoreBar
                label={t("depth.rigor", "严谨性")}
                score={result.q3_result?.rigor_score ?? 0}
                color="#fa8c16"
              />
              <ScoreBar
                label={t("depth.influence", "影响力")}
                score={result.q4_result?.influence_score ?? 0}
                color="#52c41a"
              />
              <ScoreBar
                label={t("depth.reproducibility", "可复现性")}
                score={result.q4_result?.reproducibility_score ?? 0}
                color="#13c2c2"
              />
            </Card>

            <FigureConsistencyCard finalVerdict={fv} evidencePool={result.evidence_pool} />

            <Card
              title={t("depth.figureDetails", "图表详情")}
              style={{ marginBottom: 16 }}
            >
              <FigureDetailsList paperId={paperId} />
            </Card>

            <Card title={t("depth.paperType", "论文类型")} style={{ marginBottom: 16 }}>
              <Descriptions size="small" column={2}>
                <Descriptions.Item label={t("depth.primaryType", "主类型")}>
                  {result.q1_result?.type || "-"}
                </Descriptions.Item>
                <Descriptions.Item label={t("depth.secondaryType", "辅类型")}>
                  {result.q1_result?.secondary_type || t("common.none", "无")}
                </Descriptions.Item>
                <Descriptions.Item label={t("depth.confidence", "置信度")}>
                  {(Number(result.q1_result?.confidence ?? 0) * 100).toFixed(0)}%
                </Descriptions.Item>
              </Descriptions>
            </Card>

            {result.evidence_pool && result.evidence_pool.length > 0 && (
              <Card title={t("depth.evidencePool", "证据池")} style={{ marginBottom: 16 }}>
                <Text type="secondary">
                  <FileSearch />{" "}
                  {t("depth.evidenceCount", "共 {{count}} 条证据", {
                    count: result.evidence_pool.length,
                  })}
                </Text>
              </Card>
            )}
          </>
        )}
      </Space>
    </Spin>
  );
}
