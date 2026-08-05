import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import PdfAnnotationsTab from "@/components/PdfAnnotationsTab";
import type { PdfAnnotation } from "@/api/types";

const listPdfAnnotations = vi.fn();

vi.mock("@/api/pdfAnnotations", () => ({
  listPdfAnnotations: (...args: any[]) => listPdfAnnotations(...args),
  createPdfAnnotation: vi.fn(),
  updatePdfAnnotation: vi.fn(),
  deletePdfAnnotation: vi.fn(),
}));

const mockAnnotations: PdfAnnotation[] = [
  {
    id: "a1",
    paperId: "p1",
    page: 3,
    quadpoints: [100, 200, 300, 200, 100, 250, 300, 250],
    color: "#ffeb3b",
    note: "Important point",
    createdAt: "2024-01-15T08:30:00Z",
  },
  {
    id: "a2",
    paperId: "p1",
    page: 5,
    quadpoints: [50, 100, 150, 100, 50, 120, 150, 120],
    color: "#4caf50",
    note: "",
    createdAt: "2024-01-16T10:00:00Z",
  },
];

beforeEach(() => {
  vi.clearAllMocks();
});

describe("PdfAnnotationsTab", () => {
  it("shows loading skeleton initially", async () => {
    let resolvePromise: (value: PdfAnnotation[]) => void = () => {};
    listPdfAnnotations.mockReturnValue(
      new Promise<PdfAnnotation[]>((resolve) => {
        resolvePromise = resolve;
      }),
    );
    render(<PdfAnnotationsTab paperId="p1" />);
    expect(screen.getByTestId("pdf-annotations-tab")).toBeInTheDocument();
    resolvePromise([]);
    expect(await screen.findByText("paper.noAnnotations")).toBeInTheDocument();
  });

  it("renders empty state when no annotations", async () => {
    listPdfAnnotations.mockResolvedValue([]);
    render(<PdfAnnotationsTab paperId="p1" />);
    expect(await screen.findByText("paper.noAnnotations")).toBeInTheDocument();
    expect(listPdfAnnotations).toHaveBeenCalledWith("p1");
  });

  it("renders annotations with page and note", async () => {
    listPdfAnnotations.mockResolvedValue(mockAnnotations);
    render(<PdfAnnotationsTab paperId="p1" />);
    expect(await screen.findByText("Important point")).toBeInTheDocument();
    expect(screen.getByText("paper.emptyAnnotation")).toBeInTheDocument();
    expect(screen.getAllByText("paper.annotationPage")).toHaveLength(2);
  });

  it("refetches when refreshKey changes", async () => {
    listPdfAnnotations.mockResolvedValue([]);
    const { rerender } = render(<PdfAnnotationsTab paperId="p1" refreshKey={0} />);
    expect(await screen.findByText("paper.noAnnotations")).toBeInTheDocument();

    listPdfAnnotations.mockResolvedValue(mockAnnotations);
    rerender(<PdfAnnotationsTab paperId="p1" refreshKey={1} />);
    expect(await screen.findByText("Important point")).toBeInTheDocument();
    expect(listPdfAnnotations).toHaveBeenCalledTimes(2);
  });
});
