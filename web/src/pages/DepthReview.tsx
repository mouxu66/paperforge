import { RefreshCw, Zap, CheckCircle, XCircle, Clock, FileSearch, Trash2, SquareMinus, TriangleAlert } from "lucide-react";
import FigureConsistencyCard from "@/components/FigureConsistencyCard";
import ScoreBar from "@/components/ScoreBar";
/**
 * DEPTH v4.1 深度审稿页面。
 *
 * - /depth-v4/result/:paperId  审稿结果展示
 * - /depth-v4/list             审稿记录列表
 */
import * as React from "react";
import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useParams, useNavigate, useSearchParams, Navigate } from "react-router-dom";
import {
  Button,
  Card,
  Descriptions,
  Tag,
  Spin,
  Alert,
  Table,
  Collapse,
  Typography,
  Space,
  Empty,
  Tooltip,
  App,
  Popconfirm,
  Select,
} from "antd";

import {
  getDepthV4Result,
  listDepthV4Papers,
  startDepthV4Review,
  deleteDepthReview,
  batchDeleteDepthReviews,
  startSelectedV4Review,
  type DepthReviewV4Result,
  type DepthReviewV4PaperItem,
  type EvidenceItem,
} from "../api/depth";
import QwenLoadingBanner from "@/components/QwenLoadingBanner";
import FigureDetailsList from "@/components/FigureDetailsList";
import EvidenceSeverityCard from "@/components/EvidenceSeverityCard";
import { getEvidenceSeverityStyles } from "@/components/EvidenceSeverityCard.utils";
import { useQwenStatus } from "@/hooks/useQwenStatus";

const { Title, Text, Paragraph } = Typography;

// antd Table 自定义行：复用 EvidenceSeverityCard 的 severity 样式
interface EvidenceTableRowProps extends React.HTMLAttributes<HTMLTableRowElement> {
  children?: React.ReactNode;
}

const EvidenceTableRow = React.forwardRef<HTMLElement, EvidenceTableRowProps>((props, ref) => {
  const { children, ...rest } = props;
  return (
    <EvidenceSeverityCard as="tr" {...rest} ref={ref}>
      {children}
    </EvidenceSeverityCard>
  );
});

// ---- 裁决展示 ----

const VERDICT_COLORS: Record<string, string> = {
  accept: "green",
  minor_revision: "blue",
  major_revision: "orange",
  reject: "red",
};

const VERDICT_LABELS: Record<string, string> = {
  accept: "接收 Accept",
  minor_revision: "小修 Minor Revision",
  major_revision: "大修 Major Revision",
  reject: "拒稿 Reject",
};

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock />,
  running: <RefreshCw className="pf-spin" />,
  completed: <CheckCircle />,
  failed: <XCircle />,
  timed_out: <TriangleAlert />,
};

// 仅「pending/running」状态触发 409 */
const ACTIVE_STATUSES = new Set(["pending", "running"]);

// 证据池 severity 排序权重：数值越小越靠前
const EVIDENCE_SEVERITY_ORDER: Record<string, number> = {
  fatal: 0,
  minor: 1,
  unknown: 2,
};

function getEvidenceSeverityRank(severity: string | undefined): number {
  if (severity === "fatal") return EVIDENCE_SEVERITY_ORDER.fatal;
  if (severity === "minor") return EVIDENCE_SEVERITY_ORDER.minor;
  return EVIDENCE_SEVERITY_ORDER.unknown;
}

// ---- 结果展示页 ----

function DepthResultView({ paperId }: { paperId: string }) {
  const { message } = App.useApp();
  const [searchParams] = useSearchParams();
  const highlighted = searchParams.get("highlight") === "1";

  const [result, setResult] = useState<DepthReviewV4Result | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [evidenceFilter, setEvidenceFilter] = useState<
    "all" | "fatal" | "minor" | "unknown"
  >("all");
  const [evidenceSort, setEvidenceSort] = useState<"default" | "severity">("default");
  const [activeEvidenceIds, setActiveEvidenceIds] = useState<string[]>([]);
  const [evidenceCollapseKeys, setEvidenceCollapseKeys] = useState<
    string | string[]
  >([]);
  const fetchingRef = useRef(false);
  const navigate = useNavigate();

  const activateEvidenceIds = useCallback((ids: string[]) => {
    setActiveEvidenceIds(ids);
    setEvidenceCollapseKeys(["evidence"]);
  }, []);

  const extractEvidenceIds = useCallback((text: string) => {
    const matches = text.match(/E\d+/g) || [];
    return Array.from(new Set(matches));
  }, []);

  const handleEvidenceClick = useCallback(
    (id: string) => {
      activateEvidenceIds([id]);
      setTimeout(() => {
        document
          .getElementById(`evidence-row-${id}`)
          ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 50);
    },
    [activateEvidenceIds],
  );

  const handleQ5aClick = useCallback(
    (point: string) => {
      const ids = extractEvidenceIds(point);
      if (ids.length > 0) {
        activateEvidenceIds(ids);
        setTimeout(() => {
          document
            .getElementById(`evidence-row-${ids[0]}`)
            ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
        }, 50);
      }
    },
    [activateEvidenceIds, extractEvidenceIds],
  );

  // 证据池：按 severity 过滤/排序（必须在所有 early return 之前调用 Hook）
  const processedEvidence = useMemo(() => {
    const pool = result?.evidence_pool;
    if (!pool) return [];
    const filtered =
      evidenceFilter === "all"
        ? [...pool]
        : pool.filter((item) => (item.severity || "unknown") === evidenceFilter);
    if (evidenceSort === "severity") {
      return [...filtered].sort(
        (a, b) =>
          getEvidenceSeverityRank(a.severity) - getEvidenceSeverityRank(b.severity),
      );
    }
    return filtered;
  }, [result?.evidence_pool, evidenceFilter, evidenceSort]);

  // Qwen/llama-server 加载提示轮询（冷启动/互斥切换时展示）
  const {
    status: qwenStatus,
    showHint,
    start: startQwenPoll,
    stop: stopQwenPoll,
  } = useQwenStatus();

  const fetchResult = useCallback(async () => {
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const res = await getDepthV4Result(paperId);
      setResult(res);
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      if (e?.response?.status === 404) {
        setError("该论文暂无审稿结果");
      } else {
        setError(e?.response?.data?.detail || "获取审稿结果失败");
      }
    } finally {
      setLoading(false);
      fetchingRef.current = false;
    }
  }, [paperId]);

  useEffect(() => {
    fetchResult();
  }, [paperId, fetchResult]);

  useEffect(() => {
    // 审稿已结束（完成/失败）→ 停止 Qwen 状态轮询
    if (result?.status !== "running" && result?.status !== "pending") {
      stopQwenPoll();
      return;
    }
    // 如果是 running/pending 状态，每 3 秒轮询
    const timer = setInterval(() => {
      fetchResult();
    }, 3000);
    return () => clearInterval(timer);
  }, [result?.status, fetchResult, stopQwenPoll]);

  const startReview = async () => {
    try {
      const res = await startDepthV4Review(paperId);
      message.success(`审稿任务已提交 (${res.task_id})`);
      startQwenPoll(); // 开始轮询 Qwen 状态（冷启动/切换提示）
      fetchResult();
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error(e?.response?.data?.detail || "启动审稿失败");
    }
  };

  if (loading) return <Spin size="large" style={{ display: "block", margin: "100px auto" }} />;

  if (error && !result) {
    return (
      <div style={{ maxWidth: 700, margin: "60px auto", textAlign: "center" }}>
        <Empty description={error}>
          <Button type="primary" icon={<Zap />} onClick={startReview}>
            启动深度审稿
          </Button>
        </Empty>
      </div>
    );
  }

  if (!result) return null;

  const fv = result.final_verdict;
  const isOverridden = fv?.llm_verdict !== fv?.final_verdict;

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "24px 16px" }}>
      {/* Qwen 加载/切换提示 */}
      {showHint && <QwenLoadingBanner status={qwenStatus} />}

      {/* 状态栏 */}
      <Space style={{ marginBottom: 16 }}>
        {STATUS_ICONS[result.status]}
        <Text>
          {result.status === "running"
            ? "审稿中..."
            : result.status === "completed"
              ? "审稿完成"
              : result.status === "failed"
                ? "审稿失败"
                : result.status === "timed_out"
                  ? "审稿超时"
                  : "等待中"}
        </Text>
        {result.status === "running" && (
          <Button size="small" icon={<RefreshCw />} onClick={fetchResult}>
            刷新
          </Button>
        )}
        {result.status !== "running" && result.status !== "pending" && (
          <Button size="small" icon={<Zap />} onClick={startReview}>
            重新审稿
          </Button>
        )}
        <Button size="small" onClick={() => navigate("/depth-v4/list")}>
          返回列表
        </Button>
      </Space>

      {result.status === "failed" && (
        <Alert
          type="error"
          title="审稿失败"
          description={result.error_message}
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      {result.status === "timed_out" && (
        <Alert
          type="warning"
          title="审稿超时"
          description={result.error_message || "任务超过最大执行时长，未生成可用结果；可以重新审稿。"}
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      {result.status === "running" && (
        <Alert
          type="info"
          title="审稿正在进行中，请稍候..."
          style={{ marginBottom: 16 }}
          showIcon
        />
      )}

      {result.status === "completed" && fv && (
        <>
          {/* 最终得分 + 裁决 */}
          <Card
            style={{
              marginBottom: 16,
              textAlign: "center",
              ...(highlighted
                ? {
                    animation: "pf-highlight-pulse 1.5s ease-in-out 3",
                    borderColor: "var(--pf-warning)",
                    boxShadow: "0 0 20px rgba(250, 173, 20, 0.3)",
                  }
                : {}),
            }}
          >
            <Title level={2} style={{ marginBottom: 4 }}>
              综合得分 {(Number(fv.calibrated_score ?? 0) * 100).toFixed(0)}%
            </Title>
            <Space size="middle">
              <Tag
                color={VERDICT_COLORS[fv.final_verdict] || "default"}
                style={{ fontSize: 16, padding: "4px 16px" }}
              >
                {VERDICT_LABELS[fv.final_verdict] || fv.final_verdict}
              </Tag>
              {isOverridden && (
                <Tooltip title={fv.override_reason}>
                  <Tag color="volcano">
                    硬编码覆盖 (LLM 原判: {VERDICT_LABELS[fv.llm_verdict] || fv.llm_verdict})
                  </Tag>
                </Tooltip>
              )}
            </Space>
            {isOverridden && (
              <Paragraph type="secondary" style={{ marginTop: 8, fontSize: 13 }}>
                {fv.override_reason}
              </Paragraph>
            )}
          </Card>

          {/* 各维度得分 */}
          <Card title="各维度评分" style={{ marginBottom: 16 }}>
            <ScoreBar label="创新分" score={result.q2_result?.novelty_score || 0} color="#1677ff" />
            <ScoreBar
              label="热点契合"
              score={result.q2_result?.hotspot_alignment_score || 0}
              color="#722ed1"
            />
            <ScoreBar label="严谨性" score={result.q3_result?.rigor_score || 0} color="#fa8c16" />
            <ScoreBar
              label="影响力"
              score={result.q4_result?.influence_score || 0}
              color="#52c41a"
            />
            <ScoreBar
              label="可复现性"
              score={result.q4_result?.reproducibility_score || 0}
              color="#13c2c2"
            />
            <ScoreBar label="主席校准分" score={fv.calibrated_score} color="#eb2f96" />
          </Card>

          {/* 图表一致性（v4.2 QF 节点） */}
          <FigureConsistencyCard
            finalVerdict={fv}
            evidencePool={result.evidence_pool}
            activeEvidenceIds={activeEvidenceIds}
            onEvidenceClick={handleEvidenceClick}
          />

          {/* 图表数值断言与 axis 范围一致性明细 */}
          <Card title="图表详情" style={{ marginBottom: 16 }}>
            <FigureDetailsList paperId={paperId} />
          </Card>

          {/* Q5a 批评 + Q5b 辩护 + Q5c 裁决理由 */}
          <Card title="辩论式校准" style={{ marginBottom: 16 }}>
            {result.q5a_result?.critique_points && result.q5a_result.critique_points.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <Text strong>🔴 质疑者 (Q5a)</Text>
                {result.q5a_result.critique_points.map((cp, i) => {
                  const isRelated = activeEvidenceIds.some((id) =>
                    cp.point.includes(id),
                  );
                  return (
                    <Card
                      key={i}
                      size="small"
                      role="button"
                      tabIndex={0}
                      onClick={() => handleQ5aClick(cp.point)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          handleQ5aClick(cp.point);
                        }
                      }}
                      style={{
                        marginTop: 8,
                        cursor: "pointer",
                        background: isRelated ? "#fffbe6" : undefined,
                        border: isRelated
                          ? "1px solid #faad14"
                          : "1px solid transparent",
                        borderLeft: `3px solid ${
                          isRelated
                            ? "#faad14"
                            : cp.severity === "fatal"
                              ? "#ff4d4f"
                              : "var(--pf-warning)"
                        }`,
                        transition: "all 0.3s",
                      }}
                    >
                      <Space>
                        <Tag color={cp.severity === "fatal" ? "red" : "gold"}>
                          {cp.severity === "fatal" ? "致命" : "轻微"}
                        </Tag>
                        <Text>{cp.point}</Text>
                      </Space>
                    </Card>
                  );
                })}
              </div>
            )}
            {result.q5b_result?.defense_points && result.q5b_result.defense_points.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <Text strong>🟢 辩护者 (Q5b)</Text>
                {result.q5b_result.defense_points.map((dp, i) => (
                  <Card key={i} size="small" style={{ marginTop: 8 }}>
                    <Text type="secondary">{dp}</Text>
                  </Card>
                ))}
              </div>
            )}
            {result.q5c_result?.reasoning && (
              <div>
                <Text strong>⚖️ 主席裁决 (Q5c)</Text>
                <Card size="small" style={{ marginTop: 8 }}>
                  <Text>{result.q5c_result.reasoning}</Text>
                  <br />
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    Delta: {(result.q5c_result.delta ?? 0) > 0 ? "+" : ""}
                    {Number(result.q5c_result.delta ?? 0).toFixed(3)}
                    {result.q5c_result.delta_missing && " (LLM 未输出，默认 0)"}
                  </Text>
                </Card>
              </div>
            )}
          </Card>

          {/* 评审依据（可复核性）：分数把握、双模型印证、复现参数 */}
          {(fv.score_uncertainty || fv.cross_check || fv.llm_params_snapshot || fv.citation_integrity) && (
            <Card
              title={
                <Space>
                  <FileSearch />
                  评审依据（可复核性）
                </Space>
              }
              size="small"
              style={{ marginBottom: 16 }}
            >
              {/* 双模型交叉复核 */}
              {fv.cross_check?.enabled && (
                <Alert
                  type={fv.cross_check.flag === "disagreement" ? "warning" : "success"}
                  showIcon
                  style={{ marginBottom: 12 }}
                  title={
                    fv.cross_check.flag === "disagreement"
                      ? "双模型分歧：建议人工复核"
                      : "双模型交叉印证一致"
                  }
                  description={
                    <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                      <div>
                        第二评审员：{fv.cross_check.second_model || fv.cross_check.second_provider || "-"}
                        {fv.cross_check.second_score != null && (
                          <span style={{ marginLeft: 8 }}>
                            次评分：{(Number(fv.cross_check.second_score) * 100).toFixed(0)}%
                          </span>
                        )}
                        {fv.cross_check.second_verdict && (
                          <Tag
                            style={{ marginLeft: 8 }}
                            color={fv.cross_check.flag === "disagreement" ? "orange" : "green"}
                          >
                            {fv.cross_check.second_verdict}
                          </Tag>
                        )}
                        {fv.cross_check.score_delta != null && (
                          <Text type="secondary" style={{ marginLeft: 8, fontSize: 12 }}>
                            |Δ|={Number(fv.cross_check.score_delta).toFixed(2)}
                          </Text>
                        )}
                      </div>
                      {(fv.cross_check.second_reason || fv.cross_check.note) && (
                        <Text type="secondary" style={{ fontSize: 12 }}>
                          {fv.cross_check.second_reason || fv.cross_check.note}
                        </Text>
                      )}
                    </Space>
                  }
                />
              )}

              {/* 分数不确定性（bootstrap CI） */}
              {fv.score_uncertainty && fv.score_uncertainty.ci_low != null && (
                <div style={{ marginBottom: 12 }}>
                  <Space wrap>
                    <Text strong>分数置信区间（95%）</Text>
                    <Tag color={fv.score_uncertainty.status === "needs_human_review" ? "volcano" : "blue"}>
                      {(Number(fv.score_uncertainty.ci_low) * 100).toFixed(0)}% ~{" "}
                      {(Number(fv.score_uncertainty.ci_high) * 100).toFixed(0)}%
                    </Tag>
                    {fv.score_uncertainty.status === "needs_human_review" && (
                      <Tag color="red">建议人工复核</Tag>
                    )}
                  </Space>
                  {fv.score_uncertainty.note && (
                    <div style={{ fontSize: 12, color: "var(--pf-text-muted)", marginTop: 4 }}>
                      {fv.score_uncertainty.note}
                    </div>
                  )}
                </div>
              )}

              {/* 引用真值校验 */}
              {fv.citation_integrity &&
                (typeof fv.citation_integrity === "object") &&
                Object.keys(fv.citation_integrity).length > 0 && (
                  <div style={{ marginBottom: 12 }}>
                    <Text strong>引用真值校验</Text>
                    <div style={{ fontSize: 12, color: "var(--pf-text-muted)", marginTop: 4 }}>
                      {JSON.stringify(fv.citation_integrity)}
                    </div>
                  </div>
                )}

              {/* LLM 参数快照（可复现性） */}
              {fv.llm_params_snapshot && Object.keys(fv.llm_params_snapshot).length > 0 && (
                <div>
                  <Text strong style={{ fontSize: 12 }}>评审参数（可复现）</Text>
                  <div style={{ fontSize: 12, color: "var(--pf-text-muted)", marginTop: 2 }}>
                    {JSON.stringify(fv.llm_params_snapshot)}
                  </div>
                </div>
              )}
            </Card>
          )}

          {/* 类型判别 */}
          <Card title="论文类型" style={{ marginBottom: 16 }}>
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="主类型">{result.q1_result?.type}</Descriptions.Item>
              <Descriptions.Item label="辅类型">
                {result.q1_result?.secondary_type || "无"}
              </Descriptions.Item>
              <Descriptions.Item label="置信度">
                {(Number(result.q1_result?.confidence ?? 0) * 100).toFixed(0)}%
              </Descriptions.Item>
              <Descriptions.Item label="实质性贡献">
                {result.q0_result?.has_substance ? "是" : "否"}
              </Descriptions.Item>
            </Descriptions>
          </Card>

          {/* 证据池 */}
          {result.evidence_pool && (
            <Collapse
              style={{ marginBottom: 16 }}
              activeKey={evidenceCollapseKeys}
              onChange={setEvidenceCollapseKeys}
              items={[
                {
                  key: "evidence",
                  label: (
                    <Space>
                      <FileSearch />
                      证据池 ({processedEvidence.length}/{result.evidence_pool.length} 条)
                    </Space>
                  ),
                  children: (
                    <>
                      <Space style={{ marginBottom: 12 }} wrap>
                        <Select
                          value={evidenceFilter}
                          onChange={setEvidenceFilter}
                          options={[
                            { value: "all", label: "全部" },
                            { value: "fatal", label: "致命" },
                            { value: "minor", label: "轻微" },
                            { value: "unknown", label: "未知" },
                          ]}
                          style={{ width: 120 }}
                          size="small"
                          data-testid="evidence-filter"
                        />
                        <Select
                          value={evidenceSort}
                          onChange={setEvidenceSort}
                          options={[
                            { value: "default", label: "默认顺序" },
                            { value: "severity", label: "按严重度排序" },
                          ]}
                          style={{ width: 140 }}
                          size="small"
                          data-testid="evidence-sort"
                        />
                      </Space>
                      <Table
                        dataSource={processedEvidence}
                        rowKey="id"
                        pagination={false}
                        size="small"
                        data-testid="evidence-table"
                        locale={{ emptyText: "暂无证据" }}
                        onRow={(record) => ({
                          id: `evidence-row-${record.id}`,
                          "data-severity": record.severity || "unknown",
                          "data-active": activeEvidenceIds.includes(record.id),
                          role: "button",
                          tabIndex: 0,
                          onClick: () => handleEvidenceClick(record.id),
                          onKeyDown: (e: React.KeyboardEvent<HTMLTableRowElement>) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              handleEvidenceClick(record.id);
                            }
                          },
                        })}
                        components={{
                          body: {
                            row: EvidenceTableRow,
                          },
                        }}
                        columns={[
                        {
                          title: "ID",
                          dataIndex: "id",
                          width: 50,
                          onCell: (record: EvidenceItem) => {
                            const isHighlighted = activeEvidenceIds.includes(record.id);
                            const { border } = getEvidenceSeverityStyles(record.severity, isHighlighted);
                            return {
                              style: {
                                boxShadow:
                                  border !== "transparent"
                                    ? `inset 3px 0 0 0 ${border}`
                                    : undefined,
                              },
                            };
                          },
                        },
                        {
                          title: "严重度",
                          dataIndex: "severity",
                          width: 80,
                          render: (
                            severity: "fatal" | "minor" | undefined,
                            record: EvidenceItem,
                          ) => {
                            const label =
                              severity === "fatal"
                                ? "致命"
                                : severity === "minor"
                                  ? "轻微"
                                  : "未知";
                            return (
                              <Tag
                                role="button"
                                tabIndex={0}
                                data-testid={`severity-tag-${record.id}`}
                                color={
                                  severity === "fatal"
                                    ? "red"
                                    : severity === "minor"
                                      ? "orange"
                                      : "default"
                                }
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleEvidenceClick(record.id);
                                }}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter" || e.key === " ") {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    handleEvidenceClick(record.id);
                                  }
                                }}
                                style={{ cursor: "pointer" }}
                              >
                                {label}
                              </Tag>
                            );
                          },
                        },
                        { title: "内容", dataIndex: "content" },
                        { title: "章节", dataIndex: "section", width: 120 },
                        {
                          title: "关键词",
                          dataIndex: "keywords",
                          width: 180,
                          render: (k: string[]) => k?.join(", "),
                        },
                      ]}
                    />
                  </>),
                },
              ]}
            />
          )}

          {/* 漏缺项 */}
          {result.q3_result?.missing_items && result.q3_result.missing_items.length > 0 && (
            <Card title="严谨性缺失项" style={{ marginBottom: 16 }}>
              {result.q3_result.missing_items.map((item, i) => (
                <Tag key={i} color="orange" style={{ marginBottom: 4 }}>
                  {item}
                </Tag>
              ))}
            </Card>
          )}
        </>
      )}
    </div>
  );
}

// ---- 列表页 ----

function DepthListPage() {
  const { message } = App.useApp();
  const [data, setData] = useState<DepthReviewV4PaperItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [restartingIds, setRestartingIds] = useState<Set<string>>(new Set());
  // 跨分页持久化的勾选状态：以 paper_id 为 key（与 Table rowKey 对齐）。
  // React state 在分页切换间保留，因此用户可以跨多页累计勾选。
  // 两条批量动作都基于它：删除走 paper_ids（后端解析），重审走 paper_ids 直接。
  const [selectedPaperIds, setSelectedPaperIds] = useState<string[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [batchReReviewing, setBatchReReviewing] = useState(false);
  const navigate = useNavigate();

  // 缓存最新 data，避免 setInterval effect 取陈旧闭包 + 避免 deps 含 data 反复重建 interval
  const dataRef = useRef(data);
  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  const fetchList = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const res = await listDepthV4Papers(20, (p - 1) * 20);
      setData(res.items);
      setTotal(res.total);
    } catch {
      message.error("获取审稿列表失败");
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    fetchList(page);
    // 进行中的审稿每 5 秒静默轮询，避免「重审」按钮一直停在旧状态
    const timer = setInterval(() => {
      if (dataRef.current.some((d) => ACTIVE_STATUSES.has(d.latest_status))) {
        fetchList(page);
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [page, fetchList]);

  // 重新审稿：dedup-aware 后端会在 active 时返回 409
  const handleRestartReview = useCallback(
    async (paperId: string) => {
      setRestartingIds((prev) => new Set(prev).add(paperId));
      try {
        const res = await startDepthV4Review(paperId);
        message.success(`重审已提交（task_id=${res.task_id}）`);
        fetchList(page);
      } catch (err0: unknown) {
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        const status = e?.response?.status;
        const detail = e?.response?.data?.detail || e?.message || "重审启动失败";
        if (status === 409) {
          // 友好提示：单行避免 AntD static API + React 18 strict mode 问题
          const firstLine = String(detail).split("\n")[0];
          message.warning(`已有进行中的审稿任务。${firstLine}`, 4);
        } else {
          message.error(detail);
        }
        // 无论如何刷一次以拿最新状态
        fetchList(page);
      } finally {
        setRestartingIds((prev) => {
          const next = new Set(prev);
          next.delete(paperId);
          return next;
        });
      }
    },
    [page, fetchList, message],
  );

  const handleDelete = useCallback(
    async (recordId: string) => {
      try {
        await deleteDepthReview(recordId);
        message.success("审稿记录已删除");
        fetchList(page);
      } catch (err0: unknown) {
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        message.error(e?.response?.data?.detail || "删除失败");
      }
    },
    [page, fetchList, message],
  );

  // 批量删除：调用 POST /api/depth/reviews/batch-delete，传 paper_ids（后端解析）
  const handleBatchDelete = useCallback(async () => {
    if (selectedPaperIds.length === 0) return;
    setBatchDeleting(true);
    try {
      const res = await batchDeleteDepthReviews(selectedPaperIds);
      if (res.failed_ids && res.failed_ids.length > 0) {
        message.warning(
          `部分删除失败：成功 ${res.deleted_count} 条，失败 ${res.failed_ids.length} 条`,
          4,
        );
      } else {
        message.success(`已批量删除 ${res.deleted_count} 条审稿记录`);
      }
      setSelectedPaperIds([]);
      fetchList(page);
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error(e?.response?.data?.detail || "批量删除失败");
    } finally {
      setBatchDeleting(false);
    }
  }, [selectedPaperIds, page, fetchList, message]);

  // 批量重新审稿：调用 POST /api/depth/v4/review-selected（复用现成的批量提交端点）
  const handleBatchReReview = useCallback(async () => {
    if (selectedPaperIds.length === 0) return;
    setBatchReReviewing(true);
    try {
      const res = await startSelectedV4Review(selectedPaperIds);
      const skipped = res.skipped || [];
      if (skipped.length === 0) {
        message.success(`已提交 ${res.submitted} 篇审稿任务，每篇独立可跟踪进度`, 3);
      } else if (res.submitted > 0) {
        // 显示前 3 条跳过原因，方便用户排查
        const sample = skipped
          .slice(0, 3)
          .map((s) => `${s.paper_id}: ${s.reason}`)
          .join(" / ");
        message.warning(
          `已提交 ${res.submitted} 篇，跳过 ${skipped.length} 篇（${sample}${skipped.length > 3 ? "…" : ""}）`,
          5,
        );
      } else {
        const sample = skipped
          .slice(0, 3)
          .map((s) => `${s.paper_id}: ${s.reason}`)
          .join(" / ");
        message.error(
          `全部跳过：${skipped.length} 篇（${sample}${skipped.length > 3 ? "…" : ""}）`,
          6,
        );
      }
      // 选中的论文可能正在审稿，清空勾选避免重复触发
      setSelectedPaperIds([]);
      fetchList(page);
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error(e?.response?.data?.detail || "批量重审失败");
    } finally {
      setBatchReReviewing(false);
    }
  }, [selectedPaperIds, page, fetchList, message]);

  // Row selection：勾选以 paper_id 为 key，与 Table rowKey='paper_id' 对齐。
  // 跨分页持久化：React state 在分页切换时保留 → 勾选一组后翻页、再勾选一批，
  //           全部累积在 selectedPaperIds 中，antd Table 用受控 selectedRowKeys
  //           跨页同样能识别。
  // 禁止勾选活跃行（pending/running）：避免删除/重审时与进行中任务冲突（服务端也会兜底 409）。
  // 最大选择数保护：避免大批量提交触发后端 50/200 上限。
  const MAX_SELECTION = 50;
  const rowSelection = {
    selectedRowKeys: selectedPaperIds,
    onChange: (keys: React.Key[]) => {
      const next = keys.map(String);
      // 截断超量选择：优雅提示用户而不是静默出错
      if (next.length > MAX_SELECTION) {
        message.warning(`单次最多选择 ${MAX_SELECTION} 篇`);
        setSelectedPaperIds(next.slice(0, MAX_SELECTION));
      } else {
        setSelectedPaperIds(next);
      }
    },
    getCheckboxProps: (record: DepthReviewV4PaperItem) => ({
      disabled: ACTIVE_STATUSES.has(record.latest_status),
      title: ACTIVE_STATUSES.has(record.latest_status)
        ? `${record.paper_id} 正在审稿中，无法勾选`
        : "勾选后可批量重新审稿 / 删除",
    }),
  };

  return (
    <div style={{ maxWidth: 1100, margin: "0 auto", padding: "24px 16px" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 8,
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <Title level={3} style={{ margin: 0 }}>
          <FileSearch /> 深度审稿记录 (DEPTH v4.1)
        </Title>
        <Space size={8}>
          <Text type="secondary" style={{ fontSize: 13 }}>
            已选 <strong>{selectedPaperIds.length}</strong> 篇
            {selectedPaperIds.length > 0 && (
              <span style={{ marginLeft: 8 }}>（跨分页累积，后服务端统一提交）</span>
            )}
          </Text>
          <Popconfirm
            title={`确定批量重审 ${selectedPaperIds.length} 篇论文？`}
            description="将对选中的每篇论文重新发起 DEPTH v4.1 审稿任务（与单条 “重新审稿” 行为一致）"
            okText="批量重审"
            cancelText="取消"
            disabled={selectedPaperIds.length === 0 || batchReReviewing}
            onConfirm={(e) => {
              e?.stopPropagation();
              handleBatchReReview();
            }}
            onCancel={(e) => e?.stopPropagation()}
          >
            <Button
              type="primary"
              icon={<Zap />}
              loading={batchReReviewing}
              disabled={selectedPaperIds.length === 0}
              onClick={(e) => e.stopPropagation()}
            >
              批量重新审稿
            </Button>
          </Popconfirm>
          <Popconfirm
            title={`确定批量删除 ${selectedPaperIds.length} 条审稿记录？`}
            description="删除每篇论文最新一条审稿记录，此操作不可撤销"
            okText="批量删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            disabled={selectedPaperIds.length === 0 || batchDeleting}
            onConfirm={(e) => {
              e?.stopPropagation();
              handleBatchDelete();
            }}
            onCancel={(e) => e?.stopPropagation()}
          >
            <Button
              danger
              icon={<Trash2 />}
              loading={batchDeleting}
              disabled={selectedPaperIds.length === 0}
              onClick={(e) => e.stopPropagation()}
            >
              批量删除
            </Button>
          </Popconfirm>
          <Button
            size="small"
            icon={<SquareMinus />}
            disabled={selectedPaperIds.length === 0}
            onClick={() => setSelectedPaperIds([])}
          >
            清空选择
          </Button>
        </Space>
      </div>
      <Text type="secondary" style={{ display: "block", marginBottom: 16, fontSize: 13 }}>
        按论文聚合 · 最新创新分降序 · 有正在审稿的论文每 5 秒自动刷新
      </Text>
      <Table
        dataSource={data}
        rowKey="paper_id"
        loading={loading}
        rowSelection={rowSelection}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: (p) => setPage(p),
          showTotal: (t) => `共 ${t} 篇论文`,
        }}
        onRow={(record) => ({
          onClick: () => navigate(`/depth-v4/result/${record.paper_id}`),
          style: { cursor: "pointer" },
        })}
        columns={[
          {
            title: "论文",
            dataIndex: "paper_title",
            ellipsis: true,
            render: (t: string, record: DepthReviewV4PaperItem) => (
              <Space size={4} orientation="vertical">
                <span>{t || "(未命名)"}</span>
                <Text
                  type="secondary"
                  style={{ fontSize: 11 }}
                  copyable={{ text: record.paper_id }}
                >
                  {record.paper_id}
                </Text>
              </Space>
            ),
          },
          {
            title: "总尝试",
            dataIndex: "total_reviews",
            width: 90,
            align: "center",
            render: (n: number, record: DepthReviewV4PaperItem) => (
              <Tooltip
                title={
                  <div>
                    <div>成功：{record.completed_count}</div>
                    <div>失败：{record.failed_count}</div>
                    <div>进行中：{record.pending_count}</div>
                  </div>
                }
              >
                <span style={{ fontWeight: 600 }}>{n}</span>
              </Tooltip>
            ),
          },
          {
            title: "失败",
            dataIndex: "failed_count",
            width: 80,
            align: "center",
            render: (n: number, record: DepthReviewV4PaperItem) =>
              n > 0 ? (
                <Tag color={record.has_unresolved_failure ? "red" : "orange"}>{n}</Tag>
              ) : (
                <Text type="secondary">-</Text>
              ),
          },
          {
            title: "最新状态",
            dataIndex: "latest_status",
            width: 110,
            render: (s: string) => (
              <Tag
                icon={STATUS_ICONS[s]}
                color={
                  s === "completed"
                    ? "green"
                    : s === "failed"
                      ? "red"
                      : s === "running"
                        ? "blue"
                        : s === "timed_out"
                          ? "orange"
                          : "default"
                }
              >
                {s === "completed"
                  ? "已完成"
                  : s === "running"
                    ? "进行中"
                    : s === "failed"
                      ? "失败"
                      : s === "timed_out"
                        ? "超时"
                        : "等待"}
              </Tag>
            ),
          },
          {
            title: "最新创新分",
            dataIndex: "latest_novelty_score",
            width: 120,
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
                <Text type="secondary">-</Text>
              ),
          },
          {
            title: "最新裁决",
            dataIndex: "latest_verdict",
            width: 130,
            render: (v: string | null) =>
              v ? (
                <Tag color={VERDICT_COLORS[v] || "default"}>{VERDICT_LABELS[v] || v}</Tag>
              ) : (
                <Text type="secondary">-</Text>
              ),
          },
          {
            title: "最后审稿时间",
            dataIndex: "last_attempted_at",
            width: 160,
            render: (t: string | null) => (t ? new Date(t).toLocaleString() : "-"),
          },
          {
            title: "操作",
            key: "actions",
            width: 250,
            render: (_: unknown, record: DepthReviewV4PaperItem) => {
              const isActive = ACTIVE_STATUSES.has(record.latest_status);
              const isRestarting = restartingIds.has(record.paper_id);
              const tip = isActive
                ? `${record.paper_id} 正在审稿中              （${record.latest_status}），请等待完成后再重审或删除`
                : record.has_unresolved_failure
                  ? `检测到 ${record.failed_count} 次失败但未成功，重审一下试试`
                  : "重新发起 DEPTH v4.1 审稿";
              return (
                <Space size={4}>
                  <Button
                    size="small"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/depth-v4/result/${record.paper_id}`);
                    }}
                  >
                    查看
                  </Button>
                  <Tooltip title={tip}>
                    <Button
                      size="small"
                      type={record.has_unresolved_failure ? "primary" : "default"}
                      danger={record.has_unresolved_failure}
                      icon={<Zap />}
                      loading={isRestarting}
                      disabled={isActive}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRestartReview(record.paper_id);
                      }}
                    >
                      重新审稿
                    </Button>
                  </Tooltip>
                  <Popconfirm
                    title="确定删除该论文的最新审稿记录？"
                    description="此操作不可撤销"
                    onConfirm={(e) => {
                      e?.stopPropagation();
                      handleDelete(record.latest_record_id);
                    }}
                    onCancel={(e) => e?.stopPropagation()}
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                  >
                    <Button
                      size="small"
                      danger
                      icon={<Trash2 />}
                      onClick={(e) => e.stopPropagation()}
                    >
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              );
            },
          },
        ]}
        expandable={{
          rowExpandable: (record) => record.total_reviews > 1 || record.failed_count > 0,
          expandedRowRender: (record) => (
            <div
              style={{ padding: "8px 16px", background: "var(--pf-bg-tertiary)", borderRadius: 6 }}
            >
              <Space size={6} wrap>
                <Tag icon={<CheckCircle />} color="green">
                  成功 {record.completed_count}
                </Tag>
                <Tag
                  icon={<XCircle />}
                  color={record.failed_count > 0 ? "red" : "default"}
                >
                  失败 {record.failed_count}
                </Tag>                  <Tag
                  icon={
                    record.latest_status === "timed_out" ? (
                      <TriangleAlert />
                    ) : (
                      <RefreshCw
                        className={record.latest_status === "running" ? "pf-spin" : undefined}
                      />
                    )
                  }
                  color={record.latest_status === "timed_out" ? "orange" : record.pending_count > 0 ? "blue" : "default"}
                >
                  进行中 {record.pending_count}
                </Tag>
                {record.latest_status === "timed_out" && (
                  <Text type="warning" style={{ fontSize: 12 }}>
                    ⚠️ 任务超时，建议重新审稿
                  </Text>
                )}
                {record.has_unresolved_failure && (
                  <Text type="danger" style={{ fontSize: 12 }}>
                    ⚠️ 有未成功的尝试，建议重新审稿
                  </Text>
                )}
              </Space>
            </div>
          ),
        }}
      />
    </div>
  );
}

// ---- 路由入口 ----

/**
 * DEPTH v4.1 深度审稿页。
 * - /depth-v4/result/:paperId  审稿结果展示
 * - /depth-v4、/depth-v4/list  审稿记录列表
 *
 * 感悟报告评审已拆分为独立入口 /reflection（见 ReflectionReportsPage），
 * 不再在本页以 Tab 形式混排，保证论文分析与会话报告分析物理隔离。
 * 旧版深链 ?tab=report（曾指向感悟报告 Tab）自动重定向到新入口。
 */
export default function DepthReviewPage() {
  const { paperId } = useParams<{ paperId?: string }>();
  const [searchParams] = useSearchParams();

  if (!paperId && searchParams.get("tab") === "report") {
    return <Navigate to="/reflection" replace />;
  }
  if (paperId) {
    return <DepthResultView paperId={paperId} />;
  }
  return <DepthListPage />;
}
