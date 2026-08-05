import { Pencil, Download, FileText, Table, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Typography, Input, Button, Card, Space, Tag, Empty, Spin, message, Modal } from "antd";

import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { fetchGenerate } from "@/api/generate";
import type { GenerateResponse } from "@/api/types";
import { Link } from "react-router-dom";
import ReferenceCard from "@/components/ReferenceCard";
import PageHeader from "@/components/PageHeader";
import { exportMarkdown, exportWord, exportLatex, exportPdf } from "@/utils/export";
import { useModelStore } from "@/store/useModelStore";

const { TextArea } = Input;

export default function GeneratePage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<GenerateResponse | null>(null);
  const markdownRef = useRef<HTMLDivElement>(null);
  const modelAvailable = useModelStore((s) => s.available);
  const modelCurrent = useModelStore((s) => s.current);
  const modelLoad = useModelStore((s) => s.load);
  const hasModel = (modelAvailable?.length ?? 0) > 0 || !!modelCurrent;

  useEffect(() => {
    void modelLoad();
  }, [modelLoad]);

  const handleGenerate = async () => {
    const topicTrimmed = topic.trim();
    if (!topicTrimmed || loading) return;
    // P1 守卫：未配置模型时引导用户去模型管理，而非静默发起注定 502 的请求。
    if (!hasModel) {
      Modal.confirm({
        title: t("ask.noModelTitle"),
        content: t("ask.noModelDesc"),
        okText: t("ask.goToModels"),
        cancelText: t("common.cancel"),
        onOk: () => navigate("/models"),
      });
      return;
    }
    setLoading(true);
    setResult(null);
    try {
      const res = await fetchGenerate(topicTrimmed);
      setResult(res);
    } catch {
      // 错误已在 generateHttp 拦截器中提示
    } finally {
      setLoading(false);
    }
  };

  const filename = `survey_${topic.trim().slice(0, 20).replace(/\s+/g, "_") || "untitled"}`;

  const handleExportMarkdown = () => {
    if (!result) return;
    exportMarkdown(result.content, result.references, filename);
    message.success(t("generate.exportedMarkdown"));
  };

  const handleExportWord = () => {
    if (!result) return;
    const html = markdownRef.current?.innerHTML || "";
    exportWord(html, result.references, filename);
    message.success(t("generate.exportedWord"));
  };

  const handleExportLatex = () => {
    if (!result) return;
    exportLatex(result.content, result.references, filename);
    message.success(t("generate.exportedLatex"));
  };

  const handleExportPdf = () => {
    if (!result) return;
    const html = markdownRef.current?.innerHTML || "";
    exportPdf(html, result.references, filename);
  };

  return (
    <div style={{ maxWidth: 900, margin: "0 auto" }}>
      <PageHeader title={t("generate.title")} description={t("generate.subtitle")} />

      {/* WP-2.3: 跨文档对比表入口 */}
      <Card
        className="pf-glass-card"
        variant="borderless"
        style={{ marginBottom: 20, padding: 12 }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <Typography.Text strong style={{ fontSize: 16 }}>
              <Table style={{ marginRight: 8, color: "var(--pf-primary)" }} />
              {t("compare.title")}
            </Typography.Text>
            <div style={{ color: "var(--pf-text-muted)", fontSize: 13, marginTop: 4 }}>
              {t("compare.subtitle")}
            </div>
          </div>
          <Link to="/compare">
            <Button type="primary" icon={<Table />}>
              {t("compare.goToCompare")}
            </Button>
          </Link>
        </div>
      </Card>

      {/* 未配置模型时的内联提示 */}
      {!hasModel && !loading && !result && (
        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ marginBottom: 20, padding: 16 }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <Zap style={{ color: "var(--pf-text-placeholder)", fontSize: 20 }} />
            <div style={{ flex: 1, minWidth: 240 }}>
              <div
                className="pf-serif"
                style={{ fontSize: 15, fontWeight: 600, color: "var(--pf-text-primary)" }}
              >
                {t("ask.noModelTitle")}
              </div>
              <div style={{ fontSize: 13, color: "var(--pf-text-muted)", marginTop: 2 }}>
                {t("ask.noModelDesc")}
              </div>
            </div>
            <Button
              type="primary"
              icon={<Zap />}
              onClick={() => navigate("/models")}
            >
              {t("ask.goToModels")}
            </Button>
          </div>
        </Card>
      )}

      {/* 输入区 */}
      <Card className="pf-glass-card" variant="borderless" style={{ marginBottom: 20, padding: 8 }}>
        <TextArea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder={t("generate.placeholder")}
          rows={3}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              void handleGenerate();
            }
          }}
          style={{ borderRadius: 8, resize: "none" }}
        />
        <div
          style={{
            marginTop: 12,
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }}>
            {t("generate.hint")}
          </span>
          <Button type="primary" icon={<Pencil />} onClick={handleGenerate} loading={loading}>
            {t("generate.submit")}
          </Button>
        </div>
      </Card>

      {/* 加载中 */}
      {loading && (
        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ textAlign: "center", padding: 48 }}
        >
          <Spin size="large" />
          <div style={{ marginTop: 16, color: "var(--pf-text-muted)", fontSize: 14 }}>
            {t("generate.loading")}
          </div>
        </Card>
      )}

      {/* 结果区 */}
      {!loading && result && (
        <>
          <Card
            className="pf-glass-card"
            variant="borderless"
            style={{ marginBottom: 16, padding: 8 }}
            title={
              <Space>
                <FileText style={{ color: "var(--pf-primary)" }} />
                <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
                  {t("generate.bodyTitle")}
                </span>
              </Space>
            }
          >
            <div className="pf-markdown" ref={markdownRef}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.content}</ReactMarkdown>
            </div>
          </Card>

          {/* 导出按钮区 */}
          <Space wrap style={{ marginBottom: 20 }}>
            <Button icon={<Download />} onClick={handleExportMarkdown}>
              {t("generate.exportMarkdown")}
            </Button>
            <Button icon={<Download />} onClick={handleExportWord}>
              {t("generate.exportWord")}
            </Button>
            <Button icon={<Download />} onClick={handleExportLatex}>
              {t("generate.exportLatex")}
            </Button>
            <Button icon={<Download />} onClick={handleExportPdf}>
              {t("generate.exportPdf")}
            </Button>
          </Space>

          {result.references && result.references.length > 0 ? (
            <div>
              <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
                <FileText style={{ color: "var(--pf-primary)" }} />
                <span
                  className="pf-serif"
                  style={{ fontSize: 16, fontWeight: 600, color: "var(--pf-text-primary)" }}
                >
                  {t("generate.references")}
                </span>
                <Tag color="blue" style={{ marginInlineStart: 0 }}>
                  {result.references.length}
                </Tag>
              </div>
              {result.references.map((refItem, idx) => (
                <ReferenceCard
                  key={refItem.id || idx}
                  refItem={refItem}
                  index={idx}
                  bracketed
                  onClick={() => navigate(`/paper/${refItem.id}`)}
                />
              ))}
            </div>
          ) : (
            <Empty description={t("generate.emptyReferences")} />
          )}
        </>
      )}

      {/* 空状态 */}
      {!loading && !result && (
        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ textAlign: "center", padding: 48 }}
        >
          <Pencil
            style={{ fontSize: 40, color: "var(--pf-text-placeholder)", marginBottom: 16 }}
          />
          <div
            className="pf-serif"
            style={{ fontSize: 16, color: "var(--pf-text-muted)", marginBottom: 6 }}
          >
            {t("generate.emptyTitle")}
          </div>
          <div style={{ fontSize: 13, color: "var(--pf-text-placeholder)" }}>
            {t("generate.emptyDesc")}
          </div>
        </Card>
      )}
    </div>
  );
}
