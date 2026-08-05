import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import FigureConsistencyCard from "../FigureConsistencyCard";

const mockI18n = { language: "zh" };

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (_key: string, defaultValue?: string) => defaultValue || _key,
    i18n: mockI18n,
  }),
}));

const baseFinalVerdict = {
  figure_coverage: "analyzed",
  figure_consistency_score: 0.85,
  figure_flags: [],
  figure_evidence_count: 2,
};

const evidencePool = [
  {
    id: "E1",
    content: "[MINOR] [图文一致性] 图1 (第2页): accuracy=0.95 超出坐标轴范围 [0.8, 0.9]",
    contentZh: "[MINOR] [图文一致性] 图1 (第2页): accuracy=0.95 超出坐标轴范围 [0.8, 0.9]",
    contentEn: "[MINOR] [Figure consistency] Figure 1 (p2): accuracy=0.95 is outside axis range [0.8, 0.9]",
    section: "Figures",
    keywords: ["figure", "claim", "accuracy", "out-of-range"],
    severity: "minor",
  },
  {
    id: "E2",
    content: "[FATAL] [图文一致性] 图2 (第3页): f1=1.2 超出坐标轴范围 [0, 1]",
    contentZh: "[FATAL] [图文一致性] 图2 (第3页): f1=1.2 超出坐标轴范围 [0, 1]",
    contentEn: "[FATAL] [Figure consistency] Figure 2 (p3): f1=1.2 is outside axis range [0, 1]",
    section: "Figures",
    keywords: ["figure", "claim", "f1", "out-of-range"],
    severity: "fatal",
  },
];

describe("FigureConsistencyCard", () => {
  beforeEach(() => {
    mockI18n.language = "zh";
  });

  it("renders Chinese content by default", () => {
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
      />,
    );

    expect(screen.getByText(/图1 \(第2页\)/)).toBeInTheDocument();
    expect(screen.getByText(/图2 \(第3页\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Figure 1 \(p2\)/)).not.toBeInTheDocument();
  });

  it("renders English content when language is set to English", () => {
    mockI18n.language = "en";
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
      />,
    );

    expect(screen.getByText(/Figure 1 \(p2\)/)).toBeInTheDocument();
    expect(screen.getByText(/Figure 2 \(p3\)/)).toBeInTheDocument();
    expect(screen.queryByText(/图1 \(第2页\)/)).not.toBeInTheDocument();
  });

  it("displays severity label and prominent fatal style for fatal evidence", () => {
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
      />,
    );

    const fatalItem = screen.getByTestId("evidence-item-E2");
    expect(fatalItem).toHaveTextContent("致命");
    expect(fatalItem).toContainElement(screen.getByLabelText("fatal"));
    expect(fatalItem).toHaveTextContent("图2 (第3页)");
  });

  it("displays minor severity label for minor evidence", () => {
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
      />,
    );

    const minorItem = screen.getByTestId("evidence-item-E1");
    expect(minorItem).toHaveTextContent("轻微");
    expect(minorItem).toHaveTextContent("图1 (第2页)");
  });

  it("highlights active evidence items", () => {
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
        activeEvidenceIds={["E2"]}
      />,
    );

    const fatalItem = screen.getByTestId("evidence-item-E2");
    expect(fatalItem).toHaveAttribute("data-active", "true");
  });

  it("calls onEvidenceClick when an evidence item is clicked", () => {
    const handleClick = vi.fn();
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
        onEvidenceClick={handleClick}
      />,
    );

    const fatalItem = screen.getByTestId("evidence-item-E2");
    fireEvent.click(fatalItem);
    expect(handleClick).toHaveBeenCalledWith("E2");
  });

  it("falls back to default content when language-specific fields are missing", () => {
    const poolWithoutLang = [
      {
        id: "E1",
        content: "default content",
        section: "Figures",
        keywords: [],
      },
    ];
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={poolWithoutLang as any}
      />,
    );

    expect(screen.getByText("default content")).toBeInTheDocument();
  });

  it("renders a severity legend explaining fatal and minor severity", () => {
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
      />,
    );

    const legend = screen.getByTestId("severity-legend");
    expect(legend).toHaveTextContent("致命");
    expect(legend).toHaveTextContent("图文一致性存在严重冲突");
    expect(legend).toHaveTextContent("轻微");
    expect(legend).toHaveTextContent("图文一致性存在轻微偏差，建议复核");
  });

  it("does not render severity legend when there is no figure evidence", () => {
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={[]}
      />,
    );

    expect(screen.queryByTestId("severity-legend")).not.toBeInTheDocument();
  });

  it("expands and collapses evidence details on expand button click", () => {
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
      />,
    );

    // Details are hidden by default
    expect(screen.queryByTestId("evidence-detail-E1")).not.toBeInTheDocument();

    // Expand E1
    const expandBtn = screen.getByTestId("expand-btn-E1");
    fireEvent.click(expandBtn);

    const detail = screen.getByTestId("evidence-detail-E1");
    expect(detail).toBeInTheDocument();
    expect(detail).toHaveTextContent("Figures");
    expect(detail).toHaveTextContent("out-of-range");
    // altContent differs from displayed Chinese content
    expect(detail).toHaveTextContent(/Figure 1 \(p2\)/);

    // Collapse E1
    fireEvent.click(expandBtn);
    expect(screen.queryByTestId("evidence-detail-E1")).not.toBeInTheDocument();
  });

  it("does not trigger onEvidenceClick when expand button is clicked", () => {
    const handleClick = vi.fn();
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
        onEvidenceClick={handleClick}
      />,
    );

    const expandBtn = screen.getByTestId("expand-btn-E1");
    fireEvent.click(expandBtn);
    expect(handleClick).not.toHaveBeenCalled();

    // Collapse button also should not trigger
    fireEvent.click(expandBtn);
    expect(handleClick).not.toHaveBeenCalled();
  });

  it("still triggers onEvidenceClick when clicking the card body", () => {
    const handleClick = vi.fn();
    render(
      <FigureConsistencyCard
        finalVerdict={baseFinalVerdict as any}
        evidencePool={evidencePool as any}
        onEvidenceClick={handleClick}
      />,
    );

    const card = screen.getByTestId("evidence-item-E1");
    fireEvent.click(card);
    expect(handleClick).toHaveBeenCalledWith("E1");
  });
});
