import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CATEGORY_LABELS, formatLabel } from "@/components/SideBar.utils";
import SideBar from "@/components/SideBar";
import { usePaperStore } from "@/store/usePaperStore";
import type { LibraryStats } from "@/api/types";

// ---------------------------------------------------------------------------
// 测试 1: CATEGORY_LABELS 映射表
// ---------------------------------------------------------------------------
describe("CATEGORY_LABELS 映射表", () => {
  it('all → "全部论文"', () => {
    expect(CATEGORY_LABELS.all).toBe("全部论文");
  });

  it("arxiv → 工学", () => {
    expect(CATEGORY_LABELS.arxiv).toBe("工学");
  });

  it("upload → 交叉学科", () => {
    expect(CATEGORY_LABELS.upload).toBe("交叉学科");
  });

  it("llm → 大语言模型", () => {
    expect(CATEGORY_LABELS.llm).toBe("大语言模型");
  });

  it("lora → LoRA微调", () => {
    expect(CATEGORY_LABELS.lora).toBe("LoRA微调");
  });

  it("quant → 量化计算", () => {
    expect(CATEGORY_LABELS.quant).toBe("量化计算");
  });

  it("ri → 信息检索", () => {
    expect(CATEGORY_LABELS.ri).toBe("信息检索");
  });

  it("rl → 强化学习", () => {
    expect(CATEGORY_LABELS.rl).toBe("强化学习");
  });

  it("cv → 计算机视觉", () => {
    expect(CATEGORY_LABELS.cv).toBe("计算机视觉");
  });

  it("comm → 通信工程", () => {
    expect(CATEGORY_LABELS.comm).toBe("通信工程");
  });

  it("应包含至少 10 个键（含 ri/rl 双映射）", () => {
    expect(Object.keys(CATEGORY_LABELS).length).toBeGreaterThanOrEqual(10);
  });

  it("report 不在 CATEGORY_LABELS 中（感悟报告走独立 i18n 键）", () => {
    expect(CATEGORY_LABELS.report).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 测试 2: formatLabel 函数
// ---------------------------------------------------------------------------
describe("formatLabel", () => {
  it('all → "全部论文"', () => {
    expect(formatLabel("all")).toBe("全部论文");
  });

  it("llm → 大语言模型", () => {
    expect(formatLabel("llm")).toBe("大语言模型");
  });

  it("cv → 计算机视觉", () => {
    expect(formatLabel("cv")).toBe("计算机视觉");
  });

  it("x → 退化为首字母大写", () => {
    expect(formatLabel("unknown_category")).toBe("Unknown Category");
  });

  it("空字符串 → 空字符串", () => {
    expect(formatLabel("")).toBe("");
  });

  it("下划线分隔 → 空格 + 首字母大写（fallback）", () => {
    expect(formatLabel("deep_learning")).toBe("Deep Learning");
  });

  it("中划线分隔 → 空格 + 首字母大写（fallback）", () => {
    expect(formatLabel("machine-learning")).toBe("Machine Learning");
  });
});

// ---------------------------------------------------------------------------
// 测试 3: SideBar 组件交互 — 点击分类更新 store
// ---------------------------------------------------------------------------
describe("SideBar 组件 — 点击分类", () => {
  const mockStats: LibraryStats = {
    totalPapers: 5,
    reportCount: 0,
    totalChunks: 100,
    totalSize: 50000,
    byCategory: [
      { category: "philosophy", count: 3 },
      { category: "medicine", count: 2 },
    ],
    bySource: [
      { source: "arxiv", count: 3 },
      { source: "upload", count: 2 },
    ],
  };

  beforeEach(() => {
    // 重置 store
    usePaperStore.setState({
      category: "all",
      page: 1,
      stats: mockStats,
      items: [],
      total: 5,
      loading: false,
      keyword: "",
      sort: "year_desc",
      source: "all",
      semantic: false,
    });
  });

  it('默认选中「全部论文」（category=all）', () => {
    render(<SideBar />);

    // 「全部论文」项应有 active 样式
    const allItem = screen.getByText("全部论文").closest(".pf-category-item");
    expect(allItem).toHaveClass("pf-category-active");
    // 顶部「论文」标签页指示器处于激活状态
    expect(screen.getByRole("button", { name: "home.tabs.papers" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it('点击「哲学」应调用 setCategory("philosophy")', async () => {
    const user = userEvent.setup();
    render(<SideBar />);

    const philosophyItem = screen.getByText("哲学").closest(".pf-category-item")!;
    await user.click(philosophyItem);

    expect(usePaperStore.getState().category).toBe("philosophy");
    expect(usePaperStore.getState().page).toBe(1);
  });

  it('点击「医学」应调用 setCategory("medicine")', async () => {
    const user = userEvent.setup();
    render(<SideBar />);

    const medicineItem = screen.getByText("医学").closest(".pf-category-item")!;
    await user.click(medicineItem);

    expect(usePaperStore.getState().category).toBe("medicine");
    expect(usePaperStore.getState().page).toBe(1);
  });

  it("点击「全部论文」应恢复 category=all", async () => {
    const user = userEvent.setup();
    // 先切到非 all
    usePaperStore.getState().setCategory("philosophy");
    expect(usePaperStore.getState().category).toBe("philosophy");

    render(<SideBar />);

    const allItem = screen.getByText("全部论文").closest(".pf-category-item")!;
    await user.click(allItem);

    expect(usePaperStore.getState().category).toBe("all");
  });

  it("点击后分类应高亮（pf-category-active）", async () => {
    const user = userEvent.setup();
    render(<SideBar />);

    // 初始状态：all 高亮
    expect(screen.getByText("全部论文").closest(".pf-category-item")).toHaveClass(
      "pf-category-active",
    );

    // 点击 医学
    const medicineItem = screen.getByText("医学").closest(".pf-category-item")!;
    await user.click(medicineItem);

    // 点击后只有 医学 高亮；「全部论文」不再高亮，避免两个项目同时高亮
    expect(screen.getByText("全部论文").closest(".pf-category-item")).not.toHaveClass(
      "pf-category-active",
    );
    expect(screen.getByText("医学").closest(".pf-category-item")).toHaveClass(
      "pf-category-active",
    );
  });

  it("非 report 分类下只有具体分类高亮，避免同时高亮多项", () => {
    usePaperStore.setState({ category: "philosophy" });
    render(<SideBar />);

    expect(screen.getByText("全部论文").closest(".pf-category-item")).not.toHaveClass(
      "pf-category-active",
    );
    expect(screen.getByText("哲学").closest(".pf-category-item")).toHaveClass(
      "pf-category-active",
    );
    expect(
      screen.getByText("sidebar.reflectionReports").closest(".pf-category-item"),
    ).not.toHaveClass("pf-category-active");
  });

  it("report 分类下「感悟报告」高亮，「全部论文」不高亮", () => {
    usePaperStore.setState({ category: "report" });
    render(<SideBar />);

    expect(screen.getByText("全部论文").closest(".pf-category-item")).not.toHaveClass(
      "pf-category-active",
    );
    expect(screen.getByText("sidebar.reflectionReports").closest(".pf-category-item")).toHaveClass(
      "pf-category-active",
    );
    // 顶部「感悟报告」标签页指示器处于激活状态
    expect(screen.getByRole("button", { name: "home.tabs.reports" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("动态分类中的 all 键不应与静态「全部论文」重复", () => {
    usePaperStore.setState({
      stats: {
        totalPapers: 282,
        totalChunks: 100,
        totalSize: 50000,
        byCategory: [
          { category: "all", count: 277 },
          { category: "philosophy", count: 3 },
          { category: "medicine", count: 2 },
        ],
        bySource: [],
      },
    });
    render(<SideBar />);

    // 静态首项只应出现一次，all 分类不应被渲染为动态项
    const allItems = screen.getAllByText("全部论文");
    expect(allItems.length).toBe(1);
  });

  it("应显示未分类计数（不可点击）", () => {
    usePaperStore.setState({
      stats: {
        ...mockStats,
        byCategory: [...mockStats.byCategory, { category: "", count: 7 }],
      },
    });
    render(<SideBar />);

    const uncategorizedItem = screen
      .getByText("sidebar.uncategorized")
      .closest(".pf-category-item")!;
    expect(uncategorizedItem).toBeInTheDocument();
    // 直接断言 DOM style 属性，避免 jsdom computed style 不稳定
    expect(uncategorizedItem.style.opacity).toBe("0.6");
    expect(uncategorizedItem.style.cursor).toBe("default");
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("应显示感悟报告入口并支持点击筛选", async () => {
    const user = userEvent.setup();
    usePaperStore.setState({
      stats: { ...mockStats, reportCount: 4 },
    });
    render(<SideBar />);

    const reportItem = screen.getByText("sidebar.reflectionReports").closest(".pf-category-item")!;
    expect(reportItem).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();

    await user.click(reportItem);
    expect(usePaperStore.getState().category).toBe("report");
  });

  it("动态分类中不应出现 report", () => {
    usePaperStore.setState({
      stats: {
        ...mockStats,
        byCategory: [...mockStats.byCategory, { category: "report", count: 9 }],
      },
    });
    render(<SideBar />);

    // report 只应作为独立静态项出现一次，且计数使用 reportCount 而非 byCategory
    const reportItems = screen.getAllByText("sidebar.reflectionReports");
    expect(reportItems.length).toBe(1);
  });

  it("动态分类应作为「全部论文」的子项展示（pf-category-subitem）", () => {
    render(<SideBar />);

    const philosophyItem = screen.getByText("哲学").closest(".pf-category-item");
    const medicineItem = screen.getByText("医学").closest(".pf-category-item");

    expect(philosophyItem).toHaveClass("pf-category-subitem");
    expect(medicineItem).toHaveClass("pf-category-subitem");
  });
});
