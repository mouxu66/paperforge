import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DepthEvalModal from "../DepthEvalModal";
import type { Paper } from "@/api/types";

const mockPapers: Paper[] = [
  {
    id: "1",
    title: "Paper 1",
    authors: ["Alice Smith"],
    year: 2024,
    abstract: "",
    category: "",
    tags: [],
    citations: 0,
    chunkCount: 0,
    indexSize: 0,
    pdfUrl: "",
    source: "arxiv",
  },
];

describe("DepthEvalModal", () => {
  const renderModal = (props = {}) => {
    return render(
      <DepthEvalModal
        open={true}
        onCancel={vi.fn()}
        onOk={vi.fn()}
        confirmLoading={false}
        selectedPapers={mockPapers}
        estimatedMinutes={2}
        {...props}
      />,
    );
  };

  it("renders selected paper title", () => {
    renderModal();
    expect(screen.getByText("Paper 1")).toBeInTheDocument();
  });

  it("renders estimated minutes", () => {
    renderModal();
    expect(screen.getByText(/约 2 分钟/)).toBeInTheDocument();
  });

  it("calls onOk when confirm button clicked", async () => {
    const onOk = vi.fn();
    const user = userEvent.setup();
    renderModal({ onOk });
    const okButton = await screen.findByRole("button", { name: /确认提交/ });
    await user.click(okButton);
    expect(onOk).toHaveBeenCalledTimes(1);
  });

  it("calls onCancel when cancel button clicked", async () => {
    const onCancel = vi.fn();
    const user = userEvent.setup();
    renderModal({ onCancel });
    const cancelButton = await screen.findByRole("button", { name: /取\s*消/ });
    await user.click(cancelButton);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});
