import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import FigureDetailsList from "../FigureDetailsList";

const mockFetchPaperFigures = vi.fn();

vi.mock("@/api/papers", () => ({
  fetchPaperFigures: (...args: unknown[]) => mockFetchPaperFigures(...args),
}));

describe("FigureDetailsList", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders without crashing and eventually shows empty state", async () => {
    mockFetchPaperFigures.mockResolvedValue([]);
    render(<FigureDetailsList paperId="paper-1" />);
    await waitFor(() => {
      expect(screen.getByText("figureDetails.noFigures")).toBeInTheDocument();
    });
  });

  it("renders axis info and source text span", async () => {
    mockFetchPaperFigures.mockResolvedValue([
      {
        id: 1,
        paperId: "paper-1",
        page: 3,
        figureIndex: 0,
        figureNumber: 1,
        figurePath: "/figures/1.png",
        ocrText: "accuracy vs epochs",
        captionText: "Figure 1: accuracy",
        qwenSummary: "summary",
        source: "bitmap",
        sourceTextSpan: "As shown in Fig.1, our method achieves 90% accuracy.",
        axisInfo: {
          xLabel: "Epoch",
          yLabel: "Accuracy",
          xTicks: [0, 10, 20],
          yTicks: [0.8, 0.9, 1.0],
          legendItems: ["Ours", "Baseline"],
          captionSummary: "accuracy comparison",
        },
        claimValidation: { claims: [], validated: [] },
        curvePoints: null,
      },
    ]);

    render(<FigureDetailsList paperId="paper-1" />);

    await waitFor(() => {
      expect(screen.getByText("figureDetails.figureTitle")).toBeInTheDocument();
    });

    expect(screen.getByText("figureDetails.sourceTextSpan")).toBeInTheDocument();
    expect(
      screen.getByText("As shown in Fig.1, our method achieves 90% accuracy.")
    ).toBeInTheDocument();
    expect(screen.getByText(/X: Epoch/)).toBeInTheDocument();
    expect(screen.getByText(/Y: Accuracy/)).toBeInTheDocument();
  });

  it("highlights out-of-range claims", async () => {
    mockFetchPaperFigures.mockResolvedValue([
      {
        id: 1,
        paperId: "paper-1",
        page: 3,
        figureIndex: 0,
        figureNumber: 1,
        figurePath: "/figures/1.png",
        ocrText: "accuracy vs epochs",
        captionText: "Figure 1",
        qwenSummary: "summary",
        source: "bitmap",
        sourceTextSpan: "Our method achieves 99% accuracy.",
        axisInfo: { xLabel: "Epoch", yLabel: "Accuracy", yTicks: [0.8, 0.9, 1.0] },
        claimValidation: {
          claims: [{ metric: "accuracy", value: 0.99 }],
          validated: [{ valid: false, metricMatched: true, reason: "exceeds axis range" }],
        },
        curvePoints: null,
      },
    ]);

    render(<FigureDetailsList paperId="paper-1" />);

    await waitFor(() => {
      expect(screen.getByText("figureDetails.claimValidation")).toBeInTheDocument();
    });

    expect(screen.getByText(/accuracy 0.99/)).toBeInTheDocument();
  });

  it("highlights the caption sentence of an invalid claim by context", async () => {
    mockFetchPaperFigures.mockResolvedValue([
      {
        id: 1,
        paperId: "paper-1",
        page: 3,
        figureIndex: 0,
        figureNumber: 1,
        figurePath: "/figures/1.png",
        ocrText: "accuracy vs epochs",
        captionText: "Baseline reaches 80% accuracy. Our method achieves 99% accuracy, which is outside the axis range.",
        qwenSummary: "summary",
        source: "bitmap",
        sourceTextSpan: null,
        axisInfo: { xLabel: "Epoch", yLabel: "Accuracy", yTicks: [0.8, 0.9, 1.0] },
        claimValidation: {
          claims: [
            { metric: "accuracy", value: 0.99, context: "Our method achieves 99% accuracy, which is outside the axis range." },
          ],
          validated: [{ valid: false, metricMatched: true, reason: "value 0.99 outside axis range [0.8, 1.0]" }],
        },
        curvePoints: null,
      },
    ]);

    render(<FigureDetailsList paperId="paper-1" />);

    await waitFor(() => {
      expect(screen.getByText("figureDetails.claimValidation")).toBeInTheDocument();
    });

    const highlighted = screen.getByText(/Our method achieves 99% accuracy/);
    expect(highlighted).toHaveAttribute("data-highlight", "invalid");

    // The other sentence should not be highlighted
    const baseline = screen.getByText(/Baseline reaches 80% accuracy/);
    expect(baseline).not.toHaveAttribute("data-highlight");
  });

  it("falls back to metric+value matching when context is absent", async () => {
    mockFetchPaperFigures.mockResolvedValue([
      {
        id: 1,
        paperId: "paper-1",
        page: 3,
        figureIndex: 0,
        figureNumber: 1,
        figurePath: "/figures/1.png",
        ocrText: "accuracy vs epochs",
        captionText: "Model A reaches 80% accuracy. Model B achieves 99% accuracy.",
        qwenSummary: "summary",
        source: "bitmap",
        sourceTextSpan: null,
        axisInfo: { xLabel: "Epoch", yLabel: "Accuracy", yTicks: [0.8, 0.9, 1.0] },
        claimValidation: {
          claims: [{ metric: "accuracy", value: 0.99 }],
          validated: [{ valid: false, metricMatched: true, reason: "out of range" }],
        },
        curvePoints: null,
      },
    ]);

    render(<FigureDetailsList paperId="paper-1" />);

    await waitFor(() => {
      expect(screen.getByText("figureDetails.claimValidation")).toBeInTheDocument();
    });

    const highlighted = screen.getByText(/Model B achieves 99% accuracy/);
    expect(highlighted).toHaveAttribute("data-highlight", "invalid");
  });

  it("renders a curve-corrected badge for claims fixed by curve points", async () => {
    mockFetchPaperFigures.mockResolvedValue([
      {
        id: 1,
        paperId: "paper-1",
        page: 3,
        figureIndex: 0,
        figureNumber: 1,
        figurePath: "/figures/1.png",
        ocrText: "accuracy vs epochs",
        captionText: "Figure 1",
        qwenSummary: "summary",
        source: "bitmap",
        sourceTextSpan: null,
        axisInfo: { xLabel: "Epoch", yLabel: "Accuracy", yTicks: [0.8, 0.9, 1.0] },
        claimValidation: {
          claims: [{ metric: "accuracy", value: 0.95 }],
          validated: [
            {
              valid: true,
              metricMatched: true,
              curveCorrected: true,
              reason: "value 0.95 outside axis range but within curve y-range [0.9, 0.98]",
            },
          ],
        },
        curvePoints: null,
      },
    ]);

    render(<FigureDetailsList paperId="paper-1" />);

    await waitFor(() => {
      expect(screen.getByText("figureDetails.claimValidation")).toBeInTheDocument();
    });

    expect(screen.getByText("figureDetails.curveCorrected")).toBeInTheDocument();
  });
});
