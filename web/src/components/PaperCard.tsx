import { FileText, Link, Database, Trophy, UploadCloud, Cloud, FolderOpen, Globe, BookMarked, GraduationCap, MoreHorizontal, BookOpen, Radar } from "lucide-react";
import { Card, Tag, Tooltip, Space, Checkbox, Button, Dropdown } from "antd";

import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { Paper } from "@/api/types";
import { arxivUrl, formatAuthors, formatSize } from "@/utils/format";
import { SOURCE_COLOR, CATEGORY_COLOR } from "@/utils/constants";
import { useMetadataActions } from "@/hooks/useMetadataActions";
import FavoriteButton from "./FavoriteButton";
import OcrBadge from "./OcrBadge";

// 来源标签配置：颜色、图标、显示文本
const SOURCE_LABELS: Record<string, { color: string; icon: ReactNode; text: string }> = {
  upload: { color: "#52c41a", icon: <UploadCloud />, text: "本地上传" },
  arxiv: { color: "#1677ff", icon: <Globe />, text: "arXiv" },
  cnki: { color: "#fa541c", icon: <BookMarked />, text: "知网" },
  google_scholar: { color: "#722ed1", icon: <GraduationCap />, text: "Scholar" },
  web_clipper: { color: "#0891b2", icon: <Globe />, text: "网页剪藏" },
  zotero: { color: "#722ed1", icon: <FolderOpen />, text: "Zotero" },
  pubmed: { color: "#13c2c2", icon: <Cloud />, text: "PubMed" },
  ieee: { color: "#fa8c16", icon: <Cloud />, text: "IEEE" },
};

interface Props {
  paper: Paper;
  selected?: boolean;
  onToggleSelect?: () => void;
  onEnrich?: () => void;
  depthScore?: { verdict: string | null; noveltyScore: number | null };
}

export default function PaperCard({ paper, selected, onToggleSelect, onEnrich, depthScore }: Props) {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { items: metadataItems } = useMetadataActions({
    paper,
    onEnrichOpen: onEnrich,
  });

  const isReport = paper.category === "report";

  return (
    <Card
      className={`pf-glass-card pf-paper-card pf-lift${isReport ? " pf-report-card" : ""}`}
      variant="borderless"
      style={{
        height: "100%",
        ...(selected
          ? {
              border: "2px solid var(--pf-primary)",
              background: "var(--pf-primary-soft)",
              boxShadow: "0 0 0 1px var(--pf-primary)",
            }
          : {}),
      }}
      title={
        <div className="pf-paper-card-title-row">
          {onToggleSelect && (
            <Checkbox
              checked={selected}
              onChange={(e) => {
                e.stopPropagation();
                onToggleSelect();
              }}
              onClick={(e) => e.stopPropagation()}
              style={{ marginTop: 2 }}
            />
          )}
          <button
            type="button"
            className="pf-paper-title-link pf-serif"
            onClick={() => navigate(`/paper/${paper.id}`)}
          >
            {isReport && (
              <span role="img" aria-label={t("paper.report", "感悟报告")}>
                <BookOpen style={{ color: "var(--pf-report-accent)", marginRight: 6 }} />
              </span>
            )}
            {paper.title}
          </button>
        </div>
      }
      extra={
        onToggleSelect ? undefined : (
          <FavoriteButton paper={paper} variant="text" size="small" stopPropagation />
        )
      }
    >
      <div className="pf-paper-card-meta">
        <span className="pf-paper-authors">{formatAuthors(paper.authors)}</span>
        <span className="pf-paper-meta-separator" aria-hidden="true" />
        <span>{paper.year || t("paper.notAvailable", "暂无")}</span>
        <span className="pf-paper-meta-separator" aria-hidden="true" />
        {paper.category === "report" ? (
          <Tag
            className="pf-report-tag"
            style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: "18px", borderRadius: 10 }}
            variant="filled"            >
              {t("paper.report", "感悟报告")}
            </Tag>
        ) : (
          <>
            <OcrBadge ocrStatus={paper.ocrStatus} isScanned={paper.isScanned} />
          </>
        )}

        {paper.category !== "report" &&
          (() => {
            const sl = SOURCE_LABELS[paper.source];
            return sl ? (
              <Tag
                color={sl.color}
                style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: "18px", borderRadius: 10 }}
                variant="filled"
              >
                {sl.icon}
                <span style={{ marginLeft: 4 }}>{sl.text}</span>
              </Tag>
            ) : (
              <Tag color={SOURCE_COLOR[paper.source]} style={{ marginInlineEnd: 0 }}>
                {paper.source.toUpperCase()}
              </Tag>
            );
          })()}
        <span
          style={{
            display: "inline-block",
            width: 6,
            height: 6,
            borderRadius: "50%",
            background: CATEGORY_COLOR[paper.category] || "var(--pf-text-placeholder)",
            marginLeft: 8,
            verticalAlign: "middle",
          }}
        />
      </div>

      <div
        className="pf-paper-card-abstract pf-abstract"
        style={{
          display: "-webkit-box",
          WebkitLineClamp: 3,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}
      >
        {paper.abstract}
      </div>

      {/* 期刊与引用信息行 */}
      {(paper.journal || paper.citations > 0) && (
        <div style={{ marginTop: 8, display: "flex", gap: 12, alignItems: "center" }}>
          {paper.journal && (
            <Tooltip title={t("paper.journal")}>
              <Tag
                color="purple"
                style={{ fontSize: 11, margin: 0, maxWidth: 200 }}
                variant="filled"
              >
                <Trophy style={{ marginRight: 4 }} />
                {paper.journal.length > 25 ? paper.journal.slice(0, 25) + "…" : paper.journal}
              </Tag>
            </Tooltip>
          )}
          {paper.citations > 0 && (
            <Tooltip title={t("paper.citationCount")}>
              <span className="pf-paper-citation-metric">
                <FileText size={13} aria-hidden="true" />
                {t("paper.citationsShort", { count: paper.citations })}
              </span>
            </Tooltip>
          )}
        </div>
      )}

      <div
        className="pf-paper-card-footer"
        style={{
          marginTop: 14,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <Space size={6} wrap>
          {/* B2: Semantic Scholar 研究领域标签（蓝色，区别于普通标签） */}
          {paper.fieldsOfStudy?.slice(0, 2).map((f) => (
            <Tag
              key={`fos-${f}`}
              variant="filled"
              color="blue"
              style={{ fontSize: 12, lineHeight: 1.6 }}
            >
              {f}
            </Tag>
          ))}
          {paper.tags.slice(0, 3).map((t) => (
            <Tag key={t} bordered={false} color="default" style={{ fontSize: 12, lineHeight: 1.6 }}>
              {t}
            </Tag>
          ))}
        </Space>
        <Space
          size={16}
          style={{ fontSize: 13, lineHeight: 1.8, color: "var(--pf-text-placeholder)" }}
        >
          {depthScore && depthScore.verdict && (
            <Tooltip title={t("paper.depthSummary", {
                verdict: depthScore.verdict,
                novelty: depthScore.noveltyScore != null ? (depthScore.noveltyScore * 100).toFixed(0) : t("paper.notAvailable", "暂无"),
              })}>
              <span
                style={{
                  fontSize: 12,
                  fontWeight: 600,
                  color: depthScore.verdict === 'accept' ? '#52c41a'
                    : depthScore.verdict === 'minor_revision' ? '#1677ff'
                    : depthScore.verdict === 'major_revision' ? '#fa8c16'
                    : '#ff4d4f',
                  cursor: 'pointer',
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  navigate(`/paper/${paper.id}?tab=depthReview`);
                }}
              >
                <Radar size={12} style={{ marginRight: 2, verticalAlign: "middle" }} />
                {depthScore.verdict === 'accept' ? '接收'
                  : depthScore.verdict === 'minor_revision' ? '小修'
                  : depthScore.verdict === 'major_revision' ? '大修'
                  : depthScore.verdict === 'reject' ? '拒稿'
                  : depthScore.verdict}
                {depthScore.noveltyScore != null && (
                  <span style={{ marginLeft: 4, color: "var(--pf-text-placeholder)" }}>
                    {(depthScore.noveltyScore * 100).toFixed(0)}
                  </span>
                )}
              </span>
            </Tooltip>
          )}
          <span className="pf-paper-footer-metric">
            <Database aria-hidden="true" /> {t("paper.chunksShort", { count: paper.chunkCount })}
          </span>
          <span className="pf-paper-footer-metric">
            <FileText aria-hidden="true" /> {formatSize(paper.indexSize)}
          </span>
          {onEnrich && (
            <Dropdown
              menu={{
                items: metadataItems.map((item) => ({
                  key: item.key,
                  icon: <item.icon />,
                  label: item.label,
                  onClick: (e: { domEvent: React.MouseEvent | React.KeyboardEvent }) => {
                    e.domEvent.stopPropagation();
                    item.onClick();
                  },
                })),
              }}
              trigger={["click"]}
            >
              <Tooltip title={t("paper.metadataActions", "元数据操作")}>
                <Button
                  type="text"
                  size="small"
                  icon={<MoreHorizontal />}
                  aria-label={t("paper.metadataActions", "元数据操作")}
                  onClick={(e: React.MouseEvent<HTMLButtonElement>) => e.stopPropagation()}
                  style={{ color: "#722ed1", padding: 0 }}
                />
              </Tooltip>
            </Dropdown>
          )}
          {paper.category === "report" && paper.sourcePaperId ? (
            <Tooltip title={t("paper.sourcePaper", "查看被点评的原论文")}>
              <a
                href={`/paper/${paper.sourcePaperId}`}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  navigate(`/paper/${paper.sourcePaperId}`);
                }}
                style={{ fontSize: 13 }}
              >
                <Link /> {t("paper.sourcePaperLink", "源论文")}
              </a>
            </Tooltip>
          ) : (
            <Tooltip title={t("paper.arxivOriginal")}>
              <a
                href={paper.pdfUrl || arxivUrl(paper.id)}
                target="_blank"
                rel="noreferrer"
                style={{ fontSize: 13 }}
              >
                <Link /> PDF
              </a>
            </Tooltip>
          )}
        </Space>
      </div>
    </Card>
  );
}
