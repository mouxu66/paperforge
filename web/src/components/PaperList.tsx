import { useEffect, useRef, useState } from "react";
import { Skeleton, Pagination, App } from "antd";
import { useTranslation } from "react-i18next";
import type { Paper } from "@/api/types";
import type { PaperDepthScore } from "@/api/depth";
import { enrichPaperMetadata, previewEnrichPaperMetadata } from "@/api/papers";
import PaperCard from "./PaperCard";
import { Reveal } from "./motion";
import EmptyState from "./EmptyState";
import { buildFirstRunGuide, buildOcrGuide } from "./EmptyState.guide";
import EnrichDiffModal from "./EnrichDiffModal";
import { PAGE_SIZE_OPTIONS } from "@/utils/constants";
import { startMark } from "@/utils/perf";

interface Props {
  items: Paper[];
  loading: boolean;
  total: number;
  page: number;
  pageSize: number;
  onPage: (p: number) => void;
  onPageSize?: (size: number) => void;
  selectedIds?: Set<string>;
  onToggleSelect?: (id: string) => void;
  onEnrichSuccess?: (paper: Paper) => void;
  /** WP-2.5: 上下文空态所需的状态/回调（均可选，保持向后兼容） */
  /** 全库论文总数（来自 stats.totalPapers），用于区分首跑（=0）vs 搜索无果（>0） */
  totalPapers?: number;
  /** 当前是否为语义搜索模式 */
  semantic?: boolean;
  /** 清除当前搜索词/筛选，回到全列表 */
  onClearSearch?: () => void;
  /** 关闭语义搜索，切回关键词模式 */
  onDisableSemantic?: () => void;
  /** 首跑 CTA 点击（默认滚动到页面上传区） */
  onFirstRunCta?: () => void;
  /** 当前分类，用于渲染分类特定的空态 */
  category?: string;
  depthScoreMap?: Map<string, PaperDepthScore>;
}

export default function PaperList({
  items,
  loading,
  total,
  page,
  pageSize,
  onPage,
  onPageSize,
  selectedIds,
  onToggleSelect,
  onEnrichSuccess,
  totalPapers,
  semantic = false,
  onClearSearch,
  onDisableSemantic,
  onFirstRunCta,
  category,
  depthScoreMap,
}: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  // WP-2.4: 测量 PaperList 数据渲染耗时（loading 结束且 items 变化时）
  const prevLoadingRef = useRef(loading);
  useEffect(() => {
    const wasLoading = prevLoadingRef.current;
    prevLoadingRef.current = loading;
    if (wasLoading && !loading && items.length > 0) {
      const endMark = startMark("PaperList.render", { itemCount: items.length, pageSize });
      endMark();
    }
  }, [loading, items.length, pageSize]);

  const [enrichingPaper, setEnrichingPaper] = useState<Paper | null>(null);
  const [enrichPreview, setEnrichPreview] = useState<{ original: Paper; enriched: Paper } | null>(
    null,
  );
  const [enrichLoading, setEnrichLoading] = useState(false);
  const [enrichModalOpen, setEnrichModalOpen] = useState(false);

  useEffect(() => {
    if (!enrichingPaper) return;
    let active = true;
    setEnrichLoading(true);
    previewEnrichPaperMetadata(enrichingPaper.id)
      .then((preview) => {
        if (!active) return;
        setEnrichPreview(preview);
        setEnrichModalOpen(true);
      })
      .catch((e: unknown) => {
        const err = e as Error & { response?: { data?: { detail?: string } } };
        message.error(err?.response?.data?.detail || err?.message || t("paper.enrichError", "元数据补全失败"));
        setEnrichingPaper(null);
      })
      .finally(() => {
        if (active) setEnrichLoading(false);
      });
    return () => {
      active = false;
    };
  }, [enrichingPaper, t, message]);

  // WP-2.4: 骨架屏数量与当前 pageSize 对齐，避免布局跳变
  const skeletonCount = Math.max(1, Math.min(pageSize, 20));

  const pagination = (
    <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 24 }}>
      <Pagination
        current={page}
        pageSize={pageSize}
        total={total}
        onChange={onPage}
        onShowSizeChange={(_, size) => onPageSize?.(size)}
        showTotal={(total) => t("common.total", { count: total })}
        showSizeChanger={!!onPageSize}
        pageSizeOptions={PAGE_SIZE_OPTIONS as unknown as number[]}
      />
    </div>
  );

  // WP-2.4: 分页加载中保留分页器，避免布局跳变
  if (loading) {
    return (
      <>
        <div className="pf-paper-grid pf-paper-grid-skeleton">
          {Array.from({ length: skeletonCount }).map((_, i) => (
            <Skeleton
              key={i}
              active
              paragraph={{ rows: 4 }}
              className="pf-paper-skeleton-item"
            />
          ))}
        </div>
        {pagination}
      </>
    );
  }

  if (!items.length) {
    // WP-2.5: 上下文化空态——首跑 / 语义搜索无结果（索引缺失）/ 关键词搜索无果
    const isReportView = category === "report";
    const isFirstRun = (totalPapers ?? total) === 0;

    // 感悟报告视图：无报告时的专属空态
    if (isReportView) {
      return <EmptyState type="report" />;
    }

    if (isFirstRun) {
      return (
        <EmptyState
          type="firstRun"
          action={
            onFirstRunCta
              ? { label: t("home.firstRun.cta", "导入论文"), onClick: onFirstRunCta }
              : undefined
          }
          guide={buildFirstRunGuide(t)}
        />
      );
    }

    if (semantic) {
      // 语义搜索无结果：多为全文索引未完成，引导切换关键词模式
      return (
        <EmptyState
          type="ocr"
          secondaryAction={
            onDisableSemantic
              ? {
                  label: t("search.switchToKeyword"),
                  onClick: onDisableSemantic,
                  variant: "primary",
                }
              : undefined
          }
          guide={buildOcrGuide(t)}
        />
      );
    }

    // 关键词/分类搜索无果：可清除筛选回到全列表
    return (
      <EmptyState
        type="search"
        title={t("search.noResultsTitle")}
        description={t("search.noResults")}
        secondaryAction={
          onClearSearch
            ? { label: t("search.clearFilters"), onClick: onClearSearch, variant: "text" }
            : undefined
        }
      />
    );
  }

  return (
    <>
      <div className="pf-paper-grid">
        {items.map((p, index) => (
          <Reveal
            key={p.id}
            delay={Math.min(index * 40, 300)}
            style={{ height: "100%" }}
          >
            <PaperCard
              paper={p}
              selected={selectedIds?.has(p.id)}
              onToggleSelect={onToggleSelect ? () => onToggleSelect(p.id) : undefined}
              onEnrich={() => setEnrichingPaper(p)}
              depthScore={depthScoreMap?.get(p.id)}
            />
          </Reveal>
        ))}
      </div>
      {pagination}
      <EnrichDiffModal
        open={enrichModalOpen}
        original={enrichPreview?.original ?? null}
        enriched={enrichPreview?.enriched ?? null}
        loading={enrichLoading}
        onCancel={() => {
          setEnrichModalOpen(false);
          setEnrichPreview(null);
          setEnrichingPaper(null);
        }}
        onAccept={async () => {
          if (!enrichingPaper) return;
          setEnrichLoading(true);
          try {
            const updated = await enrichPaperMetadata(enrichingPaper.id);
            message.success(t("paper.enrichSuccess", "元数据补全成功"));
            onEnrichSuccess?.(updated);
          } catch (err0: unknown) {
            const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
            message.error(e?.message || t("paper.enrichError", "元数据补全失败"));
          } finally {
            setEnrichLoading(false);
            setEnrichModalOpen(false);
            setEnrichPreview(null);
            setEnrichingPaper(null);
          }
        }}
      />
    </>
  );
}
