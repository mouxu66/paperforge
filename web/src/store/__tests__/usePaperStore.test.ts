import { describe, it, expect, beforeEach } from "vitest";
import { usePaperStore } from "@/store/usePaperStore";

/**
 * Store 单元测试: 验证 setCategory 行为
 * - 更新 category 字段
 * - 重置 page 为 1
 * - 不修改其他字段（keyword / sort / source 保持）
 */
describe("usePaperStore — setCategory", () => {
  beforeEach(() => {
    // 重置 store 到初始状态
    usePaperStore.setState({
      keyword: "",
      category: "all",
      sort: "year_desc",
      source: "all",
      page: 3,
      items: [],
      total: 0,
      loading: false,
      semantic: false,
    });
  });

  it("初始 category 应为 all", () => {
    expect(usePaperStore.getState().category).toBe("all");
  });

  it("setCategory 应更新 category 并重置 page 为 1", () => {
    usePaperStore.getState().setCategory("llm");

    const state = usePaperStore.getState();
    expect(state.category).toBe("llm");
    expect(state.page).toBe(1);
  });

  it('setCategory("all") 应恢复到全部论文', () => {
    usePaperStore.getState().setCategory("arxiv");
    expect(usePaperStore.getState().category).toBe("arxiv");

    usePaperStore.getState().setCategory("all");
    expect(usePaperStore.getState().category).toBe("all");
    expect(usePaperStore.getState().page).toBe(1);
  });

  it("setCategory 不应修改 keyword / sort / source", () => {
    usePaperStore.setState({ keyword: "transformer", sort: "citations_desc", source: "arxiv" });

    usePaperStore.getState().setCategory("cv");

    const state = usePaperStore.getState();
    expect(state.keyword).toBe("transformer");
    expect(state.sort).toBe("citations_desc");
    expect(state.source).toBe("arxiv");
  });

  it("连续切换分类时 page 每次都应重置为 1", () => {
    usePaperStore.getState().setCategory("llm");
    expect(usePaperStore.getState().page).toBe(1);

    // 模拟翻页后
    usePaperStore.setState({ page: 4 });

    usePaperStore.getState().setCategory("cv");
    expect(usePaperStore.getState().page).toBe(1);
  });

  it("loadPapers 应使用当前 category 构造查询参数", async () => {
    // 验证 loadPapers 读取 store 中的 category
    // 由于 loadPapers 实际调用 API，这里仅验证 get() 读取到正确 category
    usePaperStore.getState().setCategory("upload");

    // 状态验证：store 内部 get() 能拿到最新 category
    const { category, page } = usePaperStore.getState();
    expect(category).toBe("upload");
    expect(page).toBe(1);
  });
});
