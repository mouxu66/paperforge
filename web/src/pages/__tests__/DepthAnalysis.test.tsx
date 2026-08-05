import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "@/test-utils";
import DepthAnalysis from "../DepthAnalysis";
import { fetchPapers } from "@/api/papers";
import { submitDepthBatch, pollDepthStatus } from "@/api/depth";
import { useDepthStore } from "@/store/useDepthStore";

vi.mock("@/api/papers", () => ({
  fetchPapers: vi.fn(),
}));

vi.mock("@/api/depth", () => ({
  submitDepthBatch: vi.fn(),
  pollDepthStatus: vi.fn(),
}));

vi.mock("@/components/DepthRadar", () => ({
  default: () => <div data-testid="depth-radar" />,
}));

vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  return {
    ...actual,
    message: {
      ...actual.message,
      warning: vi.fn(),
      success: vi.fn(),
      error: vi.fn(),
    },
  };
});

const mockedFetchPapers = vi.mocked(fetchPapers);
const mockedSubmit = vi.mocked(submitDepthBatch);
const mockedPoll = vi.mocked(pollDepthStatus);

const papers = [
  {
    id: "p1",
    title: "Paper One",
    authors: ["Author One"],
    year: 2025,
    abstract: "Abstract",
    category: "llm",
    tags: [],
    citations: 0,
    chunkCount: 1,
    indexSize: 10,
    pdfUrl: "",
    source: "upload",
    journal: "",
    favorited: false,
    doi: null,
    ocrStatus: null,
    isScanned: false,
  },
];

describe("DepthAnalysis — batch evaluation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useDepthStore.setState({ tasks: [] });
    mockedFetchPapers.mockResolvedValue({ items: papers as any, total: 1 });
    mockedSubmit.mockResolvedValue({ task_id: "task-1", total_papers: 1 });
    mockedPoll.mockReturnValue(new Promise(() => {}));
  });

  it("loads papers and submits the selected paper", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DepthAnalysis />
      </MemoryRouter>,
    );

    await waitFor(() => expect(mockedFetchPapers).toHaveBeenCalledWith({ page: 1, pageSize: 10 }));
    expect(screen.getByText("Paper One")).toBeInTheDocument();

    const checkbox = screen.getByRole("checkbox", { name: /Paper One/ });
    await user.click(checkbox);
    expect(screen.getByText(/已选择 1 篇论文/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "depth.startEvaluate" }));
    await waitFor(() => expect(mockedSubmit).toHaveBeenCalledWith(["p1"]));
    expect(useDepthStore.getState().tasks[0]).toMatchObject({
      taskId: "task-1",
      status: "running",
      paperIds: ["p1"],
    });
  });

  it("supports clearing the selected paper before submission", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <DepthAnalysis />
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText("Paper One")).toBeInTheDocument());
    await user.click(screen.getByRole("checkbox", { name: /Paper One/ }));
    await user.click(screen.getByRole("button", { name: "清空选择" }));

    expect(screen.getByText(/已选择 0 篇论文/)).toBeInTheDocument();
    expect(mockedSubmit).not.toHaveBeenCalled();
  });
});
