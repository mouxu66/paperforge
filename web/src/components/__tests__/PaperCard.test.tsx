import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PaperCard from "@/components/PaperCard";
import type { Paper } from "@/api/types";

const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function makePaper(overrides?: Partial<Paper>): Paper {
  return {
    id: "p1",
    title: "Test Paper",
    authors: ["Alice", "Bob"],
    year: 2024,
    abstract: "Abstract text.",
    journal: "",
    citations: 10,
    influentialCitations: 2,
    fieldsOfStudy: ["cs.AI"],
    source: "arxiv",
    category: "arxiv",
    tags: ["ai"],
    pdfUrl: "",
    chunkCount: 5,
    indexSize: 1024,
    ocrStatus: "done",
    isScanned: false,
    createdAt: "2024-01-01T00:00:00Z",
    updatedAt: "2024-01-01T00:00:00Z",
    ...overrides,
  } as Paper;
}

describe("PaperCard", () => {
  it("renders paper title and authors", () => {
    render(<PaperCard paper={makePaper()} />);
    expect(screen.getByText("Test Paper")).toBeInTheDocument();
    expect(screen.getByText("Alice, Bob")).toBeInTheDocument();
  });

  it("navigates to detail page when title clicked", async () => {
    render(<PaperCard paper={makePaper()} />);
    const user = userEvent.setup();
    await user.click(screen.getByText("Test Paper"));
    expect(mockNavigate).toHaveBeenCalledWith("/paper/p1");
  });

  it("shows metadata actions dropdown and triggers enrich", async () => {
    const onEnrich = vi.fn();
    const { baseElement } = render(<PaperCard paper={makePaper()} onEnrich={onEnrich} />);

    const user = userEvent.setup();
    const moreBtn = screen.getByLabelText("paper.metadataActions");
    await user.click(moreBtn);

    // Ant Design Dropdown menu is rendered in a portal on document.body
    const menuItem = baseElement.querySelector("[data-menu-id]");
    expect(menuItem).toBeInTheDocument();

    const enrichItem = screen.getByText("paper.enrichMetadata");
    await user.click(enrichItem);

    expect(onEnrich).toHaveBeenCalledTimes(1);
  });

  it("renders all metadata action placeholders", async () => {
    render(<PaperCard paper={makePaper()} onEnrich={vi.fn()} />);

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("paper.metadataActions"));

    expect(screen.getByText("paper.enrichMetadata")).toBeInTheDocument();
    expect(screen.getByText("paper.extractDoi")).toBeInTheDocument();
    expect(screen.getByText("paper.renamePdf")).toBeInTheDocument();
    expect(screen.getByText("paper.extractAnnotations")).toBeInTheDocument();
  });

  it("does not show metadata actions dropdown when onEnrich is not provided", () => {
    render(<PaperCard paper={makePaper()} />);
    expect(screen.queryByLabelText("paper.metadataActions")).not.toBeInTheDocument();
  });

  it("applies report card style and icon for reflection reports", () => {
    const { container } = render(<PaperCard paper={makePaper({ category: "report" })} />);
    const card = container.querySelector(".pf-report-card");
    expect(card).toBeInTheDocument();
    expect(screen.getByLabelText("paper.report")).toBeInTheDocument();
  });

  it("does not apply report card style for regular papers", () => {
    const { container } = render(<PaperCard paper={makePaper({ category: "arxiv" })} />);
    const card = container.querySelector(".pf-report-card");
    expect(card).not.toBeInTheDocument();
  });
});
