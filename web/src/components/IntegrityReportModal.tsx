/**
 * IntegrityReportModal：学生「AI 使用声明 + 真实性报告」一页纸预览。
 *
 * - 打开时拉取 GET /api/reports/{paper_id}/integrity 的 JSON 渲染预览
 * - 「导出 Word」→ 下载后端 python-docx 生成的 .docx
 * - 「打印 / 存 PDF」→ 拉取后端打印版 HTML，新窗口 window.print()
 *
 * 报告为证据性文档，所有数字来自后端确定性生成，前端不做任何计算。
 */
import { useEffect, useState } from "react";
import {
  message,
  Alert,
  Button,
  Descriptions,
  Modal,
  Result,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";
import { Download, Printer, ShieldCheck } from "lucide-react";

import {
  downloadIntegrityDocx,
  fetchIntegrityHtml,
  getIntegrityReport,
  type IntegrityReport,
} from "@/api/reports";

const { Text, Title } = Typography;

const VERDICT_COLORS: Record<string, string> = {
  well_done: "green",
  needs_evidence: "gold",
  needs_depth: "orange",
  rewrite_required: "red",
};

const VERDICT_LABELS: Record<string, string> = {
  well_done: "优秀",
  needs_evidence: "证据不足",
  needs_depth: "深度不足",
  rewrite_required: "需重写",
};

/** 0~1 分数 → 百分比（null → "—"） */
function fmtScore(v: number | null | undefined): string {
  if (v == null || Number.isNaN(Number(v))) return "—";
  return `${Math.round(Number(v) * 100)}%`;
}

/** 维度 key → 中文名（未知 key 原样显示） */
function dimLabel(key: string): string {
  const map: Record<string, string> = {
    understanding_accuracy: "理解准确性",
    analysis_depth: "分析深度",
    innovative_insights: "创新见解",
    evidence_support: "证据支撑",
    fidelity: "忠实度（报告→论文）",
    coverage: "覆盖度（论文→报告）",
    average: "平均分",
  };
  return map[key] ?? key;
}

interface Props {
  /** 当前要查看的 report paper_id；null 表示不打开 */
  paperId: string | null;
  open: boolean;
  onClose: () => void;
}

export default function IntegrityReportModal({ paperId, open, onClose }: Props) {
  const [report, setReport] = useState<IntegrityReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState<"" | "docx" | "html">("");

  useEffect(() => {
    if (!open || !paperId) return;
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    setReport(null);
    getIntegrityReport(paperId)
      .then((r) => {
        if (!cancelled) setReport(r);
      })
      .catch(() => {
        if (!cancelled) setLoadError("获取诚信报告失败：没有该报告的评审记录，或服务异常");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, paperId]);

  const handleExportDocx = async () => {
    if (!paperId) return;
    setBusy("docx");
    try {
      await downloadIntegrityDocx(paperId);
      message.success("诚信报告（Word）已开始下载");
    } catch {
      message.error("导出 Word 失败");
    } finally {
      setBusy("");
    }
  };

  const handlePrintPdf = async () => {
    if (!paperId) return;
    setBusy("html");
    try {
      const html = await fetchIntegrityHtml(paperId);
      const win = window.open("", "_blank");
      if (!win) {
        message.error("浏览器拦截了弹出窗口，请允许后重试");
        return;
      }
      win.document.write(html);
      win.document.close();
      win.focus();
      // 等样式渲染完成后再调打印，避免首屏空白
      setTimeout(() => win.print(), 300);
    } catch {
      message.error("打开打印页失败");
    } finally {
      setBusy("");
    }
  };

  const student = report?.student ?? { id: "", name: "" };
  const src = report?.source_paper;
  const aiUse = report?.ai_use;
  const auth = report?.authenticity;
  const scoring = report?.scoring;
  const scoreRows = Object.entries(scoring?.scores ?? {}).map(([key, val]) => ({
    key,
    dim: dimLabel(key),
    score: fmtScore(val),
    weight: scoring?.weights?.[key] != null ? scoring.weights[key].toFixed(2) : "—",
  }));

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={760}
      footer={null}
      title={
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <ShieldCheck style={{ color: "var(--pf-primary)" }} />
          诚信报告
        </span>
      }
      styles={{ body: { maxHeight: "70vh", overflowY: "auto", paddingTop: 8 } }}
    >
      {loading ? (
        <div style={{ textAlign: "center", padding: "48px 0" }}>
          <Spin />
        </div>
      ) : loadError ? (
        <Result status="warning" title="无法生成诚信报告" subTitle={loadError} />
      ) : report && scoring ? (
        <div>
          {/* 头部 */}
          <Title level={4} style={{ marginBottom: 4, color: "var(--pf-primary)" }}>
            AI 使用声明与真实性报告
          </Title>
          <Text type="secondary" style={{ fontSize: 12 }}>
            PaperForge 自动生成 · 数据可溯源至本地数据库 · 供教学参考
          </Text>
          <Descriptions
            size="small"
            column={2}
            style={{ marginTop: 12 }}
            items={[
              { key: "sid", label: "学号", children: student.id || "—" },
              { key: "name", label: "姓名", children: student.name || "—" },
              { key: "title", label: "报告", children: report.title || "—", span: 2 },
              {
                key: "src",
                label: "原论文",
                children: src?.title ? `${src.title}（${src.author || "佚名"}）` : "—",
                span: 2,
              },
              {
                key: "time",
                label: "评审时间",
                children: report.evaluated_at || "—",
              },
              {
                key: "gen",
                label: "生成时间",
                children: report.generated_at || "—",
              },
            ]}
          />

          {/* ① AI 使用声明 */}
          <SectionTitle>一、AI 使用声明</SectionTitle>
          <Text style={{ fontSize: 13 }}>{aiUse?.declaration_text}</Text>
          {aiUse?.advisory && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 8 }}
              message={`AI 生成疑似度：${fmtScore(aiUse.advisory.ai_likelihood)}（${aiUse.advisory.tier_label || aiUse.advisory.tier}）`}
              description={aiUse.advisory_note}
            />
          )}

          {/* ② 真实性核验 */}
          <SectionTitle>二、真实性核验（报告 ↔ 原论文）</SectionTitle>
          <Descriptions
            size="small"
            column={3}
            items={[
              {
                key: "fid",
                label: "忠实度 fidelity",
                children: `${fmtScore(auth?.fidelity)}（${auth?.fidelity_status || "—"}）`,
              },
              {
                key: "cov",
                label: "覆盖度 coverage",
                children: `${fmtScore(auth?.coverage)}（${auth?.coverage_status || "—"}）`,
              },
              {
                key: "copy",
                label: "照抄率 copy_ratio",
                children: fmtScore(auth?.copy_ratio),
              },
            ]}
          />
          {auth?.coverage_uncovered?.length ? (
            <Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 4 }}>
              未覆盖的核心要点：{auth.coverage_uncovered.slice(0, 5).join("；")}
            </Text>
          ) : null}
          {auth?.stray_claims?.length ? (
            <Text type="warning" style={{ fontSize: 12, display: "block", marginTop: 4 }}>
              无出处论点 {auth.stray_claims.length} 条：{auth.stray_claims.slice(0, 3).join("；")}
            </Text>
          ) : null}

          {/* ③ 评分 */}
          <SectionTitle>三、评分（平均分 = Σ 分数 × 权重）</SectionTitle>
          <Table
            size="small"
            rowKey="key"
            pagination={false}
            dataSource={scoreRows}
            columns={[
              { title: "维度", dataIndex: "dim" },
              { title: "分数", dataIndex: "score", width: 100, align: "center" },
              { title: "权重", dataIndex: "weight", width: 90, align: "center" },
            ]}
          />
          <div style={{ marginTop: 8 }}>
            <Text strong>平均分：{fmtScore(scoring.average)}</Text>
            <Tag color={VERDICT_COLORS[scoring.verdict] || "default"} style={{ marginLeft: 8 }}>
              {scoring.verdict_label || VERDICT_LABELS[scoring.verdict] || scoring.verdict}
            </Tag>
          </div>
          {scoring.verdict_reason ? (
            <Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 4 }}>
              结论依据：{scoring.verdict_reason}
            </Text>
          ) : null}
          {scoring.trust_warnings?.map((w) => (
            <Alert key={w} type="error" showIcon message={w} style={{ marginTop: 8 }} />
          ))}
          {scoring.hardcoded_overrides?.map((o) => (
            <Text key={o} type="secondary" style={{ fontSize: 12, display: "block" }}>
              · {o}
            </Text>
          ))}

          {/* 页脚 + 操作 */}
          <Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 16 }}>
            本报告由 PaperForge 根据本地评审记录自动生成，供教学参考；AI 生成疑似度为事后弱信号，
            不构成对学生的处分依据。
          </Text>
          <div style={{ marginTop: 12, display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <Button icon={<Printer />} loading={busy === "html"} onClick={handlePrintPdf}>
              打印 / 存 PDF
            </Button>
            <Button
              type="primary"
              icon={<Download />}
              loading={busy === "docx"}
              onClick={handleExportDocx}
            >
              导出 Word
            </Button>
          </div>
        </div>
      ) : (
        <Result status="error" title="未知错误" />
      )}
    </Modal>
  );
}

/** 一页纸分节标题 */
function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        marginTop: 16,
        marginBottom: 8,
        fontWeight: 600,
        fontSize: 14,
        color: "var(--pf-primary)",
        borderLeft: "3px solid var(--pf-primary)",
        paddingLeft: 8,
      }}
    >
      {children}
    </div>
  );
}
