import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import FieldValue from "@/components/duplicate/FieldValue";
import type { DuplicateGroupPaper } from "@/api/types";

function makePaper(overrides?: Partial<DuplicateGroupPaper>): DuplicateGroupPaper {
  return {
    id: "p1",
    title: "Title",
    authors: ["Alice", "Bob"],
    year: 2024,
    abstract: "This is a long abstract that should be truncated.",
    journal: "Journal A",
    pdfUrl: "https://example.com/paper.pdf",
    source: "arxiv",
    citations: 10,
    tags: ["ai", "ml"],
    ...overrides,
  } as DuplicateGroupPaper;
}

describe("FieldValue", () => {
  it("renders title", () => {
    render(<FieldValue field="title" paper={makePaper()} />);
    expect(screen.getByText("Title")).toBeInTheDocument();
  });

  it("renders authors joined by comma", () => {
    render(<FieldValue field="authors" paper={makePaper()} />);
    expect(screen.getByText("Alice, Bob")).toBeInTheDocument();
  });

  it("renders year", () => {
    render(<FieldValue field="year" paper={makePaper()} />);
    expect(screen.getByText("2024")).toBeInTheDocument();
  });

  it("renders truncated abstract", () => {
    render(<FieldValue field="abstract" paper={makePaper()} />);
    const el = screen.getByText(/truncated/);
    expect(el.textContent).toMatch(/truncated\.{3,4}$/);
  });

  it("renders journal", () => {
    render(<FieldValue field="journal" paper={makePaper()} />);
    expect(screen.getByText("Journal A")).toBeInTheDocument();
  });

  it("renders truncated pdf url", () => {
    render(<FieldValue field="pdfUrl" paper={makePaper()} />);
    expect(screen.getByText("https://example.com/paper.pdf...")).toBeInTheDocument();
  });

  it("renders tags", () => {
    render(<FieldValue field="tags" paper={makePaper()} />);
    expect(screen.getByText("ai")).toBeInTheDocument();
    expect(screen.getByText("ml")).toBeInTheDocument();
  });

  it("renders fallback dash for missing values", () => {
    render(<FieldValue field="title" paper={makePaper({ title: "" })} />);
    expect(screen.getByText("-")).toBeInTheDocument();
  });
});
