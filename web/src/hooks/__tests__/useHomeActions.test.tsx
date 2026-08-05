import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { MemoryRouter } from "@/test-utils";

import { useHomeActions } from "../useHomeActions";
import type { useSelection } from "../useSelection";

// --- Mocks -------------------------------------------------------------

// vi.mock is hoisted above top-level consts, so spies referenced inside the
// factory MUST be created with vi.hoisted() (otherwise ReferenceError at hoist time).
const { mockNavigate, messageSpy } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  messageSpy: {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
  },
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

vi.mock("antd", () => ({ message: messageSpy }));

vi.mock("@/api/papers", () => ({ batchDeletePapers: vi.fn() }));
vi.mock("@/api/depth", () => ({
  submitDepthBatch: vi.fn(),
  startSelectedV4Review: vi.fn(),
}));

import { batchDeletePapers } from "@/api/papers";
import { submitDepthBatch, startSelectedV4Review } from "@/api/depth";

// --- Helpers -----------------------------------------------------------

function makeSelection(ids: string[]): ReturnType<typeof useSelection> {
  return {
    selectMode: true,
    selectedIds: new Set(ids),
    selectedPapers: [],
    estimatedMinutes: 1,
    isAllSelected: false,
    toggleSelect: vi.fn(),
    toggleSelectAll: vi.fn(),
    exitSelectMode: vi.fn(),
    toggleSelectMode: vi.fn(),
  } as unknown as ReturnType<typeof useSelection>;
}

function renderActions(
  selection: ReturnType<typeof useSelection>,
  addTask: ReturnType<typeof vi.fn> = vi.fn(),
  loadPapers: ReturnType<typeof vi.fn> = vi.fn(),
) {
  return renderHook(() => useHomeActions(selection, addTask, loadPapers), {
    wrapper: ({ children }) => <MemoryRouter>{children}</MemoryRouter>,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  (batchDeletePapers as ReturnType<typeof vi.fn>).mockReset();
  (submitDepthBatch as ReturnType<typeof vi.fn>).mockReset();
  (startSelectedV4Review as ReturnType<typeof vi.fn>).mockReset();
});

// --- Tests -------------------------------------------------------------

describe("useHomeActions · handleBatchDelete", () => {
  it("全成功 → success 消息 + 退出选择 + 刷新列表", async () => {
    (batchDeletePapers as ReturnType<typeof vi.fn>).mockResolvedValue({
      deleted_count: 3,
      failed_ids: [],
    });
    const exitSelectMode = vi.fn();
    const loadPapers = vi.fn().mockResolvedValue(undefined);
    const sel = { ...makeSelection(["a", "b", "c"]), exitSelectMode };
    const { result } = renderActions(sel, vi.fn(), loadPapers);

    await act(async () => {
      await result.current.handleBatchDelete();
    });

    expect(messageSpy.success).toHaveBeenCalledOnce();
    expect(messageSpy.warning).not.toHaveBeenCalled();
    expect(exitSelectMode).toHaveBeenCalledOnce();
    expect(loadPapers).toHaveBeenCalledOnce();
  });

  it("部分失败 → warning 消息", async () => {
    (batchDeletePapers as ReturnType<typeof vi.fn>).mockResolvedValue({
      deleted_count: 2,
      failed_ids: ["x"],
    });
    const { result } = renderActions(makeSelection(["a", "b", "x"]));

    await act(async () => {
      await result.current.handleBatchDelete();
    });

    expect(messageSpy.warning).toHaveBeenCalledOnce();
    expect(messageSpy.success).not.toHaveBeenCalled();
  });

  it("抛错 → error 消息 + loading 复位", async () => {
    (batchDeletePapers as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("boom"));
    const { result } = renderActions(makeSelection(["a"]));

    await act(async () => {
      await result.current.handleBatchDelete();
    });

    expect(messageSpy.error).toHaveBeenCalledOnce();
    expect(result.current.deleteLoading).toBe(false);
  });

  it("空选择 → 提前返回，不调 API", async () => {
    const { result } = renderActions(makeSelection([]));
    await act(async () => {
      await result.current.handleBatchDelete();
    });
    expect(batchDeletePapers).not.toHaveBeenCalled();
  });
});

describe("useHomeActions · handleDepthSubmit", () => {
  it("成功 → addTask + success + 跳转 /depth", async () => {
    (submitDepthBatch as ReturnType<typeof vi.fn>).mockResolvedValue({
      task_id: "t1",
      total_papers: 2,
    });
    const addTask = vi.fn();
    const { result } = renderActions(makeSelection(["a", "b"]), addTask);

    await act(async () => {
      await result.current.handleDepthSubmit();
    });

    expect(addTask).toHaveBeenCalledWith("t1", 2, ["a", "b"]);
    expect(messageSpy.success).toHaveBeenCalledOnce();
    expect(mockNavigate).toHaveBeenCalledWith("/depth", {
      state: { taskId: "t1", selectedIds: ["a", "b"] },
    });
  });

  it("抛错 → error 消息（用 Error.message）", async () => {
    (submitDepthBatch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("depth fail"));
    const { result } = renderActions(makeSelection(["a"]));

    await act(async () => {
      await result.current.handleDepthSubmit();
    });

    expect(messageSpy.error).toHaveBeenCalledWith("depth fail");
  });

  it("空选择 → 提前返回", async () => {
    const { result } = renderActions(makeSelection([]));
    await act(async () => {
      await result.current.handleDepthSubmit();
    });
    expect(submitDepthBatch).not.toHaveBeenCalled();
  });
});

describe("useHomeActions · handleV4Review", () => {
  it("无跳过 → success 消息", async () => {
    (startSelectedV4Review as ReturnType<typeof vi.fn>).mockResolvedValue({
      submitted: 2,
      skipped: [],
    });
    const exitSelectMode = vi.fn();
    const sel = { ...makeSelection(["a", "b"]), exitSelectMode };
    const { result } = renderActions(sel);

    await act(async () => {
      await result.current.handleV4Review();
    });

    expect(messageSpy.success).toHaveBeenCalledOnce();
    expect(exitSelectMode).toHaveBeenCalledOnce();
  });

  it("有跳过 → warning 消息（含原因截断）", async () => {
    (startSelectedV4Review as ReturnType<typeof vi.fn>).mockResolvedValue({
      submitted: 1,
      skipped: [{ reason: "无全文" }, { reason: "重复" }, { reason: "x" }, { reason: "y" }],
    });
    const { result } = renderActions(makeSelection(["a", "b", "c", "d", "e"]));

    await act(async () => {
      await result.current.handleV4Review();
    });

    expect(messageSpy.warning).toHaveBeenCalledOnce();
    // warning 的第二个参数是 duration=5
    expect(messageSpy.warning.mock.calls[0][1]).toBe(5);
  });

  it("抛带 response.detail 的错 → error 用 detail", async () => {
    (startSelectedV4Review as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { data: { detail: "服务端拒绝" } },
    });
    const { result } = renderActions(makeSelection(["a"]));

    await act(async () => {
      await result.current.handleV4Review();
    });

    expect(messageSpy.error).toHaveBeenCalledWith("服务端拒绝");
  });

  it("空选择 → 提前返回", async () => {
    const { result } = renderActions(makeSelection([]));
    await act(async () => {
      await result.current.handleV4Review();
    });
    expect(startSelectedV4Review).not.toHaveBeenCalled();
  });
});

describe("useHomeActions · loading 状态", () => {
  it("delete 期间 deleteLoading=true", async () => {
    let resolveDelete: (v: { deleted_count: number; failed_ids: string[] }) => void = () => {};
    (batchDeletePapers as ReturnType<typeof vi.fn>).mockReturnValue(
      new Promise((r) => {
        resolveDelete = r;
      }),
    );
    const { result } = renderActions(makeSelection(["a"]));

    let p: Promise<void> | undefined;
    act(() => {
      p = result.current.handleBatchDelete();
    });
    await waitFor(() => expect(result.current.deleteLoading).toBe(true));
    await act(async () => {
      resolveDelete({ deleted_count: 1, failed_ids: [] });
      await p;
    });
    expect(result.current.deleteLoading).toBe(false);
  });
});
