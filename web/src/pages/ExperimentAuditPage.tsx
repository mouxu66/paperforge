/**
 * 论文实验审计页（CS Paper Experiment Auditor P0）。
 *
 * 三个视图：
 * 1. 论文审计：选论文 → 触发异步审计 → 轮询结果 → Finding 列表（severity
 *    色标 / 类型筛选 / 证据展开）→ HTML 报告下载。
 * 2. 数据泄漏初筛（P0-7）：独立端点，输入 train/test 目录。
 * 3. 审计历史：列表 + 报告入口。
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
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  AuditOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  PlayCircleOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import type {
  AuditFinding,
  AuditListItem,
  AuditResult,
  FindingTypeMeta,
} from "@/api/experimentAudit";
import {
  auditReportUrl,
  getExperimentAuditResult,
  getFindingTypes,
  listExperimentAudits,
  runLeakageCheck,
  startExperimentAudit,
} from "@/api/experimentAudit";
import { fetchAllPapers } from "@/api/papers";
import type { Paper } from "@/api/types";

const { Text, Paragraph } = Typography;

const SEVERITY_META: Record<string, { color: string; label: string }> = {
  high: { color: "red", label: "高" },
  medium: { color: "orange", label: "中" },
  low: { color: "default", label: "低" },
};

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 5 * 60 * 1000;

function FindingCard({ finding }: { finding: AuditFinding }) {
  const sev = SEVERITY_META[finding.severity] ?? SEVERITY_META.low;
  return (
    <Card size="small" style={{ marginBottom: 8 }} title={
      <Space wrap>
        <Text strong>{finding.finding_id}</Text>
        <Tag color={sev.color}>{sev.label}</Tag>
        <Tag>{finding.type}</Tag>
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
            <Space orientation="vertical" size={0}>
              {finding.evidence_sources.map((e, i) => (
                <Text key={i} type="secondary">
                  {e.table_id || e.figure_id || `p.${e.page ?? "?"}`}
                  {e.snippet ? `：${e.snippet.slice(0, 120)}` : ""}
                </Text>
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

function FindingList({ findings }: { findings: AuditFinding[] }) {
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
          style={{ width: 260 }}
          options={[{ value: "all", label: "全部类型" }, ...types.map((t) => ({ value: t, label: t }))]}
        />
        <Text type="secondary">{filtered.length} / {findings.length} 条</Text>
      </Space>
      {filtered.map((f) => (
        <FindingCard key={f.finding_id} finding={f} />
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

  // ── 历史 tab ──
  const [history, setHistory] = useState<AuditListItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // ── Finding 类型说明 ──
  const [findingTypes, setFindingTypes] = useState<FindingTypeMeta[]>([]);

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

  const refreshHistory = useCallback(() => {
    setHistoryLoading(true);
    listExperimentAudits(30)
      .then((r) => setHistory(r.items))
      .catch(() => {})
      .finally(() => setHistoryLoading(false));
  }, []);

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
                    <FindingList findings={result.findings} />
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
                {leakFindings !== null && <FindingList findings={leakFindings} />}
              </Space>
            ),
          },
          {
            key: "history",
            label: <span><HistoryOutlined /> 审计历史</span>,
            children: (
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
        ]}
        onChange={(k) => {
          if (k === "history") refreshHistory();
        }}
      />
    </div>
  );
}
