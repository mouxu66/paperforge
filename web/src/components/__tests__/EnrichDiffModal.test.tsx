import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EnrichDiffModal from "@/components/EnrichDiffModal";
import type { Paper } from "@/api/types";

function makePaper(overrides?: Partial<Paper>): Paper {
  return {
    id: "p1",
    title: "Original Title",
    authors: ["Alice", "Bob"],
    year: 2023,
    abstract: "Original abstract.",
    journal: "",
    citations: 10,
    influentialCitations: 2,
    fieldsOfStudy: ["cs.AI"],
    source: "arxiv",
    category: "arxiv",
    tags: ["ai"],
    pdfUrl: "",
    chunkCount: 0,
    indexSize: 0,
    ocrStatus: "done",
    isScanned: false,
    createdAt: "2024-01-01T00:00:00Z",
    updatedAt: "2024-01-01T00:00:00Z",
    ...overrides,
  } as Paper;
}

describe("EnrichDiffModal", () => {
  it("renders empty state when no changes", () => {
    render(
      <EnrichDiffModal
        open
        original={makePaper()}
        enriched={makePaper()}
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("paper.enrichMetadataTitle")).toBeInTheDocument();
    expect(screen.getByText("paper.noChangesFound")).toBeInTheDocument();
    // Accept button disabled when no changes
    expect(screen.getByRole("button", { name: /paper.acceptChanges/i })).toBeDisabled();
  });

  it("renders changed fields with old/new values", () => {
    const original = makePaper({ title: "Old Title", year: 2023 });
    const enriched = makePaper({ title: "New Title", year: 2024, citations: 20 });

    render(
      <EnrichDiffModal
        open
        original={original}
        enriched={enriched}
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("New Title")).toBeInTheDocument();
    expect(screen.getByText("2024")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
    // Old values rendered with strikethrough
    expect(screen.getByText("Old Title")).toBeInTheDocument();
    expect(screen.getByText("2023")).toBeInTheDocument();
  });

  it("calls onAccept when accept button clicked", async () => {
    const onAccept = vi.fn();
    const onCancel = vi.fn();
    const original = makePaper({ title: "Old" });
    const enriched = makePaper({ title: "New" });

    render(
      <EnrichDiffModal
        open
        original={original}
        enriched={enriched}
        onAccept={onAccept}
        onCancel={onCancel}
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /paper.acceptChanges/i }));
    expect(onAccept).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("calls onCancel when cancel button clicked", async () => {
    const onAccept = vi.fn();
    const onCancel = vi.fn();
    const original = makePaper({ title: "Old" });
    const enriched = makePaper({ title: "New" });

    render(
      <EnrichDiffModal
        open
        original={original}
        enriched={enriched}
        onAccept={onAccept}
        onCancel={onCancel}
      />,
    );

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /common.cancel/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onAccept).not.toHaveBeenCalled();
  });

  it("shows loading state on accept button", () => {
    const original = makePaper({ title: "Old" });
    const enriched = makePaper({ title: "New" });

    render(
      <EnrichDiffModal
        open
        original={original}
        enriched={enriched}
        loading
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const acceptBtn = screen.getByRole("button", { name: /paper.acceptChanges/i });
    expect(acceptBtn).toHaveClass("ant-btn-loading");
  });

  it("renders array fields as joined strings", () => {
    const original = makePaper({ authors: ["Alice"] });
    const enriched = makePaper({ authors: ["Alice", "Charlie"] });

    render(
      <EnrichDiffModal
        open
        original={original}
        enriched={enriched}
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("Alice, Charlie")).toBeInTheDocument();
    expect(screen.getByText("Alice")).toBeInTheDocument();
  });

  it("does not render when closed", () => {
    const original = makePaper({ title: "Old" });
    const enriched = makePaper({ title: "New" });

    render(
      <EnrichDiffModal
        open={false}
        original={original}
        enriched={enriched}
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.queryByText("paper.enrichMetadataTitle")).not.toBeInTheDocument();
  });

  it("renders empty state when original is null", () => {
    render(
      <EnrichDiffModal
        open
        original={null}
        enriched={makePaper()}
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("paper.noChangesFound")).toBeInTheDocument();
  });

  it("renders Updated tag for changed rows", () => {
    const original = makePaper({ title: "Old" });
    const enriched = makePaper({ title: "New" });

    render(
      <EnrichDiffModal
        open
        original={original}
        enriched={enriched}
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    const updatedTag = screen.getByText("common.updated");
    expect(updatedTag).toBeInTheDocument();
    // The tag should be in the same row as the new value
    expect(updatedTag.closest("tr")).toHaveTextContent("New");
  });

  it("filters out unchanged fields", () => {
    const original = makePaper({ title: "Same", year: 2023 });
    const enriched = makePaper({ title: "Same", year: 2024 });

    render(
      <EnrichDiffModal
        open
        original={original}
        enriched={enriched}
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    // Only year changed; title should not appear
    expect(screen.queryByText("Same")).not.toBeInTheDocument();
    expect(screen.getByText("2024")).toBeInTheDocument();
  });

  it("renders multiple changed fields in correct rows", () => {
    const original = makePaper({ title: "Old Title", year: 2023, citations: 10 });
    const enriched = makePaper({ title: "New Title", year: 2024, citations: 10 });

    render(
      <EnrichDiffModal
        open
        original={original}
        enriched={enriched}
        onAccept={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    // title and year changed; citations unchanged so filtered out
    const titleRow = screen.getByText("New Title").closest("tr");
    expect(titleRow).toHaveTextContent("Old Title");
    expect(titleRow).toHaveTextContent("New Title");

    const yearRow = screen.getByText("2024").closest("tr");
    expect(yearRow).toHaveTextContent("2023");
    expect(yearRow).toHaveTextContent("2024");

    // citations should not appear
    expect(screen.queryByText("10")).not.toBeInTheDocument();
  });
});
