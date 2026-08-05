import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "@/test-utils";
import HomePage from "../HomePage";
import { usePaperStore } from "@/store/usePaperStore";
import { useDepthStore } from "@/store/useDepthStore";

// ---------------------------------------------------------------------------
// Mocks — heavy children stubbed for fast/cheap render; HomeToolbar +
// SelectionToolbar kept real so their buttons drive HomePage state.
// ---------------------------------------------------------------------------

vi.mock("@/api/papers", () => ({
  batchDeletePapers: vi.fn(),
  fetchPapers: vi.fn(),
  fetchStats: vi.fn(),
}));

vi.mock("@/api/depth", () => ({
  submitDepthBatch: vi.fn(),
  startSelectedV4Review: vi.fn(),
  getDepthScoresByPaperIds: vi.fn().mockResolvedValue({}),
}));

vi.mock("@/components/StatCards", () => ({
  default: () => <div data-testid="stat-cards" />,
}));

vi.mock("@/components/PaperList", () => ({
  default: (props: { selectedIds?: Set<string>; onToggleSelect?: (id: string) => void }) => (
    <div data-testid="paper-list">
      <span data-testid="paper-list-selected-count">{props.selectedIds?.size ?? 0}</span>
      <button
        data-testid="paper-list-toggle-select"
        onClick={() => props.onToggleSelect?.("p1")}
        disabled={!props.onToggleSelect}
      >
        toggle p1
      </button>
    </div>
  ),
}));

vi.mock("@/components/SearchBar", () => ({
  default: () => <div data-testid="search-bar" />,
}));

vi.mock("@/components/UploadPaper", () => ({
  default: () => <div id="upload-paper-anchor" data-testid="upload-paper" />,
}));

vi.mock("@/components/ArxivImport", () => ({
  default: () => <div data-testid="arxiv-import" />,
}));

vi.mock("@/components/SourceFilter", () => ({
  default: () => <div data-testid="source-filter" />,
}));

vi.mock("@/components/home/V4ReviewPanel", () => ({
  default: () => <div data-testid="v4-review-panel" />,
}));

vi.mock("@/components/ReflectionUpload", () => ({
  default: ({ open }: { open: boolean }) => (
    <div data-testid="reflection-upload" data-open={open} />
  ),
}));

vi.mock("@/components/TagManager", () => ({
  BatchTagModal: ({ open }: { open: boolean }) => (
    <div data-testid="batch-tag-modal" data-open={open} />
  ),
  TagManagerModal: ({ open }: { open: boolean }) => (
    <div data-testid="tag-manager-modal" data-open={open} />
  ),
}));

vi.mock("@/components/home/DepthEvalModal", () => ({
  default: ({ open }: { open: boolean }) => <div data-testid="depth-eval-modal" data-open={open} />,
}));

vi.mock("@/components/home/DeleteConfirmModal", () => ({
  default: ({ open }: { open: boolean }) => (
    <div data-testid="delete-confirm-modal" data-open={open} />
  ),
}));

// HomeToolbar + SelectionToolbar + HomeHeader kept real to drive interactions.
// antd message is a singleton that calls into the DOM; stub it to no-op.
vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    message: {
      success: vi.fn(),
      warning: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
      loading: vi.fn(),
    },
  };
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderHomePage() {
  return render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );
}

/** 匹配 selected-count 标签文本。
 * 当前测试环境 i18n 返回 key，因此直接匹配 "common.selected"。 */
function getSelectedLabel() {
  return screen.getByText((_, node) => {
    if (!node) return false;
    return node.textContent?.replace(/\s+/g, " ").trim() === "common.selected";
  });
}

const mockPapers = [
  { id: "p1", title: "Paper 1", authors: ["A"], year: 2024 },
  { id: "p2", title: "Paper 2", authors: ["B"], year: 2023 },
  { id: "p3", title: "Paper 3", authors: ["C"], year: 2022 },
];

beforeEach(() => {
  vi.clearAllMocks();
  // SideBar-style: directly seed the zustand store rather than mock the hook.
  usePaperStore.setState({
    items: mockPapers as any,
    total: mockPapers.length,
    loading: false,
    keyword: "",
    category: "all",
    sort: "year_desc",
    source: "all",
    page: 1,
    pageSize: 8,
    semantic: false,
    stats: {
      totalPapers: mockPapers.length,
      totalChunks: 100,
      totalSize: 50000,
      byCategory: [],
      bySource: [],
    },
    statsLoading: false,
    ranking: null,
    rankingLoading: false,
    loadPapers: vi.fn(),
    loadStats: vi.fn(),
    loadRanking: vi.fn(),
    clearRanking: vi.fn(),
    setKeyword: vi.fn((v: string) => usePaperStore.setState({ keyword: v, page: 1 })),
    setCategory: vi.fn(),
    setSort: vi.fn(),
    setSource: vi.fn(),
    setCategoryAndSource: vi.fn(),
    setPage: vi.fn(),
    setSemantic: vi.fn(),
  });
  useDepthStore.setState({
    tasks: [],
    addTask: vi.fn(),
    removeTask: vi.fn(),
    updateTask: vi.fn(),
  } as any);
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("HomePage — smoke test（拆分安全网）", () => {
  it("renders all key sections without crashing", () => {
    renderHomePage();
    // HomeHeader (real) → title + subtitle
    expect(screen.getByText("home.title")).toBeInTheDocument();
    expect(screen.getByText("home.subtitle")).toBeInTheDocument();
    // stubbed children
    expect(screen.getByTestId("stat-cards")).toBeInTheDocument();
    // 高级审稿面板采用按需挂载：展开后才启动其轮询和数据请求。
    expect(screen.queryByTestId("v4-review-panel")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("home.advancedToolsTitle"));
    expect(screen.getByTestId("v4-review-panel")).toBeInTheDocument();
    expect(screen.getByTestId("search-bar")).toBeInTheDocument();
    expect(screen.getByTestId("source-filter")).toBeInTheDocument();
    expect(screen.getByTestId("arxiv-import")).toBeInTheDocument();
    expect(screen.getByTestId("upload-paper")).toBeInTheDocument();
    expect(screen.getByTestId("paper-list")).toBeInTheDocument();
  });

  it("首次展开高级工具后折叠再展开，审稿面板仍保持挂载", () => {
    renderHomePage();

    const advancedLabel = screen.getByText("home.advancedToolsTitle");
    expect(screen.queryByTestId("v4-review-panel")).not.toBeInTheDocument();

    fireEvent.click(advancedLabel);
    expect(screen.getByTestId("v4-review-panel")).toBeInTheDocument();

    fireEvent.click(advancedLabel);
    fireEvent.click(advancedLabel);
    expect(screen.getByTestId("v4-review-panel")).toBeInTheDocument();
  });

  it("select mode 初始关闭：不显示 SelectionToolbar", () => {
    renderHomePage();
    // SelectionToolbar 未渲染时不会出现「全选」按钮
    expect(screen.queryByText("selection.selectAll")).not.toBeInTheDocument();
    expect(screen.queryByText("selection.cancelSelection")).not.toBeInTheDocument();
  });

  it("点击「选择论文」→ 进入选择模式，显示 SelectionToolbar + 全选按钮", () => {
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    // SelectionToolbar 出现
    expect(screen.getByText("selection.selectAll")).toBeInTheDocument();
    expect(getSelectedLabel()).toBeInTheDocument();
    // HomeToolbar 按钮文案切到「退出选择」
    expect(screen.getByText("home.toolbar.exitSelection")).toBeInTheDocument();
  });

  it("选择模式下点击「取消选择」→ 退出选择模式，SelectionToolbar 消失", () => {
    renderHomePage();
    // 进入
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    expect(screen.getByText("selection.selectAll")).toBeInTheDocument();
    // 退出
    fireEvent.click(screen.getByText("selection.cancelSelection"));
    expect(screen.queryByText("selection.selectAll")).not.toBeInTheDocument();
    expect(screen.queryByText("selection.cancelSelection")).not.toBeInTheDocument();
    // HomeToolbar 按钮切回「选择论文」
    expect(screen.getByText("home.toolbar.selectPapers")).toBeInTheDocument();
  });

  it("选择模式下点击「退出选择」也能退出", () => {
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    expect(screen.getByText("selection.selectAll")).toBeInTheDocument();
    // HomeToolbar 的「退出选择」按钮（primary 样式）
    fireEvent.click(screen.getByText("home.toolbar.exitSelection"));
    expect(screen.queryByText("selection.selectAll")).not.toBeInTheDocument();
  });

  it("选择模式下点「全选」→ 选中当前页全部 3 篇；再点「取消全选」→ 清空", () => {
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    // 全选
    fireEvent.click(screen.getByText("selection.selectAll"));
    expect(getSelectedLabel()).toBeInTheDocument();
    expect(screen.getByText("selection.deselectAll")).toBeInTheDocument();
    // PaperList 收到 selectedIds（stub 显示 count）
    expect(screen.getByTestId("paper-list-selected-count").textContent).toBe("3");
    // 取消全选
    fireEvent.click(screen.getByText("selection.deselectAll"));
    expect(getSelectedLabel()).toBeInTheDocument();
    expect(screen.getByTestId("paper-list-selected-count").textContent).toBe("0");
  });

  it("选中 1 篇后显示 V4/DEPTH/批量打标签/删除按钮；未选中时不显示", () => {
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    // 未选中时：4 个动作按钮都不出现
    expect(screen.queryByText("selection.v4Review")).not.toBeInTheDocument();
    expect(screen.queryByText("selection.depthEval")).not.toBeInTheDocument();
    expect(screen.queryByText("selection.batchTag")).not.toBeInTheDocument();
    expect(screen.queryByText("selection.deleteSelected")).not.toBeInTheDocument();
    // 通过 PaperList stub 的 toggle 按钮选中 1 篇
    fireEvent.click(screen.getByTestId("paper-list-toggle-select"));
    expect(getSelectedLabel()).toBeInTheDocument();
    // 4 个动作按钮出现
    expect(screen.getByText("selection.v4Review")).toBeInTheDocument();
    expect(screen.getByText("selection.depthEval")).toBeInTheDocument();
    expect(screen.getByText("selection.batchTag")).toBeInTheDocument();
    expect(screen.getByText("selection.deleteSelected")).toBeInTheDocument();
  });

  it("选中后点「批量打标签」→ 打开 BatchTagModal", () => {
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    fireEvent.click(screen.getByTestId("paper-list-toggle-select"));
    expect(screen.getByTestId("batch-tag-modal").getAttribute("data-open")).toBe("false");
    fireEvent.click(screen.getByText("selection.batchTag"));
    expect(screen.getByTestId("batch-tag-modal").getAttribute("data-open")).toBe("true");
  });

  it("选中后点「删除选中」→ 打开 DeleteConfirmModal", () => {
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    fireEvent.click(screen.getByTestId("paper-list-toggle-select"));
    // modal 初始关闭
    expect(screen.getByTestId("delete-confirm-modal").getAttribute("data-open")).toBe("false");
    fireEvent.click(screen.getByText("selection.deleteSelected"));
    expect(screen.getByTestId("delete-confirm-modal").getAttribute("data-open")).toBe("true");
  });

  it("选中后点「DEPTH 评估」→ 打开 DepthEvalModal", () => {
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    fireEvent.click(screen.getByTestId("paper-list-toggle-select"));
    expect(screen.getByTestId("depth-eval-modal").getAttribute("data-open")).toBe("false");
    fireEvent.click(screen.getByText("selection.depthEval"));
    expect(screen.getByTestId("depth-eval-modal").getAttribute("data-open")).toBe("true");
  });

  it("点「标签管理」→ 打开 TagManagerModal", () => {
    renderHomePage();
    expect(screen.getByTestId("tag-manager-modal").getAttribute("data-open")).toBe("false");
    fireEvent.click(screen.getByText("tag.manager.button"));
    expect(screen.getByTestId("tag-manager-modal").getAttribute("data-open")).toBe("true");
  });

  it("点「提交感悟」→ 打开 ReflectionUpload", () => {
    renderHomePage();
    expect(screen.getByTestId("reflection-upload").getAttribute("data-open")).toBe("false");
    fireEvent.click(screen.getByText("reflection.upload.quickAction"));
    expect(screen.getByTestId("reflection-upload").getAttribute("data-open")).toBe("true");
  });

  it("loadPapers 在挂载时被调用（初始化数据获取）", () => {
    const loadPapersSpy = usePaperStore.getState().loadPapers as ReturnType<typeof vi.fn>;
    renderHomePage();
    expect(loadPapersSpy).toHaveBeenCalled();
  });

  it("选中后切换出选择模式 → selectedIds 清空（PaperList count 归零）", async () => {
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectPapers"));
    fireEvent.click(screen.getByTestId("paper-list-toggle-select"));
    expect(screen.getByTestId("paper-list-selected-count").textContent).toBe("1");
    // 退出
    fireEvent.click(screen.getByText("selection.cancelSelection"));
    await waitFor(() => {
      // 退出选择模式后 PaperList 的 selectedIds 变 undefined → count 0
      expect(screen.getByTestId("paper-list-selected-count").textContent).toBe("0");
    });
  });

  it("渲染论文/感悟报告顶部标签页", () => {
    renderHomePage();
    expect(screen.getByRole("tab", { name: "home.tabs.papers" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "home.tabs.reports" })).toBeInTheDocument();
  });

  it("感悟报告视图下隐藏论文专属 UI", () => {
    usePaperStore.setState({ category: "report" });
    renderHomePage();
    expect(screen.getByTestId("stat-cards")).toBeInTheDocument();
    expect(screen.queryByTestId("source-filter")).not.toBeInTheDocument();
    expect(screen.queryByTestId("arxiv-import")).not.toBeInTheDocument();
    expect(screen.queryByTestId("upload-paper")).not.toBeInTheDocument();
    expect(screen.getByTestId("search-bar")).toBeInTheDocument();
    expect(screen.getByTestId("paper-list")).toBeInTheDocument();
  });

  it("点击「感悟报告」标签设置 category 为 report 并重置 source", () => {
    const setCategoryAndSourceSpy = usePaperStore.getState().setCategoryAndSource as ReturnType<
      typeof vi.fn
    >;
    renderHomePage();
    fireEvent.click(screen.getByRole("tab", { name: "home.tabs.reports" }));
    expect(setCategoryAndSourceSpy).toHaveBeenCalledWith("report", "all");
  });

  it("报告视图下从驾驶舱点击开始导入会切回论文视图", () => {
    usePaperStore.setState({ category: "report", items: [] });
    const setCategoryAndSourceSpy = usePaperStore.getState().setCategoryAndSource as ReturnType<typeof vi.fn>;
    renderHomePage();

    fireEvent.click(screen.getByRole("button", { name: "home.workbench.startImport" }));

    expect(setCategoryAndSourceSpy).toHaveBeenCalledWith("all", "all");
  });

  it("感悟报告视图下选择按钮文案变为选择报告", () => {
    usePaperStore.setState({ category: "report" });
    renderHomePage();
    expect(screen.getByText("home.toolbar.selectReports")).toBeInTheDocument();
  });

  it("感悟报告视图下隐藏 V4/DEPTH 等论文专属批量操作", () => {
    usePaperStore.setState({ category: "report" });
    renderHomePage();
    fireEvent.click(screen.getByText("home.toolbar.selectReports"));
    fireEvent.click(screen.getByTestId("paper-list-toggle-select"));
    expect(screen.queryByText("selection.v4Review")).not.toBeInTheDocument();
    expect(screen.queryByText("selection.depthEval")).not.toBeInTheDocument();
    // 批量打标签/删除仍保留
    expect(screen.getByText("selection.batchTag")).toBeInTheDocument();
    expect(screen.getByText("selection.deleteSelected")).toBeInTheDocument();
  });
});
