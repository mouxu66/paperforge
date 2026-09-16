import { ArrowLeft, Link as LinkIcon, Zap, ChevronDown } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate, Link, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Typography,
  Card,
  Tag,
  Button,
  Space,
  Skeleton,
  Result,
  Divider,
  Tabs,
  Statistic,
  Empty,
  Dropdown,
  App,
} from "antd";
import PdfAnnotationsTab from "@/components/PdfAnnotationsTab";
import { List } from "@/components/CompatList";

import {
  enrichPaperMetadata,
  fetchPaperById,
  fetchPaperSentiment,
  previewEnrichPaperMetadata,
} from "@/api/papers";
import { fetchCitationRelations } from "@/api/notes";
import type { CitationRelations, CitationSentimentStats, Paper } from "@/api/types";
import { arxivUrl, formatAuthors } from "@/utils/format";
import { SOURCE_COLOR } from "@/utils/constants";
import { useMetadataActions } from "@/hooks/useMetadataActions";
import FavoriteButton from "@/components/FavoriteButton";
import AbstractTab from "@/components/AbstractTab";
import PdfTab from "@/components/PdfTab";
import CiteTab from "@/components/CiteTab";
import NoteList from "@/components/NoteList";
import OcrBadge from "@/components/OcrBadge";
import RelationGraphTab from "@/components/RelationGraphTab";
import SentimentBadge from "@/components/SentimentBadge";
import EnrichDiffModal from "@/components/EnrichDiffModal";
import DepthReviewTab from "@/components/DepthReviewTab";
import FigureDetailsList from "@/components/FigureDetailsList";

const { Title, Text } = Typography;

/**
 * 引用关系面板 —— 被引用次数 + 基于语义相似度推荐的相关论文列表。
 * 进入该 Tab 时挂载并拉取引用关系数据。
 */
function CitationRelationsTab({ paper }: { paper: Paper }) {
  const { t } = useTranslation();
  const [relations, setRelations] = useState<CitationRelations | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchCitationRelations(paper.id)
      .then((r) => {
        if (active) setRelations(r);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [paper.id]);

  if (loading) {
    return <Skeleton active paragraph={{ rows: 4 }} />;
  }

  const references = relations?.references ?? [];
  const citeCount = relations?.citations ?? paper.citations;
  // B2: 有影响力引用数（Semantic Scholar 富化，未富化时不显示）
  const influentialCitations = paper.influentialCitations;

  return (
    <div>
      <div style={{ display: "flex", gap: 32, marginBottom: 8 }}>
        <Statistic title={t("paper.citationCount")} value={citeCount} />
        {influentialCitations != null && (
          <Statistic
            title={t("paper.influentialCitations")}
            value={influentialCitations}
            styles={{ content: { color: "var(--pf-primary)" } }}
          />
        )}
      </div>
      <Divider />
      <Title level={5} className="pf-serif" style={{ marginBottom: 4 }}>
        {t("paper.relatedPapers")}
      </Title>
      <Text type="secondary" style={{ fontSize: 12, display: "block", marginBottom: 12 }}>
        {t("paper.relatedBySemantic")}
      </Text>
      {references.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t("paper.noRelatedPapers")} />
      ) : (
        <List
          rowKey="id"
          dataSource={references}
          renderItem={(item) => (
            <List.Item style={{ padding: "10px 0" }}>
              <Link to={`/paper/${item.id}`} style={{ flex: 1, minWidth: 0 }}>
                <Text
                  className="pf-serif pf-link"
                  style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.5 }}
                >
                  {item.title}
                </Text>
                <div style={{ fontSize: 12, color: "var(--pf-text-placeholder)", marginTop: 4 }}>
                  {formatAuthors(item.authors, 3)}
                  {item.year && (
                    <>
                      <span style={{ margin: "0 8px", color: "var(--pf-text-placeholder)" }}>
                        ·
                      </span>
                      {item.year}
                    </>
                  )}
                </div>
              </Link>
            </List.Item>
          )}
        />
      )}
    </div>
  );
}

export default function DetailPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [paper, setPaper] = useState<Paper | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [enrichLoading, setEnrichLoading] = useState(false);
  const [enrichModalOpen, setEnrichModalOpen] = useState(false);
  const [enrichPreview, setEnrichPreview] = useState<{ original: Paper; enriched: Paper } | null>(
    null,
  );
  const [citationStats, setCitationStats] = useState<CitationSentimentStats | null>(null);
  const [annotationsRefreshKey, setAnnotationsRefreshKey] = useState(0);

  const { items: metadataItems } = useMetadataActions({
    paper,
    onEnrichOpen: () => {
      setEnrichLoading(true);
      previewEnrichPaperMetadata(paper!.id)
        .then((preview) => {
          setEnrichPreview(preview);
          setEnrichModalOpen(true);
        })
        .catch((e: unknown) => {
          const err = e as Error & { response?: { data?: { detail?: string } } };
          message.error(err?.response?.data?.detail || err?.message || t("paper.enrichError", "元数据补全失败"));
        })
        .finally(() => setEnrichLoading(false));
    },
    onUpdate: (updated) => setPaper(updated),
    onAnnotationsExtracted: () => setAnnotationsRefreshKey((k) => k + 1),
  });

  // Deep-link params: ?tab=pdf&page=12&highlight=method
  const tabParam = searchParams.get("tab");
  const pageParam = searchParams.get("page");
  const highlightParam = searchParams.get("highlight");
  const initialPage = pageParam ? parseInt(pageParam, 10) : undefined;
  const highlightText = highlightParam || undefined;
  const activeTab = [
    "abstract",
    "pdf",
    "cite",
    "notes",
    "citations",
    "relationGraph",
    "annotations",
    "figures",
    "depthReview",
  ].includes(tabParam || "")
    ? (tabParam as string)
    : "abstract";

  useEffect(() => {
    let active = true;
    setLoading(true);
    setNotFound(false);
    fetchPaperById(id!)
      .then((p) => {
        if (!active) return;
        if (!p) setNotFound(true);
        else setPaper(p);
      })
      .finally(() => active && setLoading(false));

    fetchPaperSentiment(id!)
      .then((stats) => {
        if (!active) return;
        setCitationStats(stats);
      })
      .catch(() => {
        // 被引情感接口失败不影响详情页主流程
      });
    return () => {
      active = false;
    };
  }, [id]);

  const bibtex = useMemo(() => {
    if (!paper) return "";
    return `@article{${paper.id},
  author = {${paper.authors.join(" and ")}},
  title = {${paper.title}},
  journal = {${paper.journal || "arXiv preprint"}},
  year = {${paper.year}},
  doi = {${paper.id}}
}`;
  }, [paper]);

  if (loading) {
    return (
      <div className="pf-page-read">
        <Skeleton active paragraph={{ rows: 12 }} />
      </div>
    );
  }

  if (notFound || !paper) {
    return (
      <Result
        status="404"
        title={t("paper.paperNotFound")}
        subTitle={t("paper.paperNotFoundDesc", { id })}
        extra={
          <Button type="primary" onClick={() => navigate("/")}>
            {t("common.backHome")}
          </Button>
        }
      />
    );
  }

  const tabItems = [
    {
      key: "abstract",
      label: t("detail.tabAbstract"),
      children: <AbstractTab paper={paper} />,
    },
    {
      key: "pdf",
      label: t("detail.tabPdf"),
      children: <PdfTab paper={paper} initialPage={initialPage} highlightText={highlightText} />,
    },
    {
      key: "cite",
      label: t("detail.tabCite"),
      children: <CiteTab bibtex={bibtex} />,
    },
    {
      key: "notes",
      label: t("detail.tabNotes"),
      children: <NoteList paperId={paper.id} />,
    },
    {
      key: "citations",
      label: t("detail.tabCitations"),
      children: <CitationRelationsTab paper={paper} />,
    },
    {
      key: "relationGraph",
      label: t("detail.tabRelationGraph"),
      children: <RelationGraphTab paperId={paper.id} />,
    },
    {
      key: "annotations",
      label: t("detail.tabAnnotations", "批注"),
      children: <PdfAnnotationsTab paperId={paper.id} refreshKey={annotationsRefreshKey} />,
    },
    {
      key: "figures",
      label: t("detail.tabFigures", "图表"),
      children: <FigureDetailsList paperId={paper.id} />,
    },
    {
      key: "depthReview",
      label:
        (paper.category ?? "").trim().toLowerCase() === "report"
          ? t("detail.tabReflectionReview", "感悟报告评审")
          : t("detail.tabDepthReview", "深度评审"),
      children: <DepthReviewTab paperId={paper.id} category={paper.category} />,
    },
  ];

  return (
    <div className="pf-page-read">
      <Link
        to="/"
        style={{
          fontSize: 13,
          color: "var(--pf-text-muted)",
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
        }}
      >
        <ArrowLeft /> {t("paper.backToList")}
      </Link>

      <Card className="pf-glass-card" variant="borderless" style={{ marginTop: 16, padding: 8 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            gap: 16,
          }}
        >
          <div style={{ flex: 1 }}>
            <Title
              level={2}
              className="pf-serif"
              style={{
                fontSize: 28,
                fontWeight: 700,
                lineHeight: 1.35,
                marginBottom: 12,
                color: "var(--pf-text-primary)",
              }}
            >
              {paper.title}
            </Title>
            <Text style={{ color: "var(--pf-text-muted)", fontSize: 15, lineHeight: 1.8 }}>
              {formatAuthors(paper.authors, 6)}
            </Text>
            <Divider style={{ margin: "14px 0" }} />
            <Space size={8} wrap>
              <Tag color={SOURCE_COLOR[paper.source]}>{paper.source.toUpperCase()}</Tag>
              <Tag>{paper.year}</Tag>
              <Tag color="blue">
                {t("paper.citations")} {paper.citations.toLocaleString()}
              </Tag>
              <SentimentBadge citationCounts={citationStats?.counts} />
              {paper.tags.map((t) => (
                <Tag key={t} variant="filled">
                  {t}
                </Tag>
              ))}
            </Space>
          </div>

          <Space orientation="vertical" size="small">
            <FavoriteButton paper={paper} variant="primary" size="middle" showLabel />
            <Button
              icon={<LinkIcon />}
              href={paper.pdfUrl || arxivUrl(paper.id)}
              target="_blank"
            >
              {t("paper.viewOriginal")}
            </Button>
            <Space.Compact block>
              <Button
                loading={enrichLoading}
                onClick={() => {
                  const enrichItem = metadataItems.find((i) => i.key === "enrich");
                  enrichItem?.onClick();
                }}
              >
                <Zap /> {t("paper.enrichMetadata", "补全元数据")}
              </Button>
              <Dropdown
                menu={{
                  items: metadataItems
                    .filter((i) => i.key !== "enrich")
                    .map((item) => ({
                      key: item.key,
                      icon: <item.icon />,
                      label: item.label,
                      onClick: () => item.onClick(),
                    })),
                }}
              >
                <Button
                  aria-label="paper.metadataActions"
                  icon={<ChevronDown />}
                  loading={enrichLoading}
                />
              </Dropdown>
            </Space.Compact>
          </Space>
        </div>

        {/* WP-1.2: 扫描件状态标识 */}
        {paper.isScanned && paper.ocrStatus === "done" && (
          <div
            style={{
              marginTop: 12,
              padding: "8px 14px",
              background: "#fff7e6",
              border: "1px solid #ffd8bf",
              borderRadius: 8,
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <OcrBadge ocrStatus={paper.ocrStatus} isScanned={paper.isScanned} showLabel />
            <span style={{ color: "#d46b08", fontSize: 13 }}>
              {t("paper.scannedDetail", "扫描版 PDF，已通过 OCR 提取文本")}
            </span>
          </div>
        )}

        <Divider />

        <Tabs
          activeKey={activeTab}
          onChange={(key) => {
            const next = new URLSearchParams(searchParams);
            if (key === "abstract") {
              next.delete("tab");
            } else {
              next.set("tab", key);
            }
            // Keep page/highlight only when staying on pdf tab
            if (key !== "pdf") {
              next.delete("page");
              next.delete("highlight");
            }
            setSearchParams(next, { replace: true });
          }}
          items={tabItems}
          style={{ minHeight: 320 }}
        />
      </Card>

      <EnrichDiffModal
        open={enrichModalOpen}
        original={enrichPreview?.original ?? null}
        enriched={enrichPreview?.enriched ?? null}
        loading={enrichLoading}
        onCancel={() => {
          setEnrichModalOpen(false);
          setEnrichPreview(null);
        }}
        onAccept={async () => {
          if (!paper) return;
          setEnrichLoading(true);
          try {
            const updated = await enrichPaperMetadata(paper.id);
            setPaper(updated);
            message.success(t("paper.enrichSuccess", "元数据补全成功"));
          } catch (err0: unknown) {
            const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
            message.error(e?.message || t("paper.enrichError", "元数据补全失败"));
          } finally {
            setEnrichLoading(false);
            setEnrichModalOpen(false);
            setEnrichPreview(null);
          }
        }}
      />
    </div>
  );
}
