import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { MemoryRouter } from "@/test-utils";
import type { LibraryStats, Paper } from "@/api/types";

import ResearchWorkbenchPanel from "../ResearchWorkbenchPanel";

const stats: LibraryStats = {
  totalPapers: 12,
  reportCount: 2,
  reportAvgScore: 0.82,
  reportAvgFidelity: 0.88,
  totalChunks: 300,
  totalSize: 1024,
  byCategory: [
    { category: "machine learning", count: 7 },
    { category: "robotics", count: 5 },
  ],
  bySource: [
    { source: "arxiv", count: 8 },
    { source: "upload", count: 4 },
  ],
};

function makePaper(overrides: Partial<Paper> = {}): Paper {
  return {
    id: "2401.12345",
    title: "A useful research direction",
    authors: ["A. Researcher"],
    year: 2024,
    abstract: "Abstract",
    category: "machine learning",
    tags: [],
    citations: 42,
    chunkCount: 10,
    indexSize: 100,
    pdfUrl: "",
    source: "arxiv",
    ...overrides,
  };
}

describe("ResearchWorkbenchPanel", () => {
  it("renders library structure, recent signals, and workflow actions", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn();

    render(
      <MemoryRouter>
        <ResearchWorkbenchPanel
          stats={stats}
          papers={[makePaper(), makePaper({ id: "2402.54321", title: "A newer paper", year: 2025, citations: 9 })]}
          onUpload={onUpload}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText("home.workbench.title")).toBeInTheDocument();
    expect(screen.getByText("machine learning")).toBeInTheDocument();
    expect(screen.getByText("arXiv · 67%")).toBeInTheDocument();
    expect(screen.getByText("A newer paper")).toBeInTheDocument();
    expect(document.querySelector(".pf-workbench-materials")).not.toBeInTheDocument();
    expect(document.querySelectorAll(".pf-workbench-card")).toHaveLength(3);

    await user.click(screen.getByRole("button", { name: /home.workbench.uploadTitle/ }));
    expect(onUpload).toHaveBeenCalledOnce();
  });

  it("shows an actionable empty state when the library has no signals", async () => {
    const user = userEvent.setup();
    const onUpload = vi.fn();

    render(
      <MemoryRouter>
        <ResearchWorkbenchPanel stats={null} papers={[]} onUpload={onUpload} />
      </MemoryRouter>,
    );

    expect(screen.getByText("home.workbench.noStructure")).toBeInTheDocument();
    expect(screen.getByText("home.workbench.noSignals")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "home.workbench.startImport" }));
    expect(onUpload).toHaveBeenCalledOnce();
  });
});
