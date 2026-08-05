import { Radar, ChevronDown } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Collapse, Space, Typography, Tabs } from "antd";

import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import SearchBar from "@/components/SearchBar";
import StatCards from "@/components/StatCards";
import PaperList from "@/components/PaperList";
import UploadPaper from "@/components/UploadPaper";
import ArxivImport from "@/components/ArxivImport";
import SourceFilter from "@/components/SourceFilter";
import ReflectionUpload from "@/components/ReflectionUpload";
import { BatchTagModal, TagManagerModal } from "@/components/TagManager";
import HomeHeader from "@/components/home/HomeHeader";
import HomeToolbar from "@/components/home/HomeToolbar";
import SelectionToolbar from "@/components/home/SelectionToolbar";
import V4ReviewPanel from "@/components/home/V4ReviewPanel";
import ResearchPulseCard from "@/components/home/ResearchPulseCard";
import ResearchWorkbenchPanel from "@/components/home/ResearchWorkbenchPanel";

import { invalidatePaperQueryCache, usePaperStore } from "@/store/usePaperStore";
import { useDepthStore } from "@/store/useDepthStore";
import { useSelection } from "@/hooks/useSelection";
import { useHomeActions } from "@/hooks/useHomeActions";

import DepthEvalModal from "@/components/home/DepthEvalModal";
import DeleteConfirmModal from "@/components/home/DeleteConfirmModal";
import { getDepthScoresByPaperIds } from "@/api/depth";

export default function HomePage() {
  const navigate = useNavigate();
  const { t } = useTranslation();

  // 使用字段级 selector，避免搜索、分页或加载状态变化时订阅整个页面 Store。
  const items = usePaperStore((s) => s.items);
  const total = usePaperStore((s) => s.total);
  const loading = usePaperStore((s) => s.loading);
  const keyword = usePaperStore((s) => s.keyword);
  const category = usePaperStore((s) => s.category);
  const sort = usePaperStore((s) => s.sort);
  const source = usePaperStore((s) => s.source);
  const page = usePaperStore((s) => s.page);
  const pageSize = usePaperStore((s) => s.pageSize);
  const stats = usePaperStore((s) => s.stats);
  const semantic = usePaperStore((s) => s.semantic);
  const setKeyword = usePaperStore((s) => s.setKeyword);
  const setSort = usePaperStore((s) => s.setSort);
  const setSource = usePaperStore((s) => s.setSource);
  const setCategoryAndSource = usePaperStore((s) => s.setCategoryAndSource);
  const setPage = usePaperStore((s) => s.setPage);
  const setPageSize = usePaperStore((s) => s.setPageSize);
  const setSemantic = usePaperStore((s) => s.setSemantic);
  const loadPapers = usePaperStore((s) => s.loadPapers);

  const addTask = useDepthStore((s) => s.addTask);

  // G1: 选择模式状态 + 派生值（抽到 useSelection hook）
  const selection = useSelection(items);
  const { selectMode, selectedIds, selectedPapers, estimatedMinutes } = selection;

  // G1: 3 个批量动作 handler + loading（抽到 useHomeActions hook）
  const actions = useHomeActions(selection, addTask, loadPapers);
  const { handleBatchDelete, handleDepthSubmit, handleV4Review } = actions;

  // Modal states（彼此独立，保留在页面层；下沉到 HomeModals 收益递减，暂不抽）
  const [reflectionUploadOpen, setReflectionUploadOpen] = useState(false);
  const [batchTagOpen, setBatchTagOpen] = useState(false);
  const [tagManagerOpen, setTagManagerOpen] = useState(false);
  const [depthModalOpen, setDepthModalOpen] = useState(false);
  const [deleteModalOpen, setDeleteModalOpen] = useState(false);
  // P2 首页降噪：把占空间最大的「V4.1 深度审稿记录」大表折叠进高级工具区，
  // 让首屏聚焦「搜索 → 上传 → 最近论文」主路径；轻量动作条（HomeToolbar）
  // 与选择工具条（SelectionToolbar）始终保留在折叠区外，保证入口随时可用。
  const [advancedOpen, setAdvancedOpen] = useState<string[]>([]);
  const [advancedMounted, setAdvancedMounted] = useState(false);
  const workbenchUploadTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (workbenchUploadTimerRef.current !== null) {
        window.clearTimeout(workbenchUploadTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    loadPapers();
  }, [keyword, category, sort, source, page, semantic, loadPapers]);

  const [depthScoreMap, setDepthScoreMap] = useState<Map<string, { verdict: string | null; noveltyScore: number | null }>>(new Map());

  // 加载当前页论文的 DEPTH 评分
  useEffect(() => {
    if (items.length === 0) return;
    getDepthScoresByPaperIds(items.map((p) => p.id))
      .then((scores) => {
        const map = new Map<string, { verdict: string | null; noveltyScore: number | null }>();
        for (const [pid, s] of Object.entries(scores)) {
          map.set(pid, {
            verdict: s.verdict,
            noveltyScore: s.novelty_score,
          });
        }
        setDepthScoreMap(map);
      })
      .catch(() => {});
  }, [items]);

  // WP-2.5: 上下文空态回调
  const handleClearSearch = useCallback(() => {
    setKeyword("");
    setSource("all");
    setPage(1);
  }, [setKeyword, setSource, setPage]);

  const handleDisableSemantic = useCallback(() => {
    setSemantic(false);
  }, [setSemantic]);

  // 首跑 CTA：滚动到页面上传区（UploadPaper 自带 id）
  const handleFirstRunCta = useCallback(() => {
    const el = document.getElementById("upload-paper-anchor");
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, []);

  const isReportTab = category === "report";
  const handleWorkbenchUpload = useCallback(() => {
    if (isReportTab) {
      // 报告视图隐藏 UploadPaper；先回到论文视图，再定位到导入区。
      setCategoryAndSource("all", "all");
      if (workbenchUploadTimerRef.current !== null) {
        window.clearTimeout(workbenchUploadTimerRef.current);
      }
      workbenchUploadTimerRef.current = window.setTimeout(() => {
        document.getElementById("upload-paper-anchor")?.scrollIntoView({
          behavior: "smooth",
          block: "center",
        });
        workbenchUploadTimerRef.current = null;
      }, 0);
      return;
    }
    handleFirstRunCta();
  }, [handleFirstRunCta, isReportTab, setCategoryAndSource]);

  return (
    <div className="pf-home-page">
      <HomeHeader />

      {/* 顶部「论文 / 感悟报告」标签页：与侧边栏分类互补，提供一级内容切换 */}
      <div className="pf-home-tabs" style={{ marginBottom: 20 }}>
        <Tabs
          activeKey={isReportTab ? "report" : "paper"}
          onChange={(key) => {
            const nextCategory = key === "report" ? "report" : "all";
            // 切换到感悟报告时重置来源筛选，避免 source != all 导致报告列表为空
            const nextSource = nextCategory === "report" ? "all" : source;
            setCategoryAndSource(nextCategory, nextSource);
          }}
          items={[
            {
              key: "paper",
              label: t("home.tabs.papers", "论文"),
            },
            {
              key: "report",
              label: t("home.tabs.reports", "感悟报告"),
            },
          ]}
        />
      </div>

      <div className="pf-home-index" aria-label={t("home.statsLabel", "研究库索引")}>
        <div className="pf-home-index-heading">
          <span>{t("home.statsLabel", "研究库索引")}</span>
          <span aria-hidden="true">{isReportTab ? "REPORTS" : "LIBRARY"}</span>
        </div>
        <div className="pf-home-stats">
          <StatCards stats={stats} loading={!stats} isReport={isReportTab} />
        </div>
      </div>

      {/* 研究脉搏：有 DEPTH 任务时显示实时进度与最近结果 */}
      <ResearchPulseCard />

      <ResearchWorkbenchPanel
        stats={stats}
        papers={items}
        isReport={isReportTab}
        onUpload={handleWorkbenchUpload}
      />

      {/* P2 渐进式披露：把占空间最大的「V4.1 深度审稿记录」大表折叠进高级工具区，
          让首屏聚焦「搜索 → 上传 → 最近论文」主路径；轻量动作条与选择工具条
          始终保留在折叠区外，保证入口随时可用。 */}
      <Collapse
        className="pf-advanced-tools"
        activeKey={advancedOpen}
        onChange={(keys) => {
          const nextKeys = keys as string[];
          setAdvancedOpen(nextKeys);
          // 首次展开后保留子树挂载，折叠只隐藏它，避免重新打开时丢失审稿面板状态。
          if (nextKeys.includes("advanced")) setAdvancedMounted(true);
        }}
        ghost
        style={{ marginBottom: 20 }}
        expandIcon={() => null}
        items={[
          {
            key: "advanced",
            label: (
              <Space size={6}>
                <Radar style={{ color: "var(--pf-primary)" }} />
                <Typography.Text strong style={{ fontSize: 14 }}>
                  {t("home.advancedToolsTitle")}
                </Typography.Text>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {t("home.advancedToolsHint")}
                </Typography.Text>
                <ChevronDown
                  style={{
                    fontSize: 10,
                    color: "var(--pf-text-placeholder)",
                    transition: "transform 0.2s ease",
                    transform: advancedOpen.includes("advanced") ? "rotate(180deg)" : "none",
                  }}
                />
              </Space>
            ),
            // 只有展开高级工具时才挂载审稿面板，避免首屏启动轮询和加载请求。
            children: advancedMounted ? (
              <div style={{ paddingTop: 12 }}>
                <V4ReviewPanel />
              </div>
            ) : null,
          },
        ]}
      />

      {/* 轻量动作条始终在折叠区外渲染，保证「选择论文 / 提交感悟 / 标签管理」随时可用 */}
      <div style={{ marginBottom: 16 }}>
        <HomeToolbar
          selectMode={selectMode}
          onToggleSelectMode={selection.toggleSelectMode}
          onReflectionUpload={() => setReflectionUploadOpen(true)}
          onTagManager={() => setTagManagerOpen(true)}
          isReportView={isReportTab}
        />
      </div>

      {/* 选择模式工具条始终在折叠区外渲染，避免被 Collapse 藏住 */}
      {selectMode && (
        <SelectionToolbar
          selectedIds={selectedIds}
          items={items}
          onToggleSelectAll={selection.toggleSelectAll}
          onExitSelectMode={selection.exitSelectMode}
          onV4Review={handleV4Review}
          onDepthEval={() => setDepthModalOpen(true)}
          onBatchTag={() => setBatchTagOpen(true)}
          onDelete={() => setDeleteModalOpen(true)}
          v4Loading={actions.v4SelectedLoading}
          depthLoading={actions.depthLoading}
          isReportView={isReportTab}
        />
      )}

      <section className="pf-home-library" aria-labelledby="pf-home-library-title">
        <div className="pf-home-library-heading">
          <div>
            <span className="pf-home-library-kicker">{t("home.libraryKicker", "Research library")}</span>
            <Typography.Title id="pf-home-library-title" level={4}>
              {isReportTab ? t("home.tabs.reports", "感悟报告") : t("home.libraryTitle", "论文档案")}
            </Typography.Title>
          </div>
          <span className="pf-home-library-count">{t("common.total", { count: total })}</span>
        </div>
        <div className="pf-home-search" style={{ marginBottom: 20 }}>
          <SearchBar
            keyword={keyword}
            sort={sort}
            semantic={semantic}
            onKeyword={setKeyword}
            onSort={setSort}
            onSemantic={setSemantic}
          />
        </div>

        {!isReportTab && (
          <div style={{ marginBottom: 16 }}>
            <SourceFilter stats={stats} currentSource={source} onSourceChange={setSource} />
          </div>
        )}

        {!isReportTab && (
          <div style={{ marginBottom: 12, display: "flex", gap: 12, alignItems: "flex-start" }}>
            <ArxivImport />
          </div>
        )}

        {!isReportTab && <UploadPaper />}

        <PaperList
          items={items}
          loading={loading}
          total={total}
          page={page}
          pageSize={pageSize}
          onPage={setPage}
          onPageSize={setPageSize}
          selectedIds={selectMode ? selectedIds : undefined}
          onToggleSelect={selectMode ? selection.toggleSelect : undefined}
          onEnrichSuccess={() => {
            invalidatePaperQueryCache();
            return loadPapers();
          }}
          totalPapers={stats?.totalPapers}
          semantic={semantic}
          onClearSearch={handleClearSearch}
          onDisableSemantic={handleDisableSemantic}
          onFirstRunCta={handleFirstRunCta}
          category={category}
          depthScoreMap={depthScoreMap}
        />
      </section>

      <ReflectionUpload
        open={reflectionUploadOpen}
        onClose={() => setReflectionUploadOpen(false)}
        onSuccess={() => navigate("/depth-v4?tab=report")}
      />

      <BatchTagModal
        open={batchTagOpen}
        selectedIds={Array.from(selectedIds)}
        onClose={() => setBatchTagOpen(false)}
        onSuccess={() => {
          invalidatePaperQueryCache();
          loadPapers();
          selection.exitSelectMode();
        }}
      />

      <TagManagerModal open={tagManagerOpen} onClose={() => setTagManagerOpen(false)} />

      <DepthEvalModal
        open={depthModalOpen}
        onCancel={() => setDepthModalOpen(false)}
        onOk={handleDepthSubmit}
        confirmLoading={actions.depthLoading}
        selectedPapers={selectedPapers}
        estimatedMinutes={estimatedMinutes}
      />

      <DeleteConfirmModal
        open={deleteModalOpen}
        onCancel={() => setDeleteModalOpen(false)}
        onOk={handleBatchDelete}
        confirmLoading={actions.deleteLoading}
        selectedCount={selectedIds.size}
      />
    </div>
  );
}
