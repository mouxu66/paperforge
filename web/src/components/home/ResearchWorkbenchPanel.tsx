import {
  ArrowUpRight,
  BookOpen,
  BrainCircuit,
  FilePlus2,
  FileText,
  FlaskConical,
  Library,
  MessageSquareText,
  Network,
  Sparkles,
} from "lucide-react";
import { Button, Card, Progress, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import type { LibraryStats, Paper } from "@/api/types";

interface Props {
  stats: LibraryStats | null;
  papers: Paper[];
  isReport?: boolean;
  onUpload: () => void;
}

const SOURCE_LABELS: Record<string, string> = {
  arxiv: "arXiv",
  upload: "Upload",
  pubmed: "PubMed",
  ieee: "IEEE",
  cnki: "CNKI",
  google_scholar: "Google Scholar",
  web_clipper: "Web Clipper",
  zotero: "Zotero",
};

function compactNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(
    Math.max(0, value),
  );
}

function shortDate(year: number): string {
  return year > 0 ? String(year) : "N/A";
}

export default function ResearchWorkbenchPanel({ stats, papers, isReport, onUpload }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const allCategories = (stats?.byCategory ?? [])
    .filter((item) => item.count > 0 && item.category !== "report")
    .sort((a, b) => b.count - a.count);
  const categories = allCategories.slice(0, 4);
  const sources = (stats?.bySource ?? [])
    .filter((item) => item.count > 0)
    .sort((a, b) => b.count - a.count)
    .slice(0, 4);
  const categoryTotal = allCategories.reduce((sum, item) => sum + item.count, 0);
  const sourceTotal = sources.reduce((sum, item) => sum + item.count, 0);
  const recentPapers = [...papers]
    .filter((paper) => paper.category !== "report")
    .sort((a, b) => (b.year || 0) - (a.year || 0) || b.citations - a.citations)
    .slice(0, 3);
  const topPaper = [...papers]
    .filter((paper) => paper.category !== "report")
    .sort((a, b) => b.citations - a.citations)[0];
  const hasLibrary = (stats?.totalPapers ?? 0) > 0;

  const quickActions = [
    {
      key: "ask",
      icon: <MessageSquareText size={17} />,
      title: t("home.workbench.askTitle"),
      description: t("home.workbench.askDesc"),
      onClick: () => navigate("/ask"),
      tone: "blue",
    },
    {
      key: "generate",
      icon: <Sparkles size={17} />,
      title: t("home.workbench.generateTitle"),
      description: t("home.workbench.generateDesc"),
      onClick: () => navigate("/generate"),
      tone: "violet",
    },
    {
      key: "depth",
      icon: <FlaskConical size={17} />,
      title: t("home.workbench.depthTitle"),
      description: t("home.workbench.depthDesc"),
      onClick: () => navigate("/depth"),
      tone: "amber",
    },
    {
      key: "upload",
      icon: <FilePlus2 size={17} />,
      title: t("home.workbench.uploadTitle"),
      description: t("home.workbench.uploadDesc"),
      onClick: onUpload,
      tone: "green",
    },
  ];

  return (
    <section className="pf-workbench-panel" aria-labelledby="research-workbench-title">
      <div className="pf-workbench-heading">
        <div>
          <div className="pf-section-eyebrow">
            <Network size={13} />
            {t("home.workbench.eyebrow")}
          </div>
          <Typography.Title id="research-workbench-title" level={4} className="pf-workbench-title">
            {isReport ? t("home.workbench.reportTitle") : t("home.workbench.title")}
          </Typography.Title>
          <Typography.Paragraph className="pf-workbench-subtitle">
            {isReport ? t("home.workbench.reportSubtitle") : t("home.workbench.subtitle")}
          </Typography.Paragraph>
        </div>
        <Tag className="pf-workbench-index-tag" icon={<Library size={13} />}>
          {hasLibrary
            ? t("home.workbench.indexed", { count: stats?.totalPapers ?? 0 })
            : t("home.workbench.emptyIndex")}
        </Tag>
      </div>

      <div className="pf-workbench-grid">
        <Card className="pf-workbench-card pf-workbench-card--actions" variant="borderless">
          <div className="pf-workbench-card-label">
            <BrainCircuit size={15} />
            {t("home.workbench.actionLabel")}
          </div>
          <div className="pf-workbench-actions">
            {quickActions.map((action) => (
              <button
                className={`pf-workbench-action pf-workbench-action--${action.tone}`}
                key={action.key}
                type="button"
                onClick={action.onClick}
              >
                <span className="pf-workbench-action-icon" aria-hidden="true">
                  {action.icon}
                </span>
                <span className="pf-workbench-action-copy">
                  <strong>{action.title}</strong>
                  <small>{action.description}</small>
                </span>
                <ArrowUpRight className="pf-workbench-action-arrow" size={15} aria-hidden="true" />
              </button>
            ))}
          </div>
        </Card>

        <Card className="pf-workbench-card pf-workbench-card--structure" variant="borderless">
          <div className="pf-workbench-card-label">
            <BookOpen size={15} />
            {t("home.workbench.structureLabel")}
          </div>
          {categories.length > 0 ? (
            <div className="pf-workbench-bars">
              {categories.map((item, index) => {
                const percent = categoryTotal ? Math.round((item.count / categoryTotal) * 100) : 0;
                return (
                  <div className="pf-workbench-bar-row" key={item.category}>
                    <div className="pf-workbench-bar-meta">
                      <span>{item.category}</span>
                      <strong>{compactNumber(item.count)}</strong>
                    </div>
                    <Progress
                      percent={percent}
                      showInfo={false}
                      strokeColor={index === 0 ? "var(--pf-primary)" : "var(--pf-accent)"}
                      trailColor="var(--pf-border-light)"
                      size="small"
                    />
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="pf-workbench-empty">{t("home.workbench.noStructure")}</div>
          )}
          <div className="pf-workbench-source-row">
            {sources.length > 0 ? (
              sources.map((item) => (
                <Tag key={item.source}>
                  {SOURCE_LABELS[item.source] ?? item.source} ·{" "}
                  {sourceTotal ? Math.round((item.count / sourceTotal) * 100) : 0}%
                </Tag>
              ))
            ) : (
              <Typography.Text type="secondary">{t("home.workbench.noSources")}</Typography.Text>
            )}
          </div>
        </Card>

        <Card className="pf-workbench-card pf-workbench-card--signals" variant="borderless">
          <div className="pf-workbench-card-label pf-workbench-card-label--signals">
            <span className="pf-workbench-card-label-copy">
              <Network size={15} />
              {t("home.workbench.signalLabel")}
            </span>
            {recentPapers.length > 0 && (
              <span className="pf-workbench-signal-count">
                {t("home.workbench.signalCount", { count: recentPapers.length })}
              </span>
            )}
          </div>
          {recentPapers.length > 0 ? (
            <div className="pf-workbench-signals">
              {recentPapers.map((paper, index) => (
                <button
                  type="button"
                  className="pf-workbench-signal"
                  key={paper.id}
                  onClick={() => navigate(`/paper/${encodeURIComponent(paper.id)}`)}
                >
                  <span className="pf-workbench-signal-number" aria-hidden="true">
                    <span className="pf-workbench-signal-icon">
                      <FileText size={12} />
                    </span>
                    <span>0{index + 1}</span>
                  </span>
                  <span className="pf-workbench-signal-copy">
                    <strong title={paper.title}>{paper.title}</strong>
                    <small className="pf-workbench-signal-meta">
                      <span>{shortDate(paper.year)}</span>
                      <span className="pf-workbench-signal-separator" aria-hidden="true" />
                      <span className="pf-workbench-signal-source">
                        {SOURCE_LABELS[paper.source] ?? paper.source}
                      </span>
                      <span className="pf-workbench-signal-separator" aria-hidden="true" />
                      <span>
                        {compactNumber(paper.citations)} {t("home.workbench.citations")}
                      </span>
                    </small>
                  </span>
                  <ArrowUpRight size={14} aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : (
            <div className="pf-workbench-empty">
              <Typography.Text type="secondary">{t("home.workbench.noSignals")}</Typography.Text>
              <Button type="link" size="small" onClick={onUpload}>
                {t("home.workbench.startImport")}
              </Button>
            </div>
          )}
          {topPaper && (
            <div className="pf-workbench-footnote">
              <span className="pf-workbench-footnote-dot" aria-hidden="true" />
              {t("home.workbench.currentViewTopPaper", {
                citations: compactNumber(topPaper.citations),
              })}
            </div>
          )}
        </Card>

      </div>
    </section>
  );
}
