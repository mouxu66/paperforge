import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "@/test-utils";
import V4ReviewPanel from "../V4ReviewPanel";
import * as depthApi from "@/api/depth";

const mockReviews = [
  {
    id: "review-1",
    paper_id: "paper-1",
    paper_title: "Test Paper",
    status: "completed",
    novelty_score: 0.75,
    hotspot_alignment_score: 0.6,
    core_contribution: "A novel approach",
    final_verdict: "accept",
    created_at: "2024-01-01T00:00:00Z",
    completed_at: "2024-01-01T01:00:00Z",
  },
];

vi.mock("@/api/depth", () => ({
  listDepthV4Reviews: vi.fn(),
  startBatchV4Review: vi.fn(),
}));

vi.mock("@/store/useTaskStore", () => ({
  useTaskStore: vi.fn(() => ({ subscribeSSE: vi.fn(() => vi.fn()) })),
}));

describe("V4ReviewPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (depthApi.listDepthV4Reviews as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: mockReviews,
      total: 1,
      limit: 5,
      offset: 0,
    });
  });

  const renderPanel = () => {
    return render(
      <MemoryRouter>
        <V4ReviewPanel />
      </MemoryRouter>,
    );
  };

  it("renders review list after loading", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("Test Paper")).toBeInTheDocument();
    });
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("Accept")).toBeInTheDocument();
  });

  it("renders empty state when no reviews", async () => {
    (depthApi.listDepthV4Reviews as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
      limit: 5,
      offset: 0,
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/暂无审稿记录/)).toBeInTheDocument();
    });
  });

  it("calls startBatchV4Review when batch review button clicked", async () => {
    const user = userEvent.setup();
    (depthApi.startBatchV4Review as ReturnType<typeof vi.fn>).mockResolvedValue({
      task_id: null,
      message: "没有待审稿论文",
      total: 0,
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText("Test Paper")).toBeInTheDocument();
    });
    await user.click(screen.getByText("批量审稿"));
    await waitFor(() => {
      expect(depthApi.startBatchV4Review).toHaveBeenCalledTimes(1);
    });
  });
});
