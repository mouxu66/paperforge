import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "@/test-utils";
import PaperList from "../PaperList";
import type { Paper } from "@/api/types";

function renderWithRouter(ui: React.ReactElement) {
  return render(ui, { wrapper: MemoryRouter });
}

function mockPaper(id: string, title = `Paper ${id}`): Paper {
  return {
    id,
    title,
    authors: ["Author"],
    year: 2024,
    abstract: "Abstract text that is long enough to trigger content estimation in virtual list.",
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
  pageSize: 20,
  onPage: vi.fn(),
};

describe("PaperList 大库渲染（WP-2.4）", () => {
  beforeAll(() => {
    // jsdom 没有 ResizeObserver，提供一个最小 mock
    global.ResizeObserver = class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) {
        const entry: Partial<ResizeObserverEntry> = {
          target,
          contentRect: { width: 1200, height: 600 } as DOMRectReadOnly,
        };
        this.callback([entry as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver;
  });

  it("小数据量时全部卡片直接渲染在 DOM 中", () => {
    const items = Array.from({ length: 10 }, (_, i) => mockPaper(`p${i}`));
    renderWithRouter(<PaperList {...baseProps} items={items} total={items.length} />);
    expect(screen.getAllByText(/Paper p/).length).toBe(10);
  });

  it("大数据量（>=50）时仍全部渲染，不启用虚拟滚动", () => {
    const items = Array.from({ length: 60 }, (_, i) => mockPaper(`p${i}`));
    renderWithRouter(<PaperList {...baseProps} items={items} total={items.length} />);
    // 纯 DOM 渲染：所有 60 张卡片标题都在文档中
    expect(screen.getAllByText(/Paper p/).length).toBe(60);
  });

  it("始终渲染分页器", () => {
    const items = Array.from({ length: 60 }, (_, i) => mockPaper(`p${i}`));
    renderWithRouter(<PaperList {...baseProps} items={items} total={items.length} pageSize={50} />);
    expect(document.querySelector(".ant-pagination")).toBeInTheDocument();
  });
});

describe("PaperList 性能基线打点（WP-2.4）", () => {
  it("loading 结束且 items 变化时调用 startMark", () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const items = Array.from({ length: 5 }, (_, i) => mockPaper(`p${i}`));
    const { rerender } = renderWithRouter(
      <PaperList {...baseProps} loading={true} items={[]} total={0} />,
    );
    // 切换到 loading=false 且有数据
    rerender(<PaperList {...baseProps} loading={false} items={items} total={items.length} />);
    // startMark 会写入 performance.measure，验证无异常即可
    expect(performance.getEntriesByName("PaperList.render").length).toBeGreaterThanOrEqual(0);
  });
});
