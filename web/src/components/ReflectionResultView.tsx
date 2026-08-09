import {
  RefreshCw,
  CheckCircle,
  XCircle,
  Clock,
  FileSearch,
  Zap,
  ArrowLeft,
  Lightbulb,
  FlaskConical,
  Rocket,
  Link,
  BadgeCheck,
  TriangleAlert,
} from "lucide-react";
/**
 * ReflectionResultView 组件：单条 reflection 评审详情页。
 *
 * 路由：/reflection/result/:paperId
 * 展示：4 维评分（带 Progress）、核心观点/证据池、总结、判决理由、verdict
 * 行为：状态为 running/pending 时每 3 秒轮询刷新
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Spin,
  Card,
  Tag,
  Progress,
  Typography,
  Space,
  Button,
  Alert,
  Descriptions,
  Empty,
  Table,
  Tooltip,
  App,
  Collapse,
} from "antd";

import {
  getReflectionResult,
  startReflectionReview,
  type ReflectionResultResponse,
  type FidelityAnchor,
} from "@/api/reflection";
import { useTaskStore } from "@/store/useTaskStore";
import ReflectionRadar from "./ReflectionRadar";

const { Title, Text, Paragraph } = Typography;

const VERDICT_COLORS: Record<string, string> = {
  well_done: "green",
  needs_evidence: "gold",
  needs_depth: "orange",
  rewrite_required: "red",
  llm_failed: "red",
  "n/a": "default",
};

const VERDICT_LABELS: Record<string, string> = {
  well_done: "写得好 Well Done",
  needs_evidence: "需补证据 Needs Evidence",
  needs_depth: "需深化 Needs Depth",
  rewrite_required: "需重写 Rewrite Required",
  llm_failed: "评审无效（LLM 故障）",
  "n/a": "无评审 N/A",
};

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock />,
  running: <RefreshCw className="pf-spin" />,
  completed: <CheckCircle />,
  failed: <XCircle />,
  timed_out: <TriangleAlert />,
};

const ACTIVE_STATUSES = new Set(["pending", "running"]);

function ScoreBar({
  label,
  score,
  color = "#1677ff",
  icon,
}: {
  label: string;
  score: number;
  color?: string;
  icon?: React.ReactNode;
}) {
  const safe = Number(score ?? 0);
  const pct = Math.round(safe * 100);
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
        <Space>
          {icon}
          <Text type="secondary">{label}</Text>
        </Space>
        <Text strong>{pct}%</Text>
      </div>
      <Progress percent={pct} strokeColor={color} showInfo={false} size="small" />
    </div>
  );
}

export default function ReflectionResultView() {
  const { paperId } = useParams<{ paperId: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { subscribeSSE } = useTaskStore();
  const [result, setResult] = useState<ReflectionResultResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [restarting, setRestarting] = useState(false);
  const [activeClaimId, setActiveClaimId] = useState<string | null>(null);
  const fetchingRef = useRef(false);
  const resultRef = useRef(result);
  useEffect(() => {
    resultRef.current = result;
  }, [result]);

  const fetchResult = useCallback(async () => {
    if (!paperId) return;
    if (fetchingRef.current) return;
    fetchingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const res = await getReflectionResult(paperId);
      setResult(res);
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      if (e?.response?.status === 404) {
        setError("该论文暂无 reflection 评审结果");
      } else {
        setError(e?.response?.data?.detail || "获取评审结果失败");
      }
    } finally {
      setLoading(false);
      fetchingRef.current = false;
    }
  }, [paperId]);

  useEffect(() => {
    if (!paperId) return;
    fetchResult();
    const timer = setInterval(() => {
      const current = resultRef.current;
      if (current && ACTIVE_STATUSES.has(current.status)) {
        fetchResult();
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [paperId, result?.status, fetchResult]);

  const handleRestart = async () => {
    if (!paperId) return;
    setRestarting(true);
    try {
      const res = await startReflectionReview(paperId);
      message.success(`重评已提交（task_id=${res.task_id}）`);
      subscribeSSE(
        res.task_id,
        undefined,
        () => {
          fetchResult();
          message.success("重评完成");
        },
        (info) => {
          fetchResult();
          message.error(info.error || "重评失败");
        },
      );
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      const status = e?.response?.status;
      const detail = e?.response?.data?.detail || e?.message || "重评失败";
      if (status === 409) {
        const firstLine = String(detail).split("\n")[0];
        message.warning(`已有进行中的任务。${firstLine}`, 4);
      } else {
        message.error(detail);
      }
    } finally {
      setRestarting(false);
    }
  };

  if (loading && !result) {
    return <Spin size="large" style={{ display: "block", margin: "100px auto" }} />;
  }

  if (error && !result) {
    return (
      <div style={{ maxWidth: 700, margin: "60px auto", textAlign: "center" }}>
        <Empty description={error}>
          <Button type="primary" icon={<Zap />} onClick={handleRestart} loading={restarting}>
            启动 reflection 评审
          </Button>
        </Empty>
      </div>
    );
  }

  if (!result) return null;

  const r = result.result;
  const scores = r?.scores;
  const llmFailed =
    Boolean(r?.llm_failed) ||
    Boolean(r?.analysis_v2?.llm_failed) ||
    result.status === "failed" ||
    result.status === "timed_out";
  const isActive = ACTIVE_STATUSES.has(result.status);
  const analysisScores = r?.analysis_v2 ?? r?.scores;
  const selectedEvidence =
    activeClaimId && r?.evidence_pool
      ? r.evidence_pool.filter(
          (item) => item.claim_ref === activeClaimId || r.claims.some(
            (claim) => claim.id === activeClaimId && claim.evidence_id === item.id,
          ),
        )
      : [];
  const coverage = r?.analysis_v2?.coverage ?? r?.coverage ?? r?.scores?.coverage;
  const coveredKeypoints = r?.coverage_covered ?? [];
  const uncoveredKeypoints = r?.coverage_uncovered ?? [];

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "24px 16px" }}>
      {/* 顶部状态栏 */}
      <Space style={{ marginBottom: 16 }}>
        <Button size="small" icon={<ArrowLeft />} onClick={() => navigate("/depth-v4?tab=report")}>
          返回报告列表
        </Button>
        {STATUS_ICONS[result.status]}
        <Text>
          {result.status === "running"
            ? "评审中..."
            : result.status === "completed"
              ? "评审完成"
              : result.status === "failed"
                ? "评审失败"
                : result.status === "timed_out"
                  ? "评审超时"
                  : "等待中"}
        </Text>
        {isActive && (
          <Button size="small" icon={<RefreshCw />} onClick={fetchResult}>
            刷新
          </Button>
        )}
        {!isActive && (
          <Button size="small" icon={<Zap />} loading={restarting} onClick={handleRestart}>
            重新评审
          </Button>
        )}
      </Space>

      {result.status === "failed" && (
        <Alert
          type="error"
          title="评审失败"
          description={result.error_message}
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {result.status === "timed_out" && (
        <Alert
          type="warning"
          title="评审超时"
          description={
            result.error_message || "任务超过最大执行时长，未生成可用结果；可以重新评审。"
          }
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {result.status === "running" && (
        <Alert
          type="info"
          title="reflection 评审进行中，请稍候..."
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {llmFailed && (
        <Alert
          type="error"
          title="评审无效：LLM 服务故障"
          description={
            result.error_message ||
            "本次没有生成可信评分。请检查模型服务后点击“重新评审”，不要将此结果视为报告质量结论。"
          }
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {result.status === "completed" && r && !llmFailed && (
        <>
          {/* 综合分 + 判决 */}
          <Card style={{ marginBottom: 16 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 24,
                flexWrap: "wrap",
              }}
            >
              <div style={{ flex: "1 1 300px", minWidth: 0 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  感悟报告深度分析
                </Text>
                <Title level={2} style={{ margin: "4px 0 8px" }}>
                  综合得分 {((r.analysis_v2?.average ?? scores?.average ?? 0) * 100).toFixed(0)}%
                </Title>
                <Tag
                  color={VERDICT_COLORS[r.verdict] || "default"}
                  style={{ fontSize: 16, padding: "4px 16px" }}
                >
                  {VERDICT_LABELS[r.verdict] || r.verdict}
                </Tag>
                {r.verdict_reason && (
                  <Paragraph type="secondary" style={{ margin: "12px 0 0", fontSize: 13 }}>
                    {r.verdict_reason}
                  </Paragraph>
                )}
              </div>
              <div style={{ flex: "0 0 190px", textAlign: "center" }}>
                <ReflectionRadar scores={analysisScores} size={174} showDetail />
              </div>
            </div>
            {/* 忠实度过低导致的 rewrite_required 警告 */}
            {r.verdict === "rewrite_required" && r.fidelity != null && r.fidelity < 0.3 && (
              <Alert
                type="warning"
                showIcon
                icon={<TriangleAlert />}
                title={`忠实度过低（${Math.round(r.fidelity * 100)}%）：报告内容与原论文匹配度不足 30%，大量论点疑似编造，系统判定需重写`}
                style={{ marginTop: 12, textAlign: "left" }}
              />
            )}
          </Card>

          {/* 6 维评分（analysis_v2）或 4 维评分（旧版） */}
          <Card
            title={r.analysis_v2 ? "6 维评分（含有据性与覆盖度）" : "4 维评分"}
            style={{ marginBottom: 16 }}
          >
            <ScoreBar
              label="理解准确性"
              score={(r.analysis_v2?.understanding_accuracy ?? scores?.understanding_accuracy) || 0}
              color="#1677ff"
              icon={<FlaskConical />}
            />
            <ScoreBar
              label="分析深度"
              score={(r.analysis_v2?.analysis_depth ?? scores?.analysis_depth) || 0}
              color="#722ed1"
              icon={<Lightbulb />}
            />
            <ScoreBar
              label="创新性见解"
              score={(r.analysis_v2?.innovative_insights ?? scores?.innovative_insights) || 0}
              color="#fa8c16"
              icon={<Rocket />}
            />
            <ScoreBar
              label="证据支撑"
              score={(r.analysis_v2?.evidence_support ?? scores?.evidence_support) || 0}
              color="#52c41a"
              icon={<Link />}
            />
            {(() => {
              const fs = r.fidelity_status;
              const hasScore = (r.analysis_v2?.fidelity ?? r.fidelity) != null;
              if (fs === "too_short") {
                return (
                  <div style={{ margin: "4px 0 12px" }}>
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      忠实度：正文过短，无法计算
                    </Text>
                    <div
                      style={{ fontSize: 12, color: "var(--pf-text-placeholder)", marginTop: 2 }}
                    >
                      报告正文解析后不足 100 字，比对无意义
                    </div>
                  </div>
                );
              }
              if (fs === "no_paper") {
                return (
                  <div style={{ margin: "4px 0 12px" }}>
                    <Text type="secondary" style={{ fontSize: 13 }}>
                      忠实度：未绑定原论文，无法计算
                    </Text>
                    <div
                      style={{ fontSize: 12, color: "var(--pf-text-placeholder)", marginTop: 2 }}
                    >
                      未识别 / 绑定到原论文，无法比对
                    </div>
                  </div>
                );
              }
              if (fs === "degraded_model") {
                return (
                  <ScoreBar
                    label="忠实度（退化模型，仅供参考）"
                    score={r.analysis_v2?.fidelity ?? r.fidelity ?? 0}
                    color="#faad14"
                    icon={<BadgeCheck />}
                  />
                );
              }
              if (hasScore) {
                return (
                  <ScoreBar
                    label={`忠实度${r.analysis_v2 ? "" : "（与原论文比对）"}`}
                    score={r.analysis_v2?.fidelity ?? r.fidelity ?? 0}
                    color={(() => {
                      const f = r.analysis_v2?.fidelity ?? r.fidelity ?? 0;
                      return f >= 0.4 ? "#52c41a" : f >= 0.3 ? "#faad14" : "#ff4d4f";
                    })()}
                    icon={<BadgeCheck />}
                  />
                );
              }
              return (
                <div style={{ margin: "4px 0 12px" }}>
                  <Text type="secondary" style={{ fontSize: 13 }}>
                    忠实度（与原论文比对）：未计算
                  </Text>
                  <div style={{ fontSize: 12, color: "var(--pf-text-placeholder)", marginTop: 2 }}>
                    未识别 / 绑定到原论文，无法比对
                  </div>
                </div>
              );
            })()}
            {r.analysis_v2 && (
              <>
                {r.analysis_v2.coverage != null && (
                  <ScoreBar
                    label="覆盖度（原论文 → 报告）"
                    score={r.analysis_v2.coverage}
                    color="#13c2c2"
                    icon={<FileSearch />}
                  />
                )}
                <div style={{ textAlign: "center", marginTop: 8 }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    6 维均权综合分：{Math.round((r.analysis_v2.average ?? 0) * 100)}%（含有据性与覆盖度）
                  </Text>
                </div>
              </>
            )}
          </Card>

          {/* 忠实度锚点句（fidelity_anchors） */}
          {r.fidelity_anchors && r.fidelity_anchors.length > 0 && (
            <Collapse
              style={{ marginBottom: 16 }}
              items={[
                {
                  key: "fidelity-anchors",
                  label: (
                    <Space>
                      <BadgeCheck />
                      忠实度锚点句 Top-{r.fidelity_anchors.length}（与原文相似度排序）
                    </Space>
                  ),
                  children: (
                    <Table
                      dataSource={r.fidelity_anchors.map((a: FidelityAnchor, i: number) => ({
                        key: i,
                        sentence: a.sentence,
                        sim: a.sim,
                      }))}
                      rowKey="key"
                      pagination={false}
                      size="small"
                      columns={[
                        {
                          title: "报告句子",
                          dataIndex: "sentence",
                          ellipsis: true,
                        },
                        {
                          title: "相似度",
                          dataIndex: "sim",
                          width: 90,
                          render: (v: number) => {
                            const pct = Math.round(v * 100);
                            return (
                              <Tag color={v >= 0.6 ? "green" : v >= 0.35 ? "gold" : "red"}>
                                {pct}%
                              </Tag>
                            );
                          },
                        },
                      ]}
                    />
                  ),
                },
              ]}
            />
          )}

          {/* 疑似编造句（fidelity_stray_claims） */}
          {coverage != null || coveredKeypoints.length > 0 || uncoveredKeypoints.length > 0 ? (
            <Card title="论文要点覆盖度" style={{ marginBottom: 16 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 12 }}>
                <Progress
                  type="circle"
                  percent={Math.round((coverage ?? 0) * 100)}
                  size={74}
                  strokeColor={(coverage ?? 0) >= 0.6 ? "#52c41a" : "#faad14"}
                />
                <div>
                  <Text strong>报告覆盖了多少原论文核心内容</Text>
                  <div style={{ color: "var(--pf-text-muted)", fontSize: 12, marginTop: 4 }}>
                    已覆盖 {coveredKeypoints.length} 项，仍有 {uncoveredKeypoints.length} 项值得补充
                  </div>
                </div>
              </div>
              {uncoveredKeypoints.length > 0 && (
                <Alert
                  type="warning"
                  showIcon
                  title="建议补充的原论文要点"
                  description={
                    <Space orientation="vertical" size={4} style={{ width: "100%" }}>
                      {uncoveredKeypoints.map((item, index) => (
                        <div key={`${item.keypoint}-${index}`}>
                          {item.keypoint} <Tag color="orange">相似度 {Math.round(item.sim * 100)}%</Tag>
                        </div>
                      ))}
                    </Space>
                  }
                />
              )}
            </Card>
          ) : null}

          {r.fidelity_stray_claims && r.fidelity_stray_claims.length > 0 && (
            <Card
              title={
                <Space>
                  <TriangleAlert style={{ color: "#ff4d4f" }} />
                  疑似编造句（{r.fidelity_stray_claims.length} 条）
                </Space>
              }
              style={{ marginBottom: 16, borderColor: "#ffccc7" }}
              styles={{ header: { background: "#fff2f0" } }}
            >
              <Alert
                type="warning"
                showIcon
                title="以下句子与原论文相似度过低（< 35%）且含绝对化断言/数字，疑似编造，请逐条核实"
                style={{ marginBottom: 12 }}
              />
              {r.fidelity_stray_claims.map((s: string, i: number) => (
                <div
                  key={i}
                  style={{
                    padding: "8px 12px",
                    background: "#fff2f0",
                    borderRadius: 6,
                    marginBottom: 6,
                    borderLeft: "3px solid #ff4d4f",
                    fontSize: 13,
                  }}
                >
                  <Text style={{ color: "#cf1322" }}>{s}</Text>
                </div>
              ))}
            </Card>
          )}
          {r.claims && r.claims.length > 0 && (
            <Card
              title="核心观点与证据链"
              extra={
                <Text type="secondary" style={{ fontSize: 12 }}>
                  点击观点查看对应原文证据
                </Text>
              }
              style={{ marginBottom: 16 }}
            >
              {r.claims.map((c: import("@/api/reflection").ReflectionClaim) => {
                const claimEvidenceCount = r.evidence_pool.filter(
                  (item) => item.claim_ref === c.id || item.id === c.evidence_id,
                ).length;
                const selected = activeClaimId === c.id;
                return (
                  <div
                    key={c.id}
                    role="button"
                    tabIndex={0}
                    aria-pressed={selected}
                    onClick={() => setActiveClaimId(selected ? null : c.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setActiveClaimId(selected ? null : c.id);
                      }
                    }}
                    style={{
                      padding: "10px 14px",
                      background: selected ? "var(--pf-primary-soft)" : "var(--pf-bg-tertiary)",
                      borderRadius: 6,
                      marginBottom: 8,
                      borderLeft: `3px solid ${selected ? "var(--pf-primary)" : "#1677ff"}`,
                      cursor: "pointer",
                      transition: "background 0.2s, border-color 0.2s",
                    }}
                  >
                    <Space wrap>
                      <Tag color="blue">{c.id}</Tag>
                      <span>{c.text}</span>
                      <Tag color={claimEvidenceCount > 0 ? "green" : "default"}>
                        {claimEvidenceCount} 条证据
                      </Tag>
                      {c.evidence_id && (
                        <Tooltip title="支撑该观点的证据 ID">
                          <Tag color="green" icon={<Link />}>
                            {c.evidence_id}
                          </Tag>
                        </Tooltip>
                      )}
                    </Space>
                  </div>
                );
              })}
              {activeClaimId && (
                <div
                  style={{
                    marginTop: 12,
                    padding: 12,
                    border: "1px solid var(--pf-border-light)",
                    borderRadius: 8,
                    background: "var(--pf-bg-primary)",
                  }}
                >
                  <Text strong>{activeClaimId} 的关联证据</Text>
                  {selectedEvidence.length > 0 ? (
                    selectedEvidence.map((item) => (
                      <div
                        key={item.id}
                        style={{
                          marginTop: 8,
                          padding: "8px 10px",
                          borderLeft: "3px solid #52c41a",
                          background: "var(--pf-bg-tertiary)",
                          fontSize: 13,
                        }}
                      >
                        <Tag color="green">{item.id}</Tag> {item.snippet}
                      </div>
                    ))
                  ) : (
                    <Text type="secondary" style={{ display: "block", marginTop: 8 }}>
                      后端没有为该观点绑定证据片段，请在原报告中补充引用或实验依据。
                    </Text>
                  )}
                </div>
              )}
            </Card>
          )}

          {r.evidence_pool && r.evidence_pool.length > 0 && (
            <Collapse
              style={{ marginBottom: 16 }}
              items={[
                {
                  key: "evidence",
                  label: (
                    <Space>
                      <FileSearch />
                      证据池 ({r.evidence_pool.length} 条原文片段)
                    </Space>
                  ),
                  children: (
                    <Table
                      dataSource={r.evidence_pool}
                      rowKey="id"
                      pagination={false}
                      size="small"
                      columns={[
                        { title: "ID", dataIndex: "id", width: 60 },
                        { title: "原文片段", dataIndex: "snippet" },
                        {
                          title: "支撑观点",
                          dataIndex: "claim_ref",
                          width: 100,
                          render: (ref: string | null) =>
                            ref ? <Tag color="blue">{ref}</Tag> : "-",
                        },
                      ]}
                    />
                  ),
                },
              ]}
            />
          )}

          {/* 总结 */}
          {r.summary && (
            <Card title="报告内容总结" style={{ marginBottom: 16 }}>
              <Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>{r.summary}</Paragraph>
            </Card>
          )}

          {/* 元信息 */}
          <Card title="元信息" size="small">
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="论文 ID">
                <Text copyable style={{ fontSize: 12 }}>
                  {result.paper_id}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="版本">{result.version}</Descriptions.Item>
              <Descriptions.Item label="创建时间">
                {result.created_at ? new Date(result.created_at).toLocaleString() : "-"}
              </Descriptions.Item>
              <Descriptions.Item label="完成时间">
                {result.completed_at ? new Date(result.completed_at).toLocaleString() : "-"}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </>
      )}
    </div>
  );
}
