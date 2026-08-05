import { Plus, Pencil, Trash2, FileText, Download, FileType, TrendingUp, FolderKanban, BarChart3, FlaskConical } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Skeleton,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
  message,
} from "antd";

import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  fetchWritingProjects,
  createWritingProject,
  deleteWritingProject,
  exportWritingProject,
  fetchWritingStats,
  fetchWordCount,
  generateOutline,
} from "@/api/writing";
import type { OutlineNode, WritingProject, WritingStats, WordCountResult } from "@/api/types";
import { exportMarkdown } from "@/utils/export";
import ProjectForm from "@/components/writing/ProjectForm";
import OutlinePreview from "@/components/writing/OutlinePreview";
import PageHeader from "@/components/PageHeader";

/** 轻量 SVG 折线图（避免引入额外图表库） */
function TrendChart({ data }: { data: { date: string; wordCount: number }[] }) {
  if (data.length === 0) return null;
  const width = 520;
  const height = 140;
  const padding = 30;
  const max = Math.max(...data.map((d) => d.wordCount), 1);
  const stepX = (width - padding * 2) / Math.max(data.length - 1, 1);
  const points = data.map((d, i) => {
    const x = padding + i * stepX;
    const y = height - padding - (d.wordCount / max) * (height - padding * 2);
    return { x, y, ...d };
  });
  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ");
  const areaD = `${pathD} L ${points[points.length - 1].x} ${height - padding} L ${points[0].x} ${height - padding} Z`;
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <defs>
        <linearGradient id="trendGradient" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(30,64,175,0.2)" />
          <stop offset="100%" stopColor="rgba(30,64,175,0.02)" />
        </linearGradient>
      </defs>
      <path d={areaD} fill="url(#trendGradient)" />
      <path d={pathD} fill="none" stroke="var(--pf-primary)" strokeWidth={2} />
      {points.map((p) => (
        <g key={p.date}>
          <circle cx={p.x} cy={p.y} r={3} fill="var(--pf-primary)" />
          <text
            x={p.x}
            y={height - padding + 14}
            textAnchor="middle"
            fontSize={10}
            fill="var(--pf-text-placeholder)"
          >
            {p.date.slice(5)}
          </text>
        </g>
      ))}
    </svg>
  );
}

export default function WritingDashboard() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [projects, setProjects] = useState<WritingProject[]>([]);
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<WritingStats | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  // A4: 各项目章节字数（按 projectId 索引）
  const [wordCounts, setWordCounts] = useState<Record<number, WordCountResult>>({});
  // P1: 自动生成大纲 Modal 状态
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [outlineTopic, setOutlineTopic] = useState("");
  const [outlineKeywords, setOutlineKeywords] = useState<string[]>([]);
  const [outlineProjectId, setOutlineProjectId] = useState<number | null>(null);
  // 生成流程状态：idle（填写中）→ generating（生成中）→ preview（预览）| error
  const [outlineStage, setOutlineStage] = useState<"idle" | "generating" | "preview" | "error">(
    "idle",
  );
  const [outlineResult, setOutlineResult] = useState<OutlineNode[]>([]);
  const [outlineChunk, setOutlineChunk] = useState("");
  const [outlineError, setOutlineError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [data, s] = await Promise.all([
        fetchWritingProjects(),
        fetchWritingStats().catch(() => null),
      ]);
      setProjects(data);
      if (s) setStats(s);
      // A4: 并行拉取每个项目的字数（容错，单个失败不影响其他）
      const entries = await Promise.all(
        data.map(async (p) => {
          try {
            const wc = await fetchWordCount(p.id);
            return [p.id, wc] as const;
          } catch {
            return [p.id, null] as const;
          }
        }),
      );
      const map: Record<number, WordCountResult> = {};
      for (const [id, wc] of entries) {
        if (wc) map[id] = wc;
      }
      setWordCounts(map);
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openCreate = () => {
    form.resetFields();
    form.setFieldsValue({ keywords: [], targetJournal: "" });
    setModalOpen(true);
  };

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);
      const templateId = values.templateId;
      const project = await createWritingProject({
        title: values.title,
        keywords: values.keywords || [],
        targetJournal: values.targetJournal || "",
        templateId: templateId || undefined,
      });
      message.success(
        templateId ? t("write.projectCreatedWithTemplate") : t("write.projectCreatedDefault"),
      );
      setModalOpen(false);
      // 创建成功后跳转到编辑器
      navigate(`/write/${project.id}`);
    } catch {
      // 校验或 API 错误
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteWritingProject(id);
      message.success(t("write.projectDeleted"));
      await load();
    } catch {
      // ignored
    }
  };

  const handleExport = async (project: WritingProject) => {
    try {
      const result = await exportWritingProject(project.id);
      exportMarkdown(result.content, result.references, result.filename.replace(/\.md$/, ""));
      message.success(t("write.exportedMarkdown"));
    } catch {
      // ignored
    }
  };

  // ===== P1: 自动生成大纲 =====

  /** 打开「自动生成大纲」Modal，并预选第一个项目 */
  const openOutlineModal = () => {
    setOutlineTopic("");
    setOutlineKeywords([]);
    setOutlineProjectId(projects.length > 0 ? projects[0].id : null);
    setOutlineStage("idle");
    setOutlineResult([]);
    setOutlineChunk("");
    setOutlineError("");
    setOutlineOpen(true);
  };

  /** 关闭 Modal 并重置全部状态 */
  const closeOutlineModal = () => {
    setOutlineOpen(false);
    setOutlineStage("idle");
    setOutlineResult([]);
    setOutlineChunk("");
    setOutlineError("");
    setOutlineTopic("");
    setOutlineKeywords([]);
  };

  /** 触发 SSE 生成大纲 */
  const handleGenerateOutline = async () => {
    const topic = outlineTopic.trim();
    if (!topic) {
      message.warning(t("write.outlineTopicRequired"));
      return;
    }
    if (outlineProjectId == null) {
      message.warning(t("write.outlineProjectRequired"));
      return;
    }
    setOutlineStage("generating");
    setOutlineChunk("");
    setOutlineError("");
    setOutlineResult([]);
    await generateOutline(
      { projectId: outlineProjectId, topic, keywords: outlineKeywords },
      (chunk) => {
        // 累积展示已生成的片段
        setOutlineChunk((prev) => (prev + chunk).slice(-400));
      },
      (outline) => {
        setOutlineResult(outline);
        setOutlineStage("preview");
      },
      (errMsg) => {
        setOutlineError(errMsg);
        setOutlineStage("error");
      },
    );
  };

  /** 导入成功后关闭 Modal 并跳转到编辑器 */
  const handleOutlineImported = () => {
    const pid = outlineProjectId;
    closeOutlineModal();
    message.success(t("write.outlineImported"));
    if (pid != null) navigate(`/write/${pid}`);
  };

  /** 重新生成：回到填写阶段，保留主题/关键词/项目便于微调 */
  const handleOutlineRegenerate = () => {
    setOutlineStage("idle");
    setOutlineResult([]);
    setOutlineChunk("");
    setOutlineError("");
  };

  return (
    <div className="pf-writing-workspace" style={{ maxWidth: 1000, margin: "0 auto" }}>
      <div
        className="pf-writing-header"
        style={{
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "space-between",
          marginBottom: 20,
        }}
      >
        <PageHeader title={t("write.title")} description={t("write.subtitle")} />
        <Space>
          <Button
            icon={<FlaskConical />}
            onClick={openOutlineModal}
            disabled={projects.length === 0}
            title={
              projects.length === 0
                ? t("write.outlineFirstCreateProject")
                : t("write.outlineAutoDesc")
            }
          >
            {t("write.autoGenerateOutline")}
          </Button>
          <Button type="primary" icon={<Plus />} onClick={openCreate}>
            {t("write.newProject")}
          </Button>
        </Space>
      </div>

      {/* 统计看板 */}
      {stats && (
        <div style={{ display: "flex", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
          <Card
            className="pf-glass-card pf-writing-stat pf-writing-stat--projects"
            variant="borderless"
            style={{ flex: "1 1 140px", minWidth: 140 }}
          >
            <Statistic
              title={t("write.statTotalProjects")}
              value={stats.totalProjects}
              prefix={<FolderKanban style={{ color: "var(--pf-primary)" }} />}
            />
          </Card>
          <Card
            className="pf-glass-card pf-writing-stat pf-writing-stat--words"
            variant="borderless"
            style={{ flex: "1 1 140px", minWidth: 140 }}
          >
            <Statistic
              title={t("write.statTotalWords")}
              value={stats.totalWords}
              suffix={t("write.wordUnit")}
              prefix={<FileType style={{ color: "var(--pf-success)" }} />}
              valueStyle={{ fontSize: 20 }}
            />
          </Card>
          <Card
            className="pf-glass-card pf-writing-stat pf-writing-stat--today"
            variant="borderless"
            style={{ flex: "1 1 140px", minWidth: 140 }}
          >
            <Statistic
              title={t("write.statTodayWords")}
              value={stats.todayWords}
              suffix={t("write.wordUnit")}
              prefix={<TrendingUp style={{ color: "#d97706" }} />}
              valueStyle={{
                color: stats.todayWords > 0 ? "var(--pf-success)" : "var(--pf-text-placeholder)",
              }}
            />
          </Card>
          <Card
            className="pf-glass-card"
            variant="borderless"
            style={{ flex: "3 1 300px", minWidth: 300, overflow: "hidden" }}
            title={
              <Typography.Text className="pf-serif" style={{ fontSize: 14 }}>
                {t("write.trend7Days")}
              </Typography.Text>
            }
            styles={{ body: { padding: "8px 16px 0" } }}
          >
            <TrendChart data={stats.trend} />
          </Card>
        </div>
      )}

      {loading ? (
        <Card className="pf-glass-card" variant="borderless">
          <Skeleton active paragraph={{ rows: 3 }} />
        </Card>
      ) : projects.length === 0 ? (
        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ textAlign: "center", padding: 48 }}
        >
          <FileText
            style={{ fontSize: 40, color: "var(--pf-text-placeholder)", marginBottom: 16 }}
          />
          <div
            className="pf-serif"
            style={{ fontSize: 16, color: "var(--pf-text-muted)", marginBottom: 6 }}
          >
            {t("write.emptyTitle")}
          </div>
          <div style={{ fontSize: 13, color: "var(--pf-text-placeholder)", marginBottom: 16 }}>
            {t("write.emptyDesc")}
          </div>
          <Button type="primary" icon={<Plus />} onClick={openCreate}>
            {t("write.newProject")}
          </Button>
        </Card>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {projects.map((p) => (
            <Card
              key={p.id}
              className="pf-glass-card pf-card-hover pf-writing-project-card"
              variant="borderless"
              style={{ cursor: "pointer" }}
              onClick={() => navigate(`/write/${p.id}`)}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: 12,
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <Space size={8} align="center">
                    <FileText style={{ color: "var(--pf-primary)" }} />
                    <span
                      className="pf-serif"
                      style={{ fontSize: 17, fontWeight: 600, color: "var(--pf-text-primary)" }}
                    >
                      {p.title}
                    </span>
                    <Tag color="blue" style={{ marginInlineStart: 0 }}>
                      {p.chapterCount} {t("write.chapterUnit")}
                    </Tag>
                  </Space>
                  <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {p.keywords.length > 0 ? (
                      p.keywords.map((k) => (
                        <Tag key={k} style={{ margin: 0 }}>
                          {k}
                        </Tag>
                      ))
                    ) : (
                      <span style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }}>
                        {t("write.noKeywords")}
                      </span>
                    )}
                  </div>
                  <div style={{ marginTop: 8, fontSize: 12, color: "var(--pf-text-placeholder)" }}>
                    {p.targetJournal && (
                      <span>
                        {t("write.targetJournal")}：{p.targetJournal} ·{" "}
                      </span>
                    )}
                    {t("write.updatedAt", { date: p.updatedAt || p.createdAt })}
                  </div>
                </div>
                <Space size={4} onClick={(e) => e.stopPropagation()}>
                  <Button
                    size="small"
                    type="text"
                    icon={<Download />}
                    onClick={() => handleExport(p)}
                    title={t("write.exportMarkdownTitle")}
                  />
                  <Button
                    size="small"
                    type="text"
                    icon={<Pencil />}
                    onClick={() => navigate(`/write/${p.id}`)}
                    title={t("write.edit")}
                  />
                  <Popconfirm
                    title={t("write.deleteProjectTitle")}
                    description={t("write.deleteProjectContent")}
                    onConfirm={() => handleDelete(p.id)}
                    okText={t("common.delete")}
                    okType="danger"
                    cancelText={t("common.cancel")}
                  >
                    <Button
                      size="small"
                      type="text"
                      danger
                      icon={<Trash2 />}
                      title={t("common.delete")}
                    />
                  </Popconfirm>
                </Space>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* A4: 章节字数分布看板 */}
      {!loading && projects.length > 0 && (
        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ marginTop: 20 }}
          title={
            <Space>
              <BarChart3 style={{ color: "var(--pf-primary)" }} />
              <Typography.Text className="pf-serif" style={{ fontSize: 15, fontWeight: 600 }}>
                {t("write.wordCountDistribution")}
              </Typography.Text>
            </Space>
          }
        >
          {Object.keys(wordCounts).length === 0 ? (
            <Typography.Text type="secondary" style={{ fontSize: 13 }}>
              {t("write.noWordCountData")}
            </Typography.Text>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
              {projects.map((p) => {
                const wc = wordCounts[p.id];
                if (!wc || wc.chapters.length === 0) return null;
                const maxWords = Math.max(...wc.chapters.map((c) => c.wordCount), 1);
                return (
                  <div key={p.id}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        marginBottom: 8,
                      }}
                    >
                      <Typography.Link
                        className="pf-serif"
                        style={{ fontSize: 14, fontWeight: 600 }}
                        onClick={() => navigate(`/write/${p.id}`)}
                      >
                        {p.title}
                      </Typography.Link>
                      <Tag color="blue" style={{ margin: 0 }}>
                        {t("write.wordCountTotal", {
                          total: wc.total,
                          chapters: wc.chapters.length,
                        })}
                      </Tag>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {wc.chapters.map((c) => {
                        const percent = Math.round((c.wordCount / maxWords) * 100);
                        return (
                          <div
                            key={c.id}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 12,
                              fontSize: 13,
                            }}
                          >
                            <Typography.Link
                              style={{
                                flex: "0 0 160px",
                                minWidth: 0,
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                whiteSpace: "nowrap",
                              }}
                              onClick={() => navigate(`/write/${p.id}`)}
                              title={c.title}
                            >
                              {c.title}
                            </Typography.Link>
                            <Progress
                              percent={percent}
                              size="small"
                              strokeColor={
                                c.wordCount === 0
                                  ? "var(--pf-text-placeholder)"
                                  : c.wordCount === maxWords
                                    ? "var(--pf-primary)"
                                    : "var(--pf-primary-hover)"
                              }
                              style={{ flex: 1, minWidth: 0 }}
                              format={() => t("write.wordCountUnit", { count: c.wordCount })}
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      )}

      <ProjectForm
        form={form}
        open={modalOpen}
        submitting={submitting}
        onOk={handleCreate}
        onCancel={() => setModalOpen(false)}
      />

      {/* P1: 自动生成大纲 Modal */}
      <Modal
        title={
          <Space>
            <FlaskConical style={{ color: "var(--pf-primary)" }} />
            <span className="pf-serif">{t("write.autoGenerateOutline")}</span>
          </Space>
        }
        open={outlineOpen}
        onCancel={closeOutlineModal}
        width={640}
        destroyOnHidden
        footer={
          outlineStage === "preview" ? null : (
            <Space>
              {outlineStage === "error" && (
                <Button onClick={handleOutlineRegenerate}>{t("write.outlineRegenerate")}</Button>
              )}
              <Button onClick={closeOutlineModal}>
                {outlineStage === "idle" ? t("write.outlineCancel") : t("write.outlineClose")}
              </Button>
              {outlineStage === "idle" && (
                <Button
                  type="primary"
                  onClick={handleGenerateOutline}
                  disabled={projects.length === 0}
                >
                  {t("write.outlineStart")}
                </Button>
              )}
            </Space>
          )
        }
      >
        {outlineStage === "idle" && (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <div style={{ marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
                {t("write.outlineTopic")} <span style={{ color: "#ef4444" }}>*</span>
              </div>
              <Input.TextArea
                value={outlineTopic}
                onChange={(e) => setOutlineTopic(e.target.value)}
                placeholder={t("write.outlineTopicPlaceholder")}
                autoSize={{ minRows: 2, maxRows: 4 }}
                maxLength={200}
                showCount
              />
            </div>
            <div>
              <div style={{ marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
                {t("write.outlineKeywords")}
              </div>
              <Select
                mode="tags"
                style={{ width: "100%" }}
                placeholder={t("write.outlineKeywordsPlaceholder")}
                value={outlineKeywords}
                onChange={setOutlineKeywords}
                tokenSeparators={[",", "，", " "]}
              />
            </div>
            <div>
              <div style={{ marginBottom: 6, fontSize: 13, fontWeight: 500 }}>
                {t("write.outlineImportTo")} <span style={{ color: "#ef4444" }}>*</span>
              </div>
              <Select
                style={{ width: "100%" }}
                placeholder={t("write.outlineSelectProject")}
                value={outlineProjectId ?? undefined}
                onChange={(v: number) => setOutlineProjectId(v)}
                options={projects.map((p) => ({
                  label: p.title,
                  value: p.id,
                }))}
              />
            </div>
            {projects.length === 0 && (
              <Typography.Text type="warning" style={{ fontSize: 12 }}>
                {t("write.outlineNoProject")}
              </Typography.Text>
            )}
          </div>
        )}

        {outlineStage === "generating" && (
          <div style={{ textAlign: "center", padding: "32px 0" }}>
            <Spin tip={t("write.outlineGenerating")} size="large">
              <div style={{ padding: 24 }} />
            </Spin>
            {outlineChunk && (
              <div
                style={{
                  marginTop: 16,
                  padding: 12,
                  background: "rgba(30,64,175,0.04)",
                  borderRadius: 6,
                  fontSize: 12,
                  color: "var(--pf-text-muted)",
                  textAlign: "left",
                  maxHeight: 120,
                  overflow: "auto",
                  fontFamily: "monospace",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-all",
                }}
              >
                {outlineChunk}
              </div>
            )}
          </div>
        )}

        {outlineStage === "preview" && outlineProjectId != null && (
          <OutlinePreview
            outline={outlineResult}
            projectId={outlineProjectId}
            onImported={handleOutlineImported}
            onRegenerate={handleOutlineRegenerate}
          />
        )}

        {outlineStage === "error" && (
          <div style={{ padding: "16px 0" }}>
            <Typography.Text type="danger" style={{ fontSize: 14 }}>
              {t("write.outlineGenerateFailed", {
                error: outlineError || t("common.unknownError"),
              })}
            </Typography.Text>
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--pf-text-placeholder)" }}>
              {t("write.outlineRetryHint")}
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}
