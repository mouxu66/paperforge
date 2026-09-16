import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import EmptyState, { type EmptyStateType } from "../EmptyState";
import { buildFirstRunGuide, buildOcrGuide } from "../EmptyState.guide";

// test-setup.ts 已 mock react-i18next：t(key) => key；故文案用 key 断言

describe("EmptyState", () => {
  it("default 类型回退到 common.noData，不渲染主标题/引导", () => {
    render(<EmptyState type="default" />);
    expect(screen.getByText("common.noData")).toBeInTheDocument();
    // default 非 firstRun/search/ocr，无默认主标题
    expect(screen.queryByText("home.firstRun.title")).not.toBeInTheDocument();
    expect(screen.queryByText("home.firstRun.guideTitle")).not.toBeInTheDocument();
  });

  it("firstRun 类型渲染欢迎主标题 + 描述 + 默认引导步骤", () => {
    render(<EmptyState type="firstRun" />);
    expect(screen.getByText("home.firstRun.title")).toBeInTheDocument();
    expect(screen.getByText("home.firstRun.desc")).toBeInTheDocument();
    expect(screen.getByText("home.firstRun.guideTitle")).toBeInTheDocument();
    // 3 个默认步骤
    expect(screen.getByText("home.firstRun.step1Title")).toBeInTheDocument();
    expect(screen.getByText("home.firstRun.step2Title")).toBeInTheDocument();
    expect(screen.getByText("home.firstRun.step3Title")).toBeInTheDocument();
  });

  it("firstRun 渲染主 CTA 并响应点击", () => {
    const onClick = vi.fn();
    render(<EmptyState type="firstRun" action={{ label: "home.firstRun.cta", onClick }} />);
    const cta = screen.getByText("home.firstRun.cta");
    fireEvent.click(cta);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("search 类型渲染无结果标题 + 描述 + secondaryAction 文字按钮", () => {
    const onClear = vi.fn();
    render(
      <EmptyState
        type="search"
        secondaryAction={{
          label: "search.clearFilters",
          onClick: onClear,
          variant: "text",
        }}
      />,
    );
    expect(screen.getByText("search.noResultsTitle")).toBeInTheDocument();
    expect(screen.getByText("search.noResults")).toBeInTheDocument();
    fireEvent.click(screen.getByText("search.clearFilters"));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it("ocr 类型渲染索引缺失标题 + 描述 + 引导步骤", () => {
    render(<EmptyState type="ocr" guide={buildOcrGuide((k) => k)} />);
    expect(screen.getByText("search.noOcrResultsTitle")).toBeInTheDocument();
    expect(screen.getByText("search.noOcrResultsDesc")).toBeInTheDocument();
    expect(screen.getByText("search.noOcrGuideTitle")).toBeInTheDocument();
    expect(screen.getByText("search.noOcrStep1Title")).toBeInTheDocument();
    expect(screen.getByText("search.noOcrStep2Title")).toBeInTheDocument();
  });

  it("显式 title 覆盖默认标题", () => {
    render(<EmptyState type="search" title="自定义标题" />);
    expect(screen.getByText("自定义标题")).toBeInTheDocument();
    expect(screen.queryByText("search.noResultsTitle")).not.toBeInTheDocument();
  });

  it("显式 description 覆盖默认描述", () => {
    render(<EmptyState type="default" description="自定义空态文案" />);
    expect(screen.getByText("自定义空态文案")).toBeInTheDocument();
    expect(screen.queryByText("common.noData")).not.toBeInTheDocument();
  });

  it("buildFirstRunGuide 返回 3 个步骤", () => {
    const guide = buildFirstRunGuide((k) => k);
    expect(guide.steps).toHaveLength(3);
    expect(guide.title).toBe("home.firstRun.guideTitle");
  });

  it("buildOcrGuide 返回 2 个步骤", () => {
    const guide = buildOcrGuide((k) => k);
    expect(guide.steps).toHaveLength(2);
    expect(guide.title).toBe("search.noOcrGuideTitle");
  });

  it("primary + text 双按钮同时渲染", () => {
    render(
      <EmptyState
        type="firstRun"
        action={{ label: "主按钮", onClick: vi.fn() }}
        secondaryAction={{ label: "次按钮", onClick: vi.fn(), variant: "text" }}
      />,
    );
    expect(screen.getByText("主按钮")).toBeInTheDocument();
    expect(screen.getByText("次按钮")).toBeInTheDocument();
  });

  it("search 类型默认渲染无结果标题 + 描述", () => {
    render(<EmptyState type="search" />);
    expect(screen.getByText("search.noResultsTitle")).toBeInTheDocument();
    expect(screen.getByText("search.noResults")).toBeInTheDocument();
  });

  it("figures 类型默认渲染 figures.empty 描述", () => {
    render(<EmptyState type="figures" />);
    expect(screen.getByText("figures.empty")).toBeInTheDocument();
  });

  it("ocr 类型默认渲染索引缺失标题 + 描述（不传 guide 也成立）", () => {
    render(<EmptyState type="ocr" />);
    expect(screen.getByText("search.noOcrResultsTitle")).toBeInTheDocument();
    expect(screen.getByText("search.noOcrResultsDesc")).toBeInTheDocument();
  });

  it("firstRun/search/ocr 的 description 可被显式覆盖", () => {
    const { rerender } = render(<EmptyState type="firstRun" description="覆盖描述" />);
    expect(screen.getByText("覆盖描述")).toBeInTheDocument();
    expect(screen.queryByText("home.firstRun.desc")).not.toBeInTheDocument();

    rerender(<EmptyState type="search" description="搜索覆盖" />);
    expect(screen.getByText("搜索覆盖")).toBeInTheDocument();
    expect(screen.queryByText("search.noResults")).not.toBeInTheDocument();

    rerender(<EmptyState type="ocr" description="OCR 覆盖" />);
    expect(screen.getByText("OCR 覆盖")).toBeInTheDocument();
    expect(screen.queryByText("search.noOcrResultsDesc")).not.toBeInTheDocument();
  });

  it("pdf / favorites / notes / citations / upload 类型使用默认 common.noData", () => {
    const types: EmptyStateType[] = ["pdf", "favorites", "notes", "citations", "upload"];
    for (const type of types) {
      const { unmount } = render(<EmptyState type={type} />);
      expect(screen.getByText("common.noData")).toBeInTheDocument();
      unmount();
    }
  });

  it("report 类型渲染感悟报告空态标题 + 描述 + BookOutlined 图标", () => {
    render(<EmptyState type="report" />);
    expect(screen.getByText("reports.emptyTitle")).toBeInTheDocument();
    expect(screen.getByText("reports.emptyDesc")).toBeInTheDocument();
  });

  it("report 类型显式 title/description 可覆盖默认值", () => {
    render(<EmptyState type="report" title="自定义标题" description="自定义描述" />);
    expect(screen.getByText("自定义标题")).toBeInTheDocument();
    expect(screen.getByText("自定义描述")).toBeInTheDocument();
    expect(screen.queryByText("reports.emptyTitle")).not.toBeInTheDocument();
    expect(screen.queryByText("reports.emptyDesc")).not.toBeInTheDocument();
  });

  // ── 未配置模型（noModel）─────────────────────────────────────

  it("noModel 类型渲染标题、描述与 3 步引导", () => {
    render(<EmptyState type="noModel" />);
    expect(screen.getByText("noModel.title")).toBeInTheDocument();
    expect(screen.getByText("noModel.desc")).toBeInTheDocument();
    expect(screen.getByText("noModel.guideTitle")).toBeInTheDocument();
    expect(screen.getByText("noModel.step1Title")).toBeInTheDocument();
    expect(screen.getByText("noModel.step2Title")).toBeInTheDocument();
    expect(screen.getByText("noModel.step3Title")).toBeInTheDocument();
  });

  it("noModel 类型支持主行动按钮（去模型管理）", () => {
    const onClick = vi.fn();
    render(<EmptyState type="noModel" action={{ label: "ask.goToModels", onClick }} />);
    fireEvent.click(screen.getByText("ask.goToModels"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  // ── 单篇论文索引未完成（indexPending）────────────────────────

  it("indexPending 类型渲染标题、描述与 3 步引导", () => {
    render(<EmptyState type="indexPending" />);
    expect(screen.getByText("indexPending.title")).toBeInTheDocument();
    expect(screen.getByText("indexPending.desc")).toBeInTheDocument();
    expect(screen.getByText("indexPending.guideTitle")).toBeInTheDocument();
    expect(screen.getByText("indexPending.step1Title")).toBeInTheDocument();
    expect(screen.getByText("indexPending.step3Title")).toBeInTheDocument();
  });

  it("新增类型不改变既有类型的回退行为", () => {
    const { unmount } = render(<EmptyState type="default" />);
    expect(screen.getByText("common.noData")).toBeInTheDocument();
    expect(screen.queryByText("noModel.title")).not.toBeInTheDocument();
    unmount();

    render(<EmptyState type="figures" />);
    expect(screen.getByText("figures.empty")).toBeInTheDocument();
    expect(screen.queryByText("indexPending.title")).not.toBeInTheDocument();
  });
});
