import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SelectionToolbar from "../SelectionToolbar";
import type { Paper } from "@/api/types";

const mockItems: Paper[] = [
  {
    id: "1",
    title: "Paper 1",
    authors: [],
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
  {
    id: "2",
    title: "Paper 2",
    authors: [],
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

describe("SelectionToolbar", () => {
  const renderToolbar = (props = {}) => {
    return render(
      <SelectionToolbar
        selectedIds={new Set()}
        items={mockItems}
        onToggleSelectAll={vi.fn()}
        onExitSelectMode={vi.fn()}
        onV4Review={vi.fn()}
        onDepthEval={vi.fn()}
        onBatchTag={vi.fn()}
        onDelete={vi.fn()}
        {...props}
      />,
    );
  };

  it("renders selection count", () => {
    renderToolbar({ selectedIds: new Set(["1"]) });
    expect(screen.getByText("common.selected")).toBeInTheDocument();
  });

  it("shows action buttons when items selected", () => {
    renderToolbar({ selectedIds: new Set(["1"]) });
    expect(screen.getByText(/selection\.v4Review/)).toBeInTheDocument();
    expect(screen.getByText(/selection\.depthEval/)).toBeInTheDocument();
    expect(screen.getByText(/selection\.batchTag/)).toBeInTheDocument();
    expect(screen.getByText("selection.deleteSelected")).toBeInTheDocument();
  });

  it("calls onToggleSelectAll when select all button clicked", async () => {
    const onToggleSelectAll = vi.fn();
    const user = userEvent.setup();
    renderToolbar({ onToggleSelectAll });
    await user.click(screen.getByText("selection.selectAll"));
    expect(onToggleSelectAll).toHaveBeenCalledTimes(1);
  });

  it("calls onExitSelectMode when cancel button clicked", async () => {
    const onExitSelectMode = vi.fn();
    const user = userEvent.setup();
    renderToolbar({ onExitSelectMode });
    await user.click(screen.getByText("selection.cancelSelection"));
    expect(onExitSelectMode).toHaveBeenCalledTimes(1);
  });

  it("calls onV4Review when v4 review button clicked", async () => {
    const onV4Review = vi.fn();
    const user = userEvent.setup();
    renderToolbar({ selectedIds: new Set(["1"]), onV4Review });
    await user.click(screen.getByText(/selection\.v4Review/));
    expect(onV4Review).toHaveBeenCalledTimes(1);
  });

  it("calls onDelete when delete button clicked", async () => {
    const onDelete = vi.fn();
    const user = userEvent.setup();
    renderToolbar({ selectedIds: new Set(["1"]), onDelete });
    await user.click(screen.getByText("selection.deleteSelected"));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });
});
