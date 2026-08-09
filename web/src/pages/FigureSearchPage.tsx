import { Image as ImageIcon, Search, FileText, Expand, Inbox } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Typography,
  Input,
  Card,
  Tag,
  Spin,
  Alert,
  Space,
  Tooltip,
  Image,
  Empty,
  Segmented,
  Select,
  Button,
} from "antd";

import { fetchHybridSearch } from "@/api/papers";
import type { Paper, UnifiedFigureHit } from "@/api/types";
import { API_BASE } from "@/api/client";
import PageHeader from "@/components/PageHeader";

// 与 PdfTab 一致：后端返回的相对 /api/... 地址需按 VITE_API_BASE 拼成完整地址，
// 否则在自定义 API 基址部署时 <img>/预览会指向错误源站。
function resolveAssetUrl(url: string): string {
  if (url.startsWith("/api")) return `${API_BASE}${url.slice("/api".length)}`;
  return url;
}

type ViewMode = "all" | "figures" | "papers";
type SortMode = "fused_score" | "score" | "page";

export default function FigureSearchPage() {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [figures, setFigures] = useState<UnifiedFigureHit[]>([]);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [searchError, setSearchError] = useState(false);
  const [viewMode, setViewMode] = useState<ViewMode>("all");
  const [sortMode, setSortMode] = useState<SortMode>("fused_score");

  const runSearch = async () => {
    const q = query.trim();
    if (!q) return;
    setLoading(true);
    setSearched(true);
    setSearchError(false);

    // 后端融合：/api/search/hybrid 同时返回论文级 RRF 与 figure 级向量结果，
    // 并在后端按论文 RRF 排名对 figure 做 boost 重排。
    try {
      const res = await fetchHybridSearch(q, 24);
      setFigures(res.fused_figures ?? []);
      setPapers(res.papers ?? []);
      // 仅当确实发生异常时提示错误；空结果由下方 EmptyState 处理，避免误报「向量不可用」。
    } catch (err) {
      console.error("[FigureSearch] hybrid search failed:", err);
      setFigures([]);
      setPapers([]);
      setSearchError(true);
    } finally {
      setLoading(false);
    }
  };

  // 按 fused_score 降序排序 figure
  const sortedFigures = [...figures].sort((a, b) => {
    if (sortMode === "fused_score") {
      return b.fused_score - a.fused_score;
    }
    if (sortMode === "score") {
      return b.score - a.score;
    }
    // page: 按论文标题 → 页码 → 图序号（同一论文的图聚在一起）
    const titleA = a.title || a.paper_id;
    const titleB = b.title || b.paper_id;
    if (titleA !== titleB) return titleA.localeCompare(titleB);
    if (a.page !== b.page) return a.page - b.page;
    return a.figure_index - b.figure_index;
  });

  const visibleFigures = viewMode === "papers" ? [] : sortedFigures;
  const visiblePapers = viewMode === "figures" ? [] : papers;

  const hasFigures = visibleFigures.length > 0;
  const hasPapers = visiblePapers.length > 0;
  const isEmpty = searched && !loading && !hasFigures && !hasPapers;

  return (
    <div>
      <PageHeader
        title={
          <>
            <ImageIcon aria-hidden="true" style={{ marginRight: 8, color: "var(--pf-primary)" }} />
            {t("figures.title")}
          </>
        }
        description={t("figures.subtitle")}
      />

      {/* 搜索框 + 筛选/排序（毛玻璃卡片承载，premium 视觉） */}
      <div className="pf-glass-card" style={{ padding: 20, marginBottom: 24 }}>
        <Input.Search
          size="large"
          allowClear
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onSearch={runSearch}
          placeholder={t("figures.placeholder")}
          enterButton={
            <>
              <Search /> {t("figures.searchButton")}
            </>
          }
          loading={loading}
        />
        <div
          style={{
            marginTop: 16,
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: 16,
          }}
        >
          <Segmented
            value={viewMode}
            onChange={(v) => setViewMode(v as ViewMode)}
            options={[
              { label: t("figures.viewAll"), value: "all" },
              { label: t("figures.viewFigures"), value: "figures" },
              { label: t("figures.viewPapers"), value: "papers" },
            ]}
          />
          <Select
            value={sortMode}
            onChange={(v) => setSortMode(v as SortMode)}
            style={{ minWidth: 160 }}
            options={[
              { label: t("figures.sortFusedScore"), value: "fused_score" },
              { label: t("figures.sortScore"), value: "score" },
              { label: t("figures.sortPage"), value: "page" },
            ]}
            disabled={viewMode === "papers"}
          />
        </div>
      </div>

      {loading && (
        <div style={{ textAlign: "center", padding: "40px 0" }}>
          <Spin size="large" />
        </div>
      )}

      {searchError && !loading && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          title={t("figures.searchError")}
        />
      )}

      {isEmpty && (
        <Card
          className="pf-glass-card"
          variant="borderless"
          style={{ padding: 24, marginBottom: 16 }}
        >
          <div style={{ textAlign: "center", padding: "24px 12px" }}>
            <Inbox
              style={{ fontSize: 40, color: "var(--pf-text-placeholder)", marginBottom: 12 }}
            />
            {/* 主信息：当前查询没有命中。措辞不武断「尚未抽取」，因为前端无法区分
                「库里没有图表」与「有图表但与关键词不匹配」两种情况。 */}
            <div
              className="pf-serif"
              style={{
                fontSize: 16,
                fontWeight: 600,
                color: "var(--pf-text-primary)",
                marginBottom: 6,
              }}
            >
              {t("figures.empty")}
            </div>
            {/* 辅助提示：如果是第一次使用且从未抽取过图表，给出可能原因与行动指引。
                使用「可能 / 或」语气，避免误导已有图表但不匹配的情况。 */}
            <div
              style={{
                fontSize: 13,
                color: "var(--pf-text-muted)",
                lineHeight: 1.6,
                maxWidth: 560,
                margin: "0 auto 16px",
              }}
            >
              {t("figures.emptyNoFiguresDesc")}
            </div>
            <Link to="/">                <Button type="primary" icon={<ImageIcon />}>
                {t("figures.emptyNoFiguresAction")}
              </Button>
            </Link>
          </div>
        </Card>
      )}

      {/* 命中图片画廊（主结果区） */}
      {hasFigures && (
        <section style={{ marginBottom: 32 }}>
          <Typography.Title level={5} style={{ marginBottom: 14 }}>
            {t("figures.figuresSection", { count: visibleFigures.length })}
          </Typography.Title>
          <Image.PreviewGroup
            items={visibleFigures.map((h) => ({ src: resolveAssetUrl(h.image_url) }))}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                gap: 18,
              }}
            >
              {visibleFigures.map((hit) => (
                <Card
                  key={`${hit.paper_id}-${hit.page}-${hit.figure_index}`}
                  className="pf-glass-card"
                  styles={{ body: { padding: 14 } }}
                  cover={
                    <div style={{ position: "relative" }}>
                      <Image
                        src={resolveAssetUrl(hit.image_url)}
                        alt={hit.title}
                        height={180}
                        style={{
                          objectFit: "cover",
                          borderTopLeftRadius: 14,
                          borderTopRightRadius: 14,
                          width: "100%",
                        }}
                        preview={{ mask: <Expand /> }}
                      />
                    </div>
                  }
                >
                  <Space orientation="vertical" size={6} style={{ width: "100%" }}>
                    <Link
                      to={`/paper/${hit.paper_id}`}
                      className="pf-link"
                      style={{ fontWeight: 600 }}
                    >
                      {hit.title || hit.paper_id}
                    </Link>
                    <Space size={4} wrap>
                      <Tag color="blue">{t("figures.page", { page: hit.page })}</Tag>
                      <Tag>{t("figures.figureIndex", { index: hit.figure_index })}</Tag>
                      <Tooltip title={t("figures.score")}>
                        <Tag color="green">{(hit.score * 100).toFixed(1)}%</Tag>
                      </Tooltip>
                    </Space>
                    <Space size={8} wrap>
                      <Link
                        to={`/paper/${hit.paper_id}?tab=pdf&page=${hit.page}`}
                        style={{ fontSize: 12 }}
                      >
                        <FileText /> {t("figures.viewPdfPage", { page: hit.page })}
                      </Link>
                    </Space>
                    {hit.ocr_text && (
                      <div
                        style={{
                          fontSize: 12,
                          color: "var(--pf-text-muted)",
                          display: "-webkit-box",
                          WebkitLineClamp: 3,
                          WebkitBoxOrient: "vertical",
                          overflow: "hidden",
                        }}
                      >
                        <strong>{t("figures.ocrLabel")}：</strong>
                        {hit.ocr_text}
                      </div>
                    )}
                  </Space>
                </Card>
              ))}
            </div>
          </Image.PreviewGroup>
        </section>
      )}

      {/* 相关论文（语义检索融合展示） */}
      {hasPapers && (
        <section>
          <Typography.Title level={5} style={{ marginBottom: 14 }}>
            {t("figures.papersSection", { count: visiblePapers.length })}
          </Typography.Title>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
              gap: 14,
            }}
          >
            {visiblePapers.slice(0, 12).map((p) => (
              <Card key={p.id} className="pf-glass-card" styles={{ body: { padding: 14 } }}>
                <Space align="start">
                  <FileText style={{ color: "var(--pf-primary)", marginTop: 4 }} />
                  <div>
                    <Link to={`/paper/${p.id}`} className="pf-link" style={{ fontWeight: 600 }}>
                      {p.title}
                    </Link>
                    <div
                      style={{ fontSize: 12, color: "var(--pf-text-placeholder)", marginTop: 2 }}
                    >
                      {p.authors?.slice(0, 3).join(", ")}
                      {p.year ? ` · ${p.year}` : ""}
                      {p.citations ? ` · ${p.citations} citations` : ""}
                    </div>
                  </div>
                </Space>
              </Card>
            ))}
          </div>
        </section>
      )}

      {/* 已检索但仅有图、无相关论文时的兜底提示 */}
      {hasFigures && !hasPapers && !loading && (
        <div style={{ marginTop: 8, color: "var(--pf-text-placeholder)", fontSize: 13 }}>
          {t("figures.noPapers")}
        </div>
      )}

      {!searched && !loading && (
        <div
          style={{ textAlign: "center", padding: "48px 0", color: "var(--pf-text-placeholder)" }}
        >
          <Empty description={t("figures.startPrompt")} />
        </div>
      )}
    </div>
  );
}
