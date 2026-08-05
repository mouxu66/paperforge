import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "@/test-utils";
import PDFViewer from "../PDFViewer";
import type { Paper } from "@/api/types";

// pdfjs-dist is heavy and uses workers; mock it to keep tests fast.
const mockDoc = {
  numPages: 2,
  getPage: vi.fn(() =>
    Promise.resolve({
      getViewport: vi.fn(() => ({ width: 600, height: 800 })),
      getTextContent: vi.fn(() =>
        Promise.resolve({
          items: [{ str: "hello world", transform: [1, 0, 0, 1, 0, 0], width: 100, height: 12 }],
        }),
      ),
      render: vi.fn(() => ({ promise: Promise.resolve() })),
    }),
  ),
  destroy: vi.fn(() => Promise.resolve()),
};

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: { workerSrc: "" },
  getDocument: vi.fn(() => ({
    promise: Promise.resolve(mockDoc),
    destroy: vi.fn(() => Promise.resolve()),
  })),
  TextLayer: vi.fn().mockImplementation(() => ({ render: vi.fn(() => Promise.resolve()) })),
}));

vi.mock("@/api/papers", () => ({
  translatePaperText: vi.fn(() => Promise.resolve({ translation: "你好世界" })),
  fetchTranslationHistory: vi.fn(() => Promise.resolve([])),
  saveTranslationHistory: vi.fn(() => Promise.resolve({ id: "h1", success: true })),
  deleteTranslationHistory: vi.fn(() => Promise.resolve()),
}));

function renderWithRouter(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

const mockPaper: Paper = {
  id: "p1",
  title: "Test Paper",
  authors: ["A"],
  year: 2024,
  abstract: "Abstract",
  category: "cs",
  tags: [],
  citations: 0,
  chunkCount: 1,
  indexSize: 100,
  pdfUrl: "https://example.com/test.pdf",
  source: "arxiv",
};

describe("PDFViewer — WP-2.7 floating translation menu", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.getSelection = vi.fn(() => ({
      toString: () => "hello world",
      rangeCount: 1,
      isCollapsed: false,
      getRangeAt: () => ({
        getClientRects: () => [{ left: 0, top: 0, width: 100, height: 12 }] as DOMRectList,
      }),
    })) as any;
  });

  it("renders PDF viewer without crashing", async () => {
    renderWithRouter(<PDFViewer paper={mockPaper} pdfUrl="test.pdf" />);
    await waitFor(() => expect(screen.getByText("pdf.sidebar")).toBeInTheDocument());
  });
});
