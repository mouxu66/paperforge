import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "@/test-utils";
import PaperList from "../PaperList";
import type { Paper } from "@/api/types";

// PaperCard 内部使用 useNavigate，需 Router 上下文；空态测试不渲染 PaperCard，
// 但「有 items」测试会渲染卡片，统一用 MemoryRouter 包装保持一致。
function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

// 构造最小可渲染的 Paper 卡片所需字段
function mockPaper(id = "p1"): Paper {
  return {
    id,
    title: "Test Paper",
    authors: ["Author A"],
    year: 2024,
    abstract: "Abstract",
    category: "cs",
    tags: [],
    citations: 0,
    chunkCount: 1,
    indexSize: 100,
    pdfUrl: "https://example.com/test.pdf",
    source: "arxiv",
  };
}

const baseProps = {
  items: [] as Paper[],
  loading: false,
  total: 0,
  page: 1,
  pageSize: 8,
  onPage: vi.fn(),
};

describe("PaperList 空态选择（WP-2.5）", () => {
  it("totalPapers=0 → 渲染 firstRun 引导 + 导入 CTA", () => {
    const onFirstRunCta = vi.fn();
    renderWithRouter(<PaperList {...baseProps} totalPapers={0} onFirstRunCta={onFirstRunCta} />);
    expect(screen.getByText("home.firstRun.title")).toBeInTheDocument();
    expect(screen.getByText("home.firstRun.guideTitle")).toBeInTheDocument();
    expect(screen.getByText("home.firstRun.step1Title")).toBeInTheDocument();
    const cta = screen.getByText("home.firstRun.cta");
    fireEvent.click(cta);
    expect(onFirstRunCta).toHaveBeenCalledTimes(1);
  });

  it("totalPapers>0 + semantic=true → 渲染 ocr 索引缺失态 + 切换关键词按钮", () => {
    const onDisableSemantic = vi.fn();
    renderWithRouter(
      <PaperList
        {...baseProps}
        totalPapers={679}
        semantic={true}
        onDisableSemantic={onDisableSemantic}
      />,
    );
    expect(screen.getByText("search.noOcrResultsTitle")).toBeInTheDocument();
    expect(screen.getByText("search.noOcrResultsDesc")).toBeInTheDocument();
    expect(screen.getByText("search.noOcrGuideTitle")).toBeInTheDocument();
    const btn = screen.getByText("search.switchToKeyword");
    fireEvent.click(btn);
    expect(onDisableSemantic).toHaveBeenCalledTimes(1);
  });

  it("totalPapers>0 + semantic=false → 渲染 search 无结果态 + 清除筛选按钮", () => {
    const onClearSearch = vi.fn();
    renderWithRouter(
      <PaperList {...baseProps} totalPapers={679} semantic={false} onClearSearch={onClearSearch} />,
    );
    expect(screen.getByText("search.noResultsTitle")).toBeInTheDocument();
    expect(screen.getByText("search.noResults")).toBeInTheDocument();
    const btn = screen.getByText("search.clearFilters");
    fireEvent.click(btn);
    expect(onClearSearch).toHaveBeenCalledTimes(1);
  });

  it("totalPapers 未定义且 total=0 → 回退走 firstRun", () => {
    renderWithRouter(<PaperList {...baseProps} totalPapers={undefined} />);
    expect(screen.getByText("home.firstRun.title")).toBeInTheDocument();
  });

  it("totalPapers 未定义且 total>0 → 走 search 无结果态", () => {
    renderWithRouter(<PaperList {...baseProps} totalPapers={undefined} total={10} />);
    expect(screen.getByText("search.noResultsTitle")).toBeInTheDocument();
    expect(screen.queryByText("home.firstRun.title")).not.toBeInTheDocument();
  });

  it("loading=true → 渲染与 pageSize 对齐的骨架屏，不渲染空态", () => {
    renderWithRouter(<PaperList {...baseProps} loading={true} />);
    // antd Skeleton 默认带 ant-skeleton 类；loading 分支骨架屏数量 = pageSize
    const skeletons = document.querySelectorAll(".ant-skeleton");
    expect(skeletons.length).toBe(baseProps.pageSize);
    expect(screen.queryByText("home.firstRun.title")).not.toBeInTheDocument();
    expect(screen.queryByText("search.noResultsTitle")).not.toBeInTheDocument();
  });

  it("有 items 时不渲染空态，渲染卡片", () => {
    renderWithRouter(<PaperList {...baseProps} items={[mockPaper()]} total={1} />);
    expect(screen.queryByText("home.firstRun.title")).not.toBeInTheDocument();
    expect(screen.queryByText("search.noResultsTitle")).not.toBeInTheDocument();
    expect(screen.getByText("Test Paper")).toBeInTheDocument();
  });

  it("loading=true 时仍渲染分页器（WP-2.4 避免布局跳变）", () => {
    renderWithRouter(<PaperList {...baseProps} loading={true} total={100} />);
    // antd Pagination 渲染后带 ant-pagination 类
    expect(document.querySelector(".ant-pagination")).toBeInTheDocument();
  });

  it("传入 onPageSize 时显示 pageSize 切换器", () => {
    const onPageSize = vi.fn();
    renderWithRouter(
      <PaperList {...baseProps} onPageSize={onPageSize} items={[mockPaper()]} total={100} />,
    );
    // antd Pagination 的 pageSize 切换器（真实 DOM 中 class 为 ant-pagination-options）
    expect(document.querySelector(".ant-pagination-options")).toBeInTheDocument();
  });

  it("onPageSize 未传入时不显示 pageSize 切换器", () => {
    renderWithRouter(<PaperList {...baseProps} items={[mockPaper()]} total={100} />);
    expect(document.querySelector(".ant-pagination-options")).not.toBeInTheDocument();
  });
});
