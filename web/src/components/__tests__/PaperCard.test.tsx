import { describe, it, expect, vi, beforeEach } from "vitest";
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
  beforeEach(() => {
    mockNavigate.mockClear();
  });

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

  it("opens the detailed reflection analysis without using the paper detail route", async () => {
    const user = userEvent.setup();
    render(<PaperCard paper={makePaper({ category: "report" })} />);

    await user.click(screen.getByRole("button", { name: "paper.reflectionAnalysis" }));

    expect(mockNavigate).toHaveBeenLastCalledWith("/reflection/result/p1");
    expect(mockNavigate).toHaveBeenCalledTimes(1);
  });

  it("does not apply report card style for regular papers", () => {
    const { container } = render(<PaperCard paper={makePaper({ category: "arxiv" })} />);
    const card = container.querySelector(".pf-report-card");
    expect(card).not.toBeInTheDocument();
  });
});

/**
 * 卡片上的 DEPTH 数字必须是【综合分】（calibratedScore = 判决依据），
 * 不能是【创新分】（noveltyScore = Q2 单维度）。
 *
 * 回归背景：卡片曾显示 noveltyScore，导致「综合分 0.48 大修」与「综合分 0.38 拒稿」
 * 两篇都显示成 50，用户以为系统分数与判决矛盾（见 2026-09-16 排查）。
 */
describe("PaperCard DEPTH 分数口径", () => {
  beforeEach(() => {
    mockNavigate.mockClear();
  });

  it("显示综合分而不是创新分（两者不同时必须取综合分）", () => {
    render(
      <PaperCard
        paper={makePaper()}
        depthScore={{ verdict: "major_revision", calibratedScore: 0.48, noveltyScore: 0.5, fatalCount: 0 }}
      />,
    );

    expect(screen.getByText("大修")).toBeInTheDocument();
    // 0.48 → 48（综合分）；0.50 → 50（创新分）不得出现
    expect(screen.getByText("48")).toBeInTheDocument();
    expect(screen.queryByText("50")).not.toBeInTheDocument();
  });

  it.each([
    ["accept", "接收", 0.81],
    ["minor_revision", "小修", 0.66],
    ["major_revision", "大修", 0.48],
    ["reject", "拒稿", 0.33],
  ])("判决 %s 渲染为 %s 且数字为综合分四舍五入", (verdict, label, score) => {
    render(
      <PaperCard
        paper={makePaper()}
        depthScore={{
          verdict,
          calibratedScore: score,
          noveltyScore: 0.99,
          fatalCount: verdict === "reject" ? 3 : 0,
        }}
      />,
    );

    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByText(String(Math.round(score * 100)))).toBeInTheDocument();
    // 创新分 0.99 → 99 不得成为主数字
    expect(screen.queryByText("99")).not.toBeInTheDocument();
  });

  it("tooltip 使用带综合分/致命缺陷/创新分的文案", async () => {
    const user = userEvent.setup();
    render(
      <PaperCard
        paper={makePaper()}
        depthScore={{ verdict: "reject", calibratedScore: 0.33, noveltyScore: 0.65, fatalCount: 3 }}
      />,
    );

    await user.hover(screen.getByText("拒稿"));
    expect(await screen.findByText("paper.depthSummary")).toBeInTheDocument();
  });

  it("旧响应缺 calibrated_score 时不崩、不显示数字（仅判决）", () => {
    render(
      <PaperCard
        paper={makePaper()}
        depthScore={{ verdict: "minor_revision", calibratedScore: null, noveltyScore: 0.62, fatalCount: null }}
      />,
    );

    expect(screen.getByText("小修")).toBeInTheDocument();
    expect(screen.queryByText("62")).not.toBeInTheDocument();
  });

  it("点击判决跳转到详情页深度评审 tab", async () => {
    const user = userEvent.setup();
    render(
      <PaperCard
        paper={makePaper()}
        depthScore={{ verdict: "major_revision", calibratedScore: 0.48, noveltyScore: 0.5, fatalCount: 1 }}
      />,
    );

    await user.click(screen.getByText("大修"));
    expect(mockNavigate).toHaveBeenCalledWith("/paper/p1?tab=depthReview");
  });
});
