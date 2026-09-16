import { ArrowLeft, Download, Table as TableIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useCallback, useState } from "react";
import { Button, Card, Empty, Input, Select, Space, Spin, Table, Typography, App } from "antd";

import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { fetchComparePapers, fetchPapers } from "@/api/papers";
import PageHeader from "@/components/PageHeader";
import type { ComparisonRow } from "@/api/types";

const { TextArea } = Input;

interface PaperOption {
  label: string;
  value: string;
}

export default function ComparePapersPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<ComparisonRow[]>([]);
  const [generatedAt, setGeneratedAt] = useState<string>("");
  const [modelUsed, setModelUsed] = useState<string>("");
  const [paperOptions, setPaperOptions] = useState<PaperOption[]>([]);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const loadOptions = useCallback(async (keyword: string) => {
    setOptionsLoading(true);
    try {
      const result = await fetchPapers({ keyword, pageSize: 50 });
      setPaperOptions(
        result.items.map((p) => ({
          label: `${p.title} (${p.year || "N/A"})`,
          value: p.id,
        })),
      );
    } catch {
      message.error(t("compare.loadError"));
    } finally {
      setOptionsLoading(false);
    }
  }, [t, message]);

  useEffect(() => {
    loadOptions("");
    return () => {
      if (searchTimer.current) clearTimeout(searchTimer.current);
    };
  }, [loadOptions]);

  const handleSearch = (value: string) => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      loadOptions(value);
    }, 300);
  };

  const handleCompare = async () => {
    if (selectedIds.length < 2) {
      message.warning(t("compare.selectAtLeastTwo"));
      return;
    }
    setLoading(true);
    setRows([]);
    try {
      const res = await fetchComparePapers({
        paper_ids: selectedIds,
        question: question.trim() || undefined,
      });
      const returnedIds = new Set(res.rows.map((r) => r.paper_id));
      const missing = selectedIds.filter((id) => !returnedIds.has(id));
      if (missing.length > 0) {
        message.warning(t("compare.partialResult", { count: missing.length }));
      }
      setRows(res.rows);
      setGeneratedAt(res.generated_at);
      setModelUsed(res.model);
    } catch (err0: unknown) {
      const err = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error(err?.message || t("compare.compareError"));
    } finally {
      setLoading(false);
    }
  };

  const escapeMd = (text: string) =>
    String(text || "-")
      .replace(/\|/g, "\\|")
      .replace(/\n/g, " ");

  const handleExportMarkdown = () => {
    if (!rows.length) return;
    const lines: string[] = [];
    lines.push("# " + t("compare.title"));
    lines.push("");
    lines.push(
      "| " +
        t("compare.fieldTitle") +
        " | " +
        rows.map((r) => escapeMd(r.title)).join(" | ") +
        " |",
    );
    lines.push("| " + rows.map(() => "---").join(" | ") + " |");
    const fields: { key: keyof ComparisonRow; label: string }[] = [
      { key: "authors", label: t("compare.fieldAuthors") },
      { key: "year", label: t("compare.fieldYear") },
      { key: "method", label: t("compare.fieldMethod") },
      { key: "sample_size", label: t("compare.fieldSampleSize") },
      { key: "main_results", label: t("compare.fieldMainResults") },
      { key: "metrics", label: t("compare.fieldMetrics") },
      { key: "limitations", label: t("compare.fieldLimitations") },
      { key: "conclusion", label: t("compare.fieldConclusion") },
    ];
    for (const { key, label } of fields) {
      const values = rows.map((r) => {
        const raw = r[key];
        let text: string;
        if (Array.isArray(raw)) {
          text = raw.join(", ") || "-";
        } else {
          text = String(raw || "-");
        }
        return escapeMd(text);
      });
      lines.push(`| ${label} | ${values.join(" | ")} |`);
    }
    lines.push("");
    if (generatedAt) {
      lines.push(`*${t("compare.generatedAt")}: ${generatedAt}*`);
    }
    const blob = new Blob([lines.join("\n")], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "comparison.md";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    message.success(t("compare.exportedMarkdown"));
  };

  const columns = useMemo(() => {
    const base = [
      {
        title: t("compare.fieldTitle"),
        dataIndex: "title",
        key: "title",
        width: 220,
        fixed: "left" as const,
        render: (text: string, record: ComparisonRow) => (
          <Typography.Link onClick={() => navigate(`/paper/${record.paper_id}`)}>
            {text}
          </Typography.Link>
        ),
      },
      {
        title: t("compare.fieldAuthors"),
        dataIndex: "authors",
        key: "authors",
        width: 160,
        render: (authors: string[]) => (authors || []).join(", ") || "-",
      },
      {
        title: t("compare.fieldYear"),
        dataIndex: "year",
        key: "year",
        width: 90,
        sorter: (a: ComparisonRow, b: ComparisonRow) => (a.year || 0) - (b.year || 0),
      },
      {
        title: t("compare.fieldMethod"),
        dataIndex: "method",
        key: "method",
        width: 220,
        filters: Array.from(new Set(rows.map((r) => r.method).filter(Boolean))).map((v) => ({
          text: v,
          value: v,
        })),
        onFilter: (value: React.Key | boolean, record: ComparisonRow) =>
          (record.method || "").includes(value as string),
      },
      {
        title: t("compare.fieldSampleSize"),
        dataIndex: "sample_size",
        key: "sample_size",
        width: 180,
      },
      {
        title: t("compare.fieldMainResults"),
        dataIndex: "main_results",
        key: "main_results",
        width: 260,
      },
      {
        title: t("compare.fieldMetrics"),
        dataIndex: "metrics",
        key: "metrics",
        width: 160,
        filters: Array.from(new Set(rows.map((r) => r.metrics).filter(Boolean))).map((v) => ({
          text: v,
          value: v,
        })),
        onFilter: (value: React.Key | boolean, record: ComparisonRow) =>
          (record.metrics || "").includes(value as string),
      },
      {
        title: t("compare.fieldLimitations"),
        dataIndex: "limitations",
        key: "limitations",
        width: 220,
      },
      {
        title: t("compare.fieldConclusion"),
        dataIndex: "conclusion",
        key: "conclusion",
        width: 240,
        filters: Array.from(new Set(rows.map((r) => r.conclusion).filter(Boolean))).map((v) => ({
          text: v,
          value: v,
        })),
        onFilter: (value: React.Key | boolean, record: ComparisonRow) =>
          (record.conclusion || "").includes(value as string),
      },
    ];
    return base;
  }, [t, navigate, rows]);

  return (
    <div className="pf-page-wide">
      <PageHeader
        title={t("compare.title")}
        description={t("compare.subtitle")}
        actions={
          <Button icon={<ArrowLeft />} onClick={() => navigate(-1)}>
            {t("common.back")}
          </Button>
        }
      />

      <Card className="pf-glass-card" variant="borderless" style={{ marginBottom: 20 }}>
        <Space orientation="vertical" style={{ width: "100%" }} size="large">
          <div>
            <Typography.Text strong>{t("compare.selectPapers")}</Typography.Text>
            <Select
              mode="multiple"
              allowClear
              showSearch
              filterOption={false}
              placeholder={t("compare.selectPlaceholder")}
              value={selectedIds}
              onChange={(value) => setSelectedIds(value.slice(0, 20))}
              onSearch={handleSearch}
              loading={optionsLoading}
              options={paperOptions}
              style={{ width: "100%", marginTop: 8 }}
              maxTagCount="responsive"
            />
          </div>

          <div>
            <Typography.Text strong>{t("compare.question")}</Typography.Text>
            <TextArea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder={t("compare.questionPlaceholder")}
              rows={2}
              style={{ marginTop: 8 }}
            />
          </div>

          <Button
            type="primary"
            icon={<TableIcon />}
            onClick={handleCompare}
            loading={loading}
            disabled={selectedIds.length < 2}
          >
            {t("compare.startCompare")}
          </Button>
        </Space>
      </Card>

      {loading && (
        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ textAlign: "center", padding: 48 }}
        >
          <Spin size="large" />
          <div style={{ marginTop: 16, color: "var(--pf-text-muted)" }}>{t("compare.loading")}</div>
        </Card>
      )}

      {!loading && rows.length > 0 && (
        <Card
          className="pf-glass-card"
          variant="borderless"
          title={
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>{t("compare.resultTitle")}</span>
              <Space>
                {modelUsed && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t("compare.modelUsed")}: {modelUsed}
                  </Typography.Text>
                )}
                <Button icon={<Download />} onClick={handleExportMarkdown}>
                  {t("compare.exportMarkdown")}
                </Button>
              </Space>
            </div>
          }
        >
          <Table
            dataSource={rows}
            columns={columns}
            rowKey="paper_id"
            scroll={{ x: "max-content" }}
            pagination={false}
            bordered
            size="small"
          />
        </Card>
      )}

      {!loading && rows.length === 0 && (
        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ textAlign: "center", padding: 48 }}
        >
          <Empty description={t("compare.empty")} />
        </Card>
      )}
    </div>
  );
}
