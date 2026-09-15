/**
 * 论文实验审计页（CS Paper Experiment Auditor P0）。
 *
 * 四个视图：
 * 1. 论文审计：选论文 → 触发异步审计 → 轮询结果 → Finding 列表（severity
 *    色标 / 类型筛选 / 证据展开）→ HTML 报告下载。
 * 2. 数据泄漏初筛（P0-7）：独立端点，输入 train/test 目录。
 * 3. 跨论文改标比对（RELABELED_IMAGE_REUSE）：两篇论文 paper_id → 同步端点。
 * 4. 审计历史：列表 + 报告入口。
 *
 * 设计：所有 Finding 展示「良性解释」列，防止把审计误读为定罪工具。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Empty,
  Image,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  AuditOutlined,
  FileSearchOutlined,
  FireOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import type {
  AuditFinding,
  AuditListItem,
  AuditResult,
  FindingsSummary,
  FindingsSummaryPaper,
  FindingTypeMeta,
} from "@/api/experimentAudit";
import {
  auditReportUrl,
  getExperimentAuditResult,
  getFindingTypes,
  getFindingsSummary,
  listExperimentAudits,
  relabeledReuse,
  runLeakageCheck,
  startExperimentAudit,
} from "@/api/experimentAudit";
import { API_BASE } from "@/api/client";
import { fetchAllPapers } from "@/api/papers";
import type { Paper } from "@/api/types";

const { Text, Paragraph } = Typography;

const SEVERITY_META: Record<string, { color: string; label: string }> = {
  high: { color: "red", label: "高" },
  medium: { color: "orange", label: "中" },
  low: { color: "default", label: "低" },
};

// Finding 类型短中文标签（优先）+ 后端 description（tooltip）。
// 后端 FINDING_TYPES 已维护中文描述，但标签太长，这里用简短中文便于列表展示。
const FINDING_TYPE_LABELS: Record<string, string> = {
  NUMERIC_MISMATCH: "数字不一致",
  METRIC_INCONSISTENCY: "指标不自洽",
  ABLATION_UNSUPPORTED: "Ablation 无据",
  CHART_AXIS_RISK: "坐标轴风险",
  BASELINE_UNFAIR: "基线不公平",
  MISSING_REPRO_INFO: "缺少复现信息",
  DATA_LEAKAGE_CANDIDATE: "数据泄漏候选",
  UNCERTAINTY_MISSING: "缺少不确定度",
  SIGNIFICANCE_MISSING: "缺少显著性检验",
  CONFIG_MISMATCH: "超参不一致",
  FIGURE_REUSE_CANDIDATE: "图片复用候选",
  SUSPICIOUS_DATA_PATTERN: "可疑数据模式",
  REPRODUCTION_BLOCKER: "无法复现",
  CITATION_INTEGRITY: "引用问题",
  CLAIMS_EXTRACTION: "实验论断",
  TEXT_DUPLICATION_CANDIDATE: "文本重复候选",
  SEMANTIC_DUPLICATION_CANDIDATE: "语义重复候选",
  RELABELED_IMAGE_REUSE: "改标图片复用",
  GRIM_INCONSISTENCY: "GRIM 不一致",
  PCURVE_ANOMALY: "p 值分布异常",
  IMAGE_TAMPERING_CANDIDATE: "图片篡改候选",
  STD_OR_SIGNIFICANCE_MISSING: "标准差/显著性缺失",
};

/** 取 Finding 类型的中文短标签；优先用静态映射，再回退后端 description，最后保留原文。 */
function getFindingTypeLabel(type: string, catalog: FindingTypeMeta[] = []): string {
  if (FINDING_TYPE_LABELS[type]) return FINDING_TYPE_LABELS[type];
  const found = catalog.find((t) => t.type === type);
  if (found?.description) return found.description;
  return type;
}

/** 取 Finding 类型的详细中文描述（用于 tooltip）。 */
function getFindingTypeTooltip(type: string, catalog: FindingTypeMeta[] = []): string | undefined {
  const found = catalog.find((t) => t.type === type);
  return found?.description;
}

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

// 与 FigureSearchPage 一致：后端返回的相对 /api/... 地址需按 VITE_API_BASE
// 拼成完整地址，否则自定义 API 基址部署时 <img> 会指向错误源站。
function resolveAssetUrl(url: string): string {
  if (url.startsWith("/api")) return `${API_BASE}${url.slice("/api".length)}`;
  return url;
}

function FindingCard({ finding, catalog }: { finding: AuditFinding; catalog?: FindingTypeMeta[] }) {
  const sev = SEVERITY_META[finding.severity] ?? SEVERITY_META.low;
  const typeTooltip = getFindingTypeTooltip(finding.type, catalog);
  return (
    <Card size="small" style={{ marginBottom: 8 }} title={
      <Space wrap>
        <Text strong>{finding.finding_id}</Text>
        <Tag color={sev.color}>{sev.label}</Tag>
        <Tooltip title={typeTooltip}>
          <Tag>{getFindingTypeLabel(finding.type, catalog)}</Tag>
        </Tooltip>
        <Text>{finding.title}</Text>
        {finding.page != null && <Tag>p.{finding.page}</Tag>}
        {finding.needs_human_review && <Tag color="purple">需人工复核</Tag>}
      </Space>
    }>
      <Descriptions size="small" column={1} colon={false}>
        {finding.claim && <Descriptions.Item label="论文声称">{finding.claim}</Descriptions.Item>}
        {finding.computed && <Descriptions.Item label="审计计算">{finding.computed}</Descriptions.Item>}
        {finding.method && (
          <Descriptions.Item label="检测方法">
            <Text code>{finding.method}</Text>
          </Descriptions.Item>
        )}
        {finding.evidence_sources && finding.evidence_sources.length > 0 && (
          <Descriptions.Item label="证据">
            <Space orientation="vertical" size={4}>
              {finding.evidence_sources.map((e, i) => (
                <div key={i}>
                  <Text type="secondary">
                    {e.table_id ||
                      (e.other_figure_id ? `${e.figure_id} ↔ ${e.other_figure_id}` : e.figure_id) ||
                      `p.${e.page ?? "?"}`}
                    {/* 有标注图时不再展示 snippet（多为生成本地路径，对用户无意义） */}
                    {!e.image_url && e.snippet ? `：${e.snippet.slice(0, 120)}` : ""}
                  </Text>
                  {e.image_url && (
                    <div style={{ marginTop: 4 }}>
                      <Image
                        src={resolveAssetUrl(e.image_url)}
                        alt={`${e.figure_id ?? "figure"} 证据标注图`}
                        width={320}
                        style={{ borderRadius: 4, border: "1px solid #e8e8e8" }}
                      />
                    </div>
                  )}
                </div>
              ))}
            </Space>
          </Descriptions.Item>
        )}
        {finding.normal_explanation && (
          <Descriptions.Item label="良性解释">
            <Text type="warning">{finding.normal_explanation}</Text>
          </Descriptions.Item>
        )}
      </Descriptions>
    </Card>
  );
}

function FindingList({ findings, catalog }: { findings: AuditFinding[]; catalog?: FindingTypeMeta[] }) {
  const [sevFilter, setSevFilter] = useState<string>("all");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const types = Array.from(new Set(findings.map((f) => f.type)));
  const filtered = findings.filter(
    (f) =>
      (sevFilter === "all" || f.severity === sevFilter) &&
      (typeFilter === "all" || f.type === typeFilter),
  );
  if (!findings.length) return <Empty description="本次审计未发现问题" />;
  return (
    <>
      <Space style={{ marginBottom: 12 }} wrap>
        <Select
          size="small"
          value={sevFilter}
          onChange={setSevFilter}
          style={{ width: 130 }}
          options={[
            { value: "all", label: "全部严重度" },
            { value: "high", label: "高" },
            { value: "medium", label: "中" },
            { value: "low", label: "低" },
          ]}
        />
        <Select
          size="small"
          value={typeFilter}
          onChange={setTypeFilter}
          style={{ width: 280 }}
          options={[
            { value: "all", label: "全部类型" },
            ...types.map((t) => ({
              value: t,
              label: `${getFindingTypeLabel(t, catalog)} (${t})`,
            })),
          ]}
        />
        <Text type="secondary">{filtered.length} / {findings.length} 条</Text>
      </Space>
      {filtered.map((f) => (
        <FindingCard key={f.finding_id} finding={f} catalog={catalog} />
      ))}
    </>
  );
}

export default function ExperimentAuditPage() {
  // ── 论文审计 tab ──
  const [papers, setPapers] = useState<Paper[]>([]);
  const [papersTruncated, setPapersTruncated] = useState(false);
  const [paperId, setPaperId] = useState<string>();
  const [auditing, setAuditing] = useState(false);
  const [result, setResult] = useState<AuditResult | null>(null);
  const pollRef = useRef<number | null>(null);

  // ── 泄漏初筛 tab ──
  const [trainDir, setTrainDir] = useState("");
  const [testDir, setTestDir] = useState("");
  const [phashThreshold, setPhashThreshold] = useState(10);
  const [leakRunning, setLeakRunning] = useState(false);
  const [leakFindings, setLeakFindings] = useState<AuditFinding[] | null>(null);

  // ── 跨论文改标比对 tab ──
  const [relabelPaperA, setRelabelPaperA] = useState("");
  const [relabelPaperB, setRelabelPaperB] = useState("");
  const [relabelRunning, setRelabelRunning] = useState(false);
  const [relabelFindings, setRelabelFindings] = useState<AuditFinding[] | null>(null);
  const [relabelError, setRelabelError] = useState<string | null>(null);

  // ── 历史 tab ──
  const [history, setHistory] = useState<AuditListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // ── Finding 类型说明 ──
  const [findingTypes, setFindingTypes] = useState<FindingTypeMeta[]>([]);

  // ── 高危论文榜 tab ──
  const [summary, setSummary] = useState<FindingsSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summarySeverity, setSummarySeverity] = useState<"low" | "medium" | "high">("high");

  const loadFindingsSummary = useCallback((sev: "low" | "medium" | "high") => {
    setSummaryLoading(true);
    getFindingsSummary(sev)
      .then(setSummary)
      .catch(() => message.error("高危论文榜加载失败"))
      .finally(() => setSummaryLoading(false));
  }, []);

  useEffect(() => {
    fetchAllPapers()
      .then(({ items, truncated }) => {
        setPapers(items);
        setPapersTruncated(truncated);
      })
      .catch(() => message.error("论文列表加载失败"));
    getFindingTypes()
      .then(setFindingTypes)
      .catch(() => {});
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const refreshHistory = useCallback(
    (minSeverity?: "high" | "medium" | "low") => {
      setHistoryLoading(true);
      listExperimentAudits(30, 0, minSeverity)
        .then((r) => setHistory(r.items))
        .catch(() => {})
        .finally(() => setHistoryLoading(false));
    },
    [],
  );

  const pollResult = useCallback(
    (pid: string) => {
      const startedAt = Date.now();
      pollRef.current = window.setInterval(async () => {
        if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          setAuditing(false);
          message.error("审计超时，请稍后在历史列表查看");
          return;
        }
        try {
          const res = await getExperimentAuditResult(pid);
          if (res.status === "completed" || res.status === "failed") {
            if (pollRef.current) window.clearInterval(pollRef.current);
            setAuditing(false);
            setResult(res);
            if (res.status === "failed") message.error(res.error_message || "审计失败");
            refreshHistory();
          }
        } catch {
          // 尚无记录（404）→ 继续轮询
        }
      }, POLL_INTERVAL_MS);
    },
    [refreshHistory],
  );

  const handleStartAudit = async () => {
    if (!paperId) {
      message.warning("请先选择论文");
      return;
    }
    setAuditing(true);
    setResult(null);
    try {
      await startExperimentAudit(paperId);
      pollResult(paperId);
    } catch {
      setAuditing(false);
    }
  };

  const handleLoadLatest = async () => {
    if (!paperId) {
      message.warning("请先选择论文");
      return;
    }
    try {
      setResult(await getExperimentAuditResult(paperId));
    } catch {
      message.info("该论文尚无审计记录");
    }
  };

  const handleLeakage = async () => {
    if (!trainDir || !testDir) {
      message.warning("请填写 train 与 test 目录");
      return;
    }
    setLeakRunning(true);
    setLeakFindings(null);
    try {
      const resp = await runLeakageCheck({
        train_dir: trainDir,
        test_dir: testDir,
        phash_threshold: phashThreshold,
      });
      setLeakFindings(resp.findings);
      if (!resp.findings_count) message.success("未发现泄漏候选");
    } catch {
      // 错误 toast 由 http 拦截器处理
    } finally {
      setLeakRunning(false);
    }
  };

  const handleRelabeled = async () => {
    if (!relabelPaperA.trim() || !relabelPaperB.trim()) {
      message.warning("请填写两篇论文的 paper_id");
      return;
    }
    setRelabelRunning(true);
    setRelabelFindings(null);
    setRelabelError(null);
    try {
      const resp = await relabeledReuse(relabelPaperA.trim(), relabelPaperB.trim());
      setRelabelFindings(resp.findings);
      if (!resp.findings_count) message.success("未发现改标图片复用候选");
    } catch (err) {
      // 拦截器已弹 detail toast；这里保留可读错误态（404 论文不存在 / 400 图不足等）
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (err as Error)?.message ||
        "改标比对失败";
      setRelabelError(detail);
    } finally {
      setRelabelRunning(false);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <Tabs
        defaultActiveKey="audit"
        items={[
          {
            key: "audit",
            label: (
              <span><SafetyCertificateOutlined /> 论文实验审计</span>
            ),
            children: (
              <Space orientation="vertical" style={{ width: "100%" }} size="middle">
                <Card size="small">
                  <Space wrap>
                    <Select
                      showSearch
                      allowClear
                      placeholder="选择待审计论文"
                      style={{ width: 420 }}
                      value={paperId}
                      onChange={setPaperId}
                      filterOption={(input, opt) =>
                        String(opt?.label ?? "").toLowerCase().includes(input.toLowerCase())
                      }
                      options={papers.map((p) => ({
                        value: p.id,
                        label: `${p.title}${p.year ? ` (${p.year})` : ""}`,
                      }))}
                    />
                    <Button
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      loading={auditing}
                      onClick={handleStartAudit}
                    >
                      开始审计
                    </Button>
                    <Button onClick={handleLoadLatest} disabled={auditing}>
                      加载最近结果
                    </Button>
                    {result && (
                      <Button
                        icon={<FileSearchOutlined />}
                        onClick={() => window.open(auditReportUrl(result.audit_id), "_blank")}
                      >
                        查看 HTML 报告
                      </Button>
                    )}
                  </Space>
                  <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
                    检测项：数字一致性 / 指标自洽 / Ablation / 坐标轴风险 / 复现信息 /
                    Baseline 公平性 / 显著性缺失 / 曲线复用。全部本地运行；LLM 不可用时语义类检测自动跳过。
                  </Paragraph>
                  {papersTruncated && (
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginTop: 8 }}
                      title="论文库较大，下拉列表仅加载了前 1000 篇（按年份倒序）"
                      description="可在下拉框输入标题关键词精确筛选，或先到论文列表页处理更早的论文。"
                    />
                  )}
                </Card>

                {auditing && (
                  <Card size="small"><Spin description="审计进行中（表格提取 → 数值比对 → 图表审计）…" /></Card>
                )}

                {result?.status === "failed" && (
                  <Alert type="error" showIcon title={result.error_message || "审计失败"} />
                )}

                {result && result.status === "completed" && (
                  <>
                    <Card size="small">
                      <Space split="·" wrap>
                        <Text>{result.paper_title}</Text>
                        <Text type="secondary">完成于 {result.completed_at}</Text>
                        <Text type="secondary" copyable={{ text: result.source_pdf_hash }}>
                          SHA-256: {result.source_pdf_hash.slice(0, 16)}…
                        </Text>
                      </Space>
                      <Collapse
                        ghost
                        items={[{
                          key: "checks",
                          label: `检测项运行摘要（${result.checks_run.length}）`,
                          children: (
                            <Table
                              size="small"
                              rowKey="check"
                              pagination={false}
                              dataSource={result.checks_run}
                              columns={[
                                { title: "检测项", dataIndex: "check" },
                                {
                                  title: "状态",
                                  dataIndex: "status",
                                  render: (s: string) => (
                                    <Tag color={s === "ok" ? "green" : s === "skipped" ? "default" : "red"}>{s}</Tag>
                                  ),
                                },
                                { title: "耗时(ms)", dataIndex: "duration_ms", render: (v) => v ?? "-" },
                                { title: "发现", dataIndex: "findings", render: (v) => v ?? "-" },
                                { title: "备注", dataIndex: "reason", render: (v) => v || "-" },
                              ]}
                            />
                          ),
                        }]}
                      />
                    </Card>
                    <FindingList findings={result.findings} catalog={findingTypes} />
                  </>
                )}
              </Space>
            ),
          },
          {
            key: "leakage",
            label: <span><AuditOutlined /> 数据泄漏初筛</span>,
            children: (
              <Space orientation="vertical" style={{ width: "100%" }} size="middle">
                <Card size="small">
                  <Row gutter={12}>
                    <Col span={9}>
                      <Input
                        placeholder="train 目录绝对路径"
                        value={trainDir}
                        onChange={(e) => setTrainDir(e.target.value)}
                      />
                    </Col>
                    <Col span={9}>
                      <Input
                        placeholder="test 目录绝对路径"
                        value={testDir}
                        onChange={(e) => setTestDir(e.target.value)}
                      />
                    </Col>
                    <Col span={3}>
                      <InputNumber
                        min={0}
                        max={64}
                        value={phashThreshold}
                        onChange={(v) => setPhashThreshold(v ?? 10)}
                        addonBefore="pHash 阈值"
                        style={{ width: "100%" }}
                      />
                    </Col>
                    <Col span={3}>
                      <Button type="primary" block loading={leakRunning} onClick={handleLeakage}>
                        开始筛查
                      </Button>
                    </Col>
                  </Row>
                  <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
                    SHA-256 精确碰撞 + pHash 近似重复（独立于论文 PDF，针对本机数据集目录；
                    建议样本数 ≤ 2000 张）。
                  </Paragraph>
                </Card>
                {leakFindings !== null && <FindingList findings={leakFindings} catalog={findingTypes} />}
              </Space>
            ),
          },
          {
            key: "relabeled",
            label: <span><SwapOutlined /> 跨论文改标比对</span>,
            children: (
              <Space orientation="vertical" style={{ width: "100%" }} size="middle">
                <Card size="small">
                  <Row gutter={12}>
                    <Col span={10}>
                      <Input
                        placeholder="论文 A 的 paper_id（如 fraud_berberine）"
                        value={relabelPaperA}
                        onChange={(e) => setRelabelPaperA(e.target.value)}
                      />
                    </Col>
                    <Col span={10}>
                      <Input
                        placeholder="论文 B 的 paper_id（如 kjpp）"
                        value={relabelPaperB}
                        onChange={(e) => setRelabelPaperB(e.target.value)}
                      />
                    </Col>
                    <Col span={4}>
                      <Button
                        type="primary"
                        block
                        loading={relabelRunning}
                        onClick={handleRelabeled}
                      >
                        开始比对
                      </Button>
                    </Col>
                  </Row>
                  <Paragraph
                    type="secondary"
                    style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}
                  >
                    跨论文改标图片复用证据链：NCC 条带像素复用 + Qwen3-VL 目标蛋白标签比对，
                    两图标签无交集时出 RELABELED_IMAGE_REUSE。需两篇论文均已上传并抽取
                    figure；含 VLM 调用，可能耗时数十秒。
                  </Paragraph>
                </Card>
                {relabelRunning && (
                  <Card size="small">
                    <Spin description="跨论文改标比对中（NCC 像素比对 + 视觉模型标签提取）…" />
                  </Card>
                )}
                {relabelError && <Alert type="error" showIcon title={relabelError} />}
                {relabelFindings !== null && !relabelRunning && (
                  <FindingList findings={relabelFindings} catalog={findingTypes} />
                )}
              </Space>
            ),
          },
          {
            key: "history",
            label: <span><HistoryOutlined /> 审计历史</span>,
            children: (
              <>
                <Space style={{ marginBottom: 8 }}>
                  <Text type="secondary">最低严重度：</Text>
                  <Select
                    size="small"
                    style={{ width: 120 }}
                    defaultValue={undefined}
                    allowClear
                    placeholder="全部"
                    options={[
                      { value: "high", label: "高" },
                      { value: "medium", label: "中及以上" },
                      { value: "low", label: "全部级别" },
                    ]}
                    onChange={(v) => refreshHistory(v)}
                  />
                </Space>
                <Table
                  rowKey="audit_id"
                  size="small"
                  loading={historyLoading}
                  dataSource={history}
                  columns={[
                    { title: "论文", dataIndex: "paper_title", ellipsis: true },
                    {
                      title: "状态",
                      dataIndex: "status",
                      width: 100,
                      render: (s: string) => (
                        <Tag color={s === "completed" ? "green" : s === "failed" ? "red" : "blue"}>{s}</Tag>
                      ),
                    },
                    { title: "发现数", dataIndex: "findings_count", width: 90 },
                    { title: "时间", dataIndex: "created_at", width: 170 },
                    {
                      title: "操作",
                      width: 120,
                      render: (_: unknown, row: AuditListItem) => (
                        <Button
                          size="small"
                          type="link"
                          onClick={() => window.open(auditReportUrl(row.audit_id), "_blank")}
                        >
                          查看报告
                        </Button>
                      ),
                    },
                  ]}
                  pagination={{ pageSize: 15 }}
                />
              </>
            ),
          },
          {
            key: "types",
            label: <span><FileSearchOutlined /> Finding 类型说明</span>,
            children: (
              <Table
                rowKey="type"
                size="small"
                dataSource={findingTypes}
                pagination={false}
                columns={[
                  { title: "类型", dataIndex: "type", width: 240 },
                  {
                    title: "严重度",
                    dataIndex: "severity",
                    width: 90,
                    render: (s: string) => <Tag color={SEVERITY_META[s]?.color}>{SEVERITY_META[s]?.label ?? s}</Tag>,
                  },
                  { title: "描述", dataIndex: "description" },
                  { title: "示例", dataIndex: "example", render: (v: string) => <Text type="secondary">{v}</Text> },
                ]}
              />
            ),
          },
          {
            key: "summary",
            label: <span><FireOutlined /> 高危论文榜</span>,
            children: (
              <Space orientation="vertical" style={{ width: "100%" }} size="middle">
                <Card size="small">
                  <Space wrap>
                    <Text type="secondary">最低严重度：</Text>
                    <Select
                      size="small"
                      style={{ width: 140 }}
                      value={summarySeverity}
                      onChange={(v: "low" | "medium" | "high") => {
                        setSummarySeverity(v);
                        loadFindingsSummary(v);
                      }}
                      options={[
                        { value: "high", label: "高" },
                        { value: "medium", label: "中及以上" },
                        { value: "low", label: "全部级别" },
                      ]}
                    />
                    <Text type="secondary">
                      {summary
                        ? `共 ${summary.papers_with_findings} 篇含发现 · ${summary.total_findings} 条发现`
                        : ""}
                    </Text>
                  </Space>
                  {summary && summary.total_findings > 0 && (
                    <div style={{ marginTop: 8 }}>
                      {Object.entries(summary.by_type).map(([type, count]) => (
                        <Tooltip key={type} title={getFindingTypeTooltip(type, findingTypes)}>
                          <Tag style={{ marginBottom: 4 }}>
                            {getFindingTypeLabel(type, findingTypes)}: {count}
                          </Tag>
                        </Tooltip>
                      ))}
                    </div>
                  )}
                </Card>
                <Table
                  rowKey="paper_id"
                  size="small"
                  loading={summaryLoading}
                  dataSource={summary?.papers ?? []}
                  locale={{ emptyText: <Empty description={summaryLoading ? "加载中…" : "无符合条件的发现"} /> }}
                  pagination={{ pageSize: 15 }}
                  columns={[
                    { title: "论文", dataIndex: "paper_title", ellipsis: true },
                    {
                      title: "发现数",
                      dataIndex: "findings_count",
                      width: 90,
                      sorter: (a: FindingsSummaryPaper, b: FindingsSummaryPaper) => a.findings_count - b.findings_count,
                      defaultSortOrder: "descend",
                    },
                    {
                      title: "类型分布",
                      dataIndex: "types",
                      render: (t: Record<string, number>) =>
                        Object.entries(t).map(([type, count]) => (
                          <Tooltip key={type} title={getFindingTypeTooltip(type, findingTypes)}>
                            <Tag>{getFindingTypeLabel(type, findingTypes)} ×{count}</Tag>
                          </Tooltip>
                        )),
                    },
                  ]}
                />
              </Space>
            ),
          },
        ]}
        onChange={(k) => {
          if (k === "history") refreshHistory();
          if (k === "summary") loadFindingsSummary(summarySeverity);
        }}
      />
    </div>
  );
}
