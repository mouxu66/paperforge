import type { DepthReviewV4Result } from "@/api/depth";

export const DEFAULT_PAPER_ID = "p1";
export const DEFAULT_REVIEW_ID = "r1";

/**
 * 构造一个类型完整、字段齐全的 DEPTH v4.1 审稿结果，用于页面/组件测试。
 * 通过 `overrides` 可覆盖任意字段，避免各测试文件重复写 mock 数据。
 */
export function buildDepthReviewV4Result(
  overrides: Partial<DepthReviewV4Result> = {},
): DepthReviewV4Result {
  return {
    id: DEFAULT_REVIEW_ID,
    paper_id: DEFAULT_PAPER_ID,
    status: "completed",
    q0_result: {
      has_substance: true,
      expectation: 0.8,
      reasoning: "The paper presents a concrete contribution.",
      evidence: "abstract+introduction",
    },
    q1_result: {
      type: "A",
      secondary_type: "none",
      confidence: 0.5,
      reasoning: "Type A paper with minor alignment risk.",
    },
    evidence_pool: [],
    q2_result: {
      novelty_score: 0.7,
      hotspot_alignment_score: 0.6,
      core_contribution: "Proposes a scalable figure consistency pipeline.",
      reasoning: "Novel axis-aware claim validation.",
      evidence_id: "E1",
    },
    q3_result: {
      rigor_score: 0.6,
      missing_items: [],
      reasoning: "Methodology is sound but lacks ablation details.",
    },
    q4_result: {
      influence_score: 0.5,
      reproducibility_score: 0.5,
      reasoning: "Moderate potential impact; reproduction requires GPU.",
    },
    q5a_result: { critique_points: [] },
    q5b_result: { defense_points: [] },
    q5c_result: {
      reasoning: "Delta is within expected bounds after calibration.",
      calibrated_score: 0.7,
      delta: 0.05,
      delta_missing: false,
      llm_verdict: "major_revision",
    },
    final_verdict: {
      final_verdict: "major_revision",
      calibrated_score: 0.7,
      override_reason: "",
      llm_verdict: "major_revision",
      base_score: 0.7,
      weights: {},
      evidence_checks: {},
      figure_coverage: "analyzed",
      figure_consistency_score: 0.85,
      figure_flags: [],
      figure_evidence_count: 0,
      qf_reasoning: "",
      m0_active: false,
    },
    final_score: 70,
    node_score_stds: {},
    error_message: null,
    created_at: "2024-01-01T00:00:00.000Z",
    completed_at: "2024-01-01T00:00:00.000Z",
    ...overrides,
  };
}
