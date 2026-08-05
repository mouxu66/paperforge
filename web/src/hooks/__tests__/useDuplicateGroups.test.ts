import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { useDuplicateGroups, makeDefaultSelection, FIELD_KEYS } from "@/hooks/useDuplicateGroups";
import * as papersApi from "@/api/papers";
import type { DuplicateGroup } from "@/api/types";

vi.mock("antd", () => ({
  message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  },
  Modal: {
    confirm: vi.fn(),
  },
}));

function makeGroup(overrides?: Partial<DuplicateGroup>): DuplicateGroup {
  return {
    papers: [
      {
        id: "p1",
        title: "Paper One",
        authors: ["Alice"],
        year: 2023,
        abstract: "Abstract one",
        journal: "Journal A",
        pdfUrl: "",
        source: "arxiv",
        citations: 10,
        tags: ["ai"],
      },
      {
        id: "p2",
        title: "Paper Two",
        authors: ["Bob"],
        year: 2024,
        abstract: "Abstract two",
        journal: "Journal B",
        pdfUrl: "",
        source: "arxiv",
        citations: 5,
        tags: ["ml"],
      },
    ],
    ...overrides,
  } as DuplicateGroup;
}

describe("useDuplicateGroups", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(papersApi, "fetchDuplicateGroups").mockResolvedValue({ groups: [makeGroup()] });
    vi.spyOn(papersApi, "mergePapers").mockResolvedValue({
      success: true,
      target_id: "p1",
      deleted_ids: ["p2"],
      paper: null,
    });
  });

  it("loads duplicate groups on mount", async () => {
    const { result } = renderHook(() => useDuplicateGroups());

    expect(result.current.loading).toBe(true);

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(result.current.groups).toHaveLength(1);
    expect(result.current.groups[0].papers).toHaveLength(2);
  });

  it("initializes default selections with first paper as target", async () => {
    const { result } = renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const key = result.current.groups[0].papers.map((p) => p.id).join("-");
    expect(result.current.selections[key].target).toBe("p1");
    FIELD_KEYS.forEach((field) => {
      expect(result.current.selections[key].fields[field]).toBe("p1");
    });
  });

  it("updates target selection", async () => {
    const { result } = renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const key = result.current.groups[0].papers.map((p) => p.id).join("-");

    act(() => {
      result.current.setTarget(key, "p2");
    });

    expect(result.current.selections[key].target).toBe("p2");
  });

  it("updates field source selection", async () => {
    const { result } = renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const key = result.current.groups[0].papers.map((p) => p.id).join("-");

    act(() => {
      result.current.setFieldSource(key, "title", "p2");
    });

    expect(result.current.selections[key].fields.title).toBe("p2");
    expect(result.current.selections[key].fields.authors).toBe("p1");
  });

  it("calls mergePapers with correct payload on confirm", async () => {
    const { Modal } = await import("antd");
    const { result } = renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const group = result.current.groups[0];
    const key = group.papers.map((p) => p.id).join("-");

    act(() => {
      result.current.handleMerge(group, key);
    });

    expect(Modal.confirm).toHaveBeenCalled();
    const onOk = (Modal.confirm as ReturnType<typeof vi.fn>).mock.calls[0][0].onOk;

    await act(async () => {
      await onOk();
    });

    expect(papersApi.mergePapers).toHaveBeenCalledWith({
      target_id: "p1",
      source_ids: ["p2"],
      field_sources: {
        title: "p1",
        authors: "p1",
        year: "p1",
        abstract: "p1",
        journal: "p1",
        pdf_url: "p1",
        tags: "p1",
      },
    });
  });

  it("shows warning when trying to merge with no source papers", async () => {
    const { Modal } = await import("antd");
    const { message } = await import("antd");
    vi.spyOn(papersApi, "fetchDuplicateGroups").mockResolvedValue({
      groups: [
        {
          papers: [
            {
              id: "p1",
              title: "Only Paper",
              authors: ["Alice"],
              year: 2023,
              abstract: "Abstract",
              journal: "Journal A",
              pdfUrl: "",
              source: "arxiv",
              citations: 10,
              tags: ["ai"],
            },
          ],
        },
      ],
    });

    const { result } = renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const group = result.current.groups[0];
    const key = group.papers.map((p) => p.id).join("-");

    act(() => {
      result.current.handleMerge(group, key);
    });

    expect(Modal.confirm).not.toHaveBeenCalled();
    expect(message.warning).toHaveBeenCalled();
  });

  it("shows error message when fetch fails", async () => {
    const { message } = await import("antd");
    vi.spyOn(papersApi, "fetchDuplicateGroups").mockRejectedValue(new Error("Network error"));

    renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(message.error).toHaveBeenCalled();
    });
  });

  it("shows error message when merge fails", async () => {
    const { Modal } = await import("antd");
    const { message } = await import("antd");
    vi.spyOn(papersApi, "mergePapers").mockRejectedValue(new Error("Merge failed"));

    const { result } = renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const group = result.current.groups[0];
    const key = group.papers.map((p) => p.id).join("-");

    act(() => {
      result.current.handleMerge(group, key);
    });

    const onOk = (Modal.confirm as ReturnType<typeof vi.fn>).mock.calls[0][0].onOk;

    await act(async () => {
      await onOk();
    });

    await waitFor(() => {
      expect(message.error).toHaveBeenCalled();
    });
    expect(result.current.mergingKey).toBeNull();
  });

  it("sets all field sources from a single paper without changing target", async () => {
    const { result } = renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const key = result.current.groups[0].papers.map((p) => p.id).join("-");

    act(() => {
      result.current.setAllFieldsFromPaper(key, "p2");
    });

    expect(result.current.selections[key].target).toBe("p1");
    FIELD_KEYS.forEach((field) => {
      expect(result.current.selections[key].fields[field]).toBe("p2");
    });
  });

  it("refreshes duplicate groups after successful merge", async () => {
    const { Modal } = await import("antd");
    const fetchSpy = vi.spyOn(papersApi, "fetchDuplicateGroups");

    const { result } = renderHook(() => useDuplicateGroups());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    const group = result.current.groups[0];
    const key = group.papers.map((p) => p.id).join("-");

    act(() => {
      result.current.handleMerge(group, key);
    });

    const onOk = (Modal.confirm as ReturnType<typeof vi.fn>).mock.calls[0][0].onOk;

    await act(async () => {
      await onOk();
    });

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(2);
    });
  });
});

describe("makeDefaultSelection", () => {
  it("uses the first paper as the default target and field source", () => {
    const group = makeGroup();
    const sel = makeDefaultSelection(group);

    expect(sel.target).toBe("p1");
    FIELD_KEYS.forEach((field) => {
      expect(sel.fields[field]).toBe("p1");
    });
  });

  it("handles empty groups gracefully", () => {
    const group: DuplicateGroup = { papers: [] };
    const sel = makeDefaultSelection(group);

    expect(sel.target).toBe("");
    FIELD_KEYS.forEach((field) => {
      expect(sel.fields[field]).toBe("");
    });
  });
});
