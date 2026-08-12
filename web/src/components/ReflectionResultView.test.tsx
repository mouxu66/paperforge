import { describe, expect, it, beforeEach, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const mockNavigate = vi.fn();
const mockSubscribeSSE = vi.fn();

vi.mock("react-router-dom", () => ({
  useParams: () => ({ paperId: "report-1" }),
  useNavigate: () => mockNavigate,
}));

vi.mock("@/api/reflection", () => ({
  getReflectionResult: vi.fn(),
  startReflectionReview: vi.fn(),
}));

vi.mock("@/store/useTaskStore", () => ({
  useTaskStore: (selector?: (state: { subscribeSSE: typeof mockSubscribeSSE }) => unknown) =>
    selector ? selector({ subscribeSSE: mockSubscribeSSE }) : { subscribeSSE: mockSubscribeSSE },
}));

import { getReflectionResult } from "@/api/reflection";
import ReflectionResultView from "./ReflectionResultView";

const completedResult = {
  id: 7,
  paper_id: "report-1",
  kind: "report",
  status: "completed",
  version: "reflection-v2",
  error_message: null,
  created_at: "2026-08-06T10:00:00Z",
  completed_at: "2026-08-06T10:01:00Z",
  result: {
    claims: [
      { id: "C1", text: "方法在小样本场景下更稳定", evidence_id: "E1" },
      { id: "C2", text: "报告遗漏了消融实验", evidence_id: null },
    ],
    evidence_pool: [
      { id: "E1", snippet: "原文报告了小样本实验结果", claim_ref: "C1" },
      { id: "E2", snippet: "原文包含消融实验章节", claim_ref: null },
    ],
    scores: {
      understanding_accuracy: 0.8,
      analysis_depth: 0.7,
      innovative_insights: 0.6,
      evidence_support: 0.75,
      fidelity: 0.72,
      coverage: 0.55,
      average: 0.69,
    },
    analysis_v2: {
      understanding_accuracy: 0.8,
      analysis_depth: 0.7,
      innovative_insights: 0.6,
      evidence_support: 0.75,
      fidelity: 0.72,
      coverage: 0.55,
      average: 0.69,
      paper_preview_chars: 11650,
      paper_chars: 20000,
      report_chars: 720,
    },
    summary: "报告准确总结了方法，并提出了一个可验证的批评点。",
    verdict: "needs_evidence",
    verdict_reason: "有一个核心观点缺少直接证据。",
    fidelity: 0.72,
    coverage: 0.55,
    coverage_covered: [{ keypoint: "方法设计", sim: 0.82 }],
    coverage_uncovered: [{ keypoint: "消融实验", sim: 0.28 }],
    fidelity_anchors: [{ sentence: "方法在小样本场景下更稳定", sim: 0.72 }],
    fidelity_stray_claims: [],
  },
};

describe("ReflectionResultView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getReflectionResult).mockResolvedValue(completedResult as never);
  });

  it("renders the six-dimension analysis overview and coverage details", async () => {
    render(<ReflectionResultView />);

    await waitFor(() => expect(getReflectionResult).toHaveBeenCalledWith("report-1"));

    expect(screen.getByText("感悟报告深度分析")).toBeInTheDocument();
    expect(screen.getByText("综合得分 69%")).toBeInTheDocument();
    expect(screen.getByText("需补证据 Needs Evidence")).toBeInTheDocument();
    expect(screen.getByText("论文要点覆盖度")).toBeInTheDocument();
    expect(screen.getByText("消融实验")).toBeInTheDocument();
    expect(screen.getByText("报告覆盖了多少原论文核心内容")).toBeInTheDocument();
  });

  it("shows how much of the original paper the review actually read", async () => {
    render(<ReflectionResultView />);

    await waitFor(() => expect(getReflectionResult).toHaveBeenCalledWith("report-1"));

    expect(screen.getByText(/分析依据：读取原论文前 11,650 字/)).toBeInTheDocument();
    expect(screen.getByText(/全文 20,000 字/)).toBeInTheDocument();
    expect(screen.getByText(/报告 720 字/)).toBeInTheDocument();
  });

  it("filters the evidence chain when a claim is selected", async () => {
    render(<ReflectionResultView />);

    await waitFor(() => expect(screen.getByText("核心观点与证据链")).toBeInTheDocument());

    fireEvent.click(screen.getByText("方法在小样本场景下更稳定"));

    expect(screen.getByText("C1 的关联证据")).toBeInTheDocument();
    expect(screen.getByText("原文报告了小样本实验结果")).toBeInTheDocument();
    expect(screen.queryByText("原文包含消融实验章节")).not.toBeInTheDocument();
  });
});
