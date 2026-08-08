import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "@/test-utils";
import { Routes, Route  } from "react-router-dom";
import DetailPage from "../DetailPage";
import type { Paper } from "@/api/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------
const mockPaper: Paper = {
  id: "p1",
  title: "Test Paper",
  authors: ["Alice", "Bob"],
  year: 2024,
  abstract: "Abstract text",
  category: "arxiv",
  tags: ["tag1"],
  citations: 10,
  chunkCount: 5,
  indexSize: 1024,
  pdfUrl: "https://arxiv.org/pdf/p1.pdf",
  source: "arxiv",
  journal: "",
  favorited: false,
  doi: null,
  ocrStatus: "done",
  isScanned: false,
};

const fetchPaperById = vi.fn();
const fetchPaperSentiment = vi.fn();
const previewEnrichPaperMetadata = vi.fn();
const enrichPaperMetadata = vi.fn();
const extractPaperDoi = vi.fn();
const renamePaperPdf = vi.fn();
const extractPaperAnnotations = vi.fn();
const listPdfAnnotations = vi.fn();

// antd 6：DetailPage 经 App.useApp() 获取 message 实例。无 <App> context 时
// useApp 返回空对象（message.error 非函数），这里部分 mock 仅覆写 useApp。
vi.mock("antd", async () => {
  const actual = await vi.importActual<typeof import("antd")>("antd");
  const message = {
    ...actual.message,
    success: vi.fn(),
    error: vi.fn(),
  };
  return {
    ...actual,
    App: {
      ...actual.App,
      useApp: () => ({ message, notification: {}, modal: {} }),
    },
  };
});

vi.mock("@/api/papers", () => ({
  fetchPaperById: (...args: any[]) => fetchPaperById(...args),
  fetchPaperSentiment: (...args: any[]) => fetchPaperSentiment(...args),
  previewEnrichPaperMetadata: (...args: any[]) => previewEnrichPaperMetadata(...args),
  enrichPaperMetadata: (...args: any[]) => enrichPaperMetadata(...args),
  extractPaperDoi: (...args: any[]) => extractPaperDoi(...args),
  renamePaperPdf: (...args: any[]) => renamePaperPdf(...args),
  extractPaperAnnotations: (...args: any[]) => extractPaperAnnotations(...args),
  reocrPaper: vi.fn(),
}));

vi.mock("@/api/pdfAnnotations", () => ({
  listPdfAnnotations: (...args: any[]) => listPdfAnnotations(...args),
  createPdfAnnotation: vi.fn(),
  updatePdfAnnotation: vi.fn(),
  deletePdfAnnotation: vi.fn(),
}));

vi.mock("@/api/notes", () => ({
  fetchCitationRelations: vi.fn().mockResolvedValue({
    citations: 10,
    references: [],
  }),
}));

vi.mock("@/components/AbstractTab", () => ({
  default: () => <div data-testid="abstract-tab" />,
}));

vi.mock("@/components/PdfTab", () => ({
  default: () => <div data-testid="pdf-tab" />,
}));

vi.mock("@/components/CiteTab", () => ({
  default: () => <div data-testid="cite-tab" />,
}));

vi.mock("@/components/NoteList", () => ({
  default: () => <div data-testid="note-list" />,
}));

vi.mock("@/components/RelationGraphTab", () => ({
  default: () => <div data-testid="relation-graph-tab" />,
}));

vi.mock("@/components/PdfAnnotationsTab", () => ({
  default: ({ refreshKey }: { refreshKey: number }) => (
    <div data-testid="pdf-annotations-tab" data-refresh-key={refreshKey} />
  ),
}));

vi.mock("@/components/FavoriteButton", () => ({
  default: () => <button data-testid="favorite-button">Favorite</button>,
}));

vi.mock("@/components/EnrichDiffModal", () => ({
  default: ({ open }: { open: boolean }) => <div data-testid="enrich-modal" data-open={open} />,
}));

vi.mock("@/components/FigureDetailsList", () => ({
  default: ({ paperId }: { paperId: string }) => <div data-testid="figure-details-list" data-paper-id={paperId} />,
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function renderDetailPage() {
  return render(
    <MemoryRouter initialEntries={["/paper/p1"]}>
      <Routes>
        <Route path="/paper/:id" element={<DetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  fetchPaperById.mockResolvedValue(mockPaper);
  fetchPaperSentiment.mockResolvedValue({
    paperId: "p1",
    sentiments: [],
    counts: { support: 0, criticize: 0, background: 0 },
  });
  previewEnrichPaperMetadata.mockResolvedValue({
    original: mockPaper,
    enriched: { ...mockPaper, citations: 20 },
  });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("DetailPage — metadata actions", () => {
  it("renders paper details and metadata action buttons", async () => {
    renderDetailPage();
    await waitFor(() => expect(fetchPaperById).toHaveBeenCalledWith("p1"));
    expect(screen.getByText("Test Paper")).toBeInTheDocument();
    expect(screen.getByText("paper.enrichMetadata")).toBeInTheDocument();
  });

  it("extracts DOI when the menu item is clicked", async () => {
    extractPaperDoi.mockResolvedValue({ ...mockPaper, doi: "10.1234/test" });
    renderDetailPage();
    await waitFor(() => expect(fetchPaperById).toHaveBeenCalled());

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("paper.metadataActions"));
    const doiItem = await screen.findByText("paper.extractDoi");
    await user.click(doiItem);

    await waitFor(() => expect(extractPaperDoi).toHaveBeenCalledWith("p1"));
  });

  it("renames PDF when the menu item is clicked", async () => {
    renamePaperPdf.mockResolvedValue({ ...mockPaper, pdfUrl: "/uploads/2024 - Test Paper.pdf" });
    renderDetailPage();
    await waitFor(() => expect(fetchPaperById).toHaveBeenCalled());

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("paper.metadataActions"));
    const renameItem = await screen.findByText("paper.renamePdf");
    await user.click(renameItem);

    await waitFor(() => expect(renamePaperPdf).toHaveBeenCalledWith("p1"));
  });

  it("extracts annotations when the menu item is clicked", async () => {
    extractPaperAnnotations.mockResolvedValue({ count: 3 });
    renderDetailPage();
    await waitFor(() => expect(fetchPaperById).toHaveBeenCalled());

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("paper.metadataActions"));
    const annotationsItem = await screen.findByText("paper.extractAnnotations");
    await user.click(annotationsItem);

    await waitFor(() => expect(extractPaperAnnotations).toHaveBeenCalledWith("p1"));
  });

  it("renders the annotations tab and refreshes it after extraction", async () => {
    extractPaperAnnotations.mockResolvedValue({ count: 2 });
    renderDetailPage();
    await waitFor(() => expect(fetchPaperById).toHaveBeenCalled());

    const tab = screen.getByText("detail.tabAnnotations");
    expect(tab).toBeInTheDocument();
    fireEvent.click(tab);

    const annotationsTab = await screen.findByTestId("pdf-annotations-tab");
    expect(annotationsTab).toHaveAttribute("data-refresh-key", "0");

    const user = userEvent.setup();
    await user.click(screen.getByLabelText("paper.metadataActions"));
    const annotationsItem = await screen.findByText("paper.extractAnnotations");
    await user.click(annotationsItem);

    await waitFor(() => expect(extractPaperAnnotations).toHaveBeenCalledWith("p1"));
    await waitFor(() =>
      expect(screen.getByTestId("pdf-annotations-tab")).toHaveAttribute("data-refresh-key", "1"),
    );
  });

  it("opens enrich preview modal when the main button is clicked", async () => {
    previewEnrichPaperMetadata.mockResolvedValue({
      original: mockPaper,
      enriched: { ...mockPaper, citations: 20 },
    });
    renderDetailPage();
    await waitFor(() => expect(fetchPaperById).toHaveBeenCalled());

    fireEvent.click(screen.getByText("paper.enrichMetadata"));

    await waitFor(() => expect(previewEnrichPaperMetadata).toHaveBeenCalledWith("p1"));
    expect(screen.getByTestId("enrich-modal").getAttribute("data-open")).toBe("true");
  });

  it("renders the figures tab and displays the figure details list when active", async () => {
    const user = userEvent.setup();
    renderDetailPage();
    await waitFor(() => expect(fetchPaperById).toHaveBeenCalled());

    const tab = screen.getByText("detail.tabFigures");
    expect(tab).toBeInTheDocument();

    await user.click(tab);

    const figureDetailsList = await screen.findByTestId("figure-details-list");
    expect(figureDetailsList).toHaveAttribute("data-paper-id", "p1");
  });
});
