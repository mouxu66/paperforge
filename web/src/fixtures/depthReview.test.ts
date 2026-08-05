import { describe, it, expect } from "vitest";
import { buildDepthReviewV4Result } from "./depthReview";

describe("buildDepthReviewV4Result", () => {
  it("returns a complete DepthReviewV4Result with all required fields", () => {
    const result = buildDepthReviewV4Result();

    expect(result.id).toBe("r1");
    expect(result.paper_id).toBe("p1");
    expect(result.status).toBe("completed");
    expect(result.evidence_pool).toEqual([]);
    expect(result.final_verdict).toBeDefined();
    expect(result.final_verdict?.figure_consistency_score).toBe(0.85);
    expect(result.q5a_result).toBeDefined();
    expect(result.q5c_result).toBeDefined();
    expect(result.final_score).toBe(70);
    expect(result.node_score_stds).toEqual({});
  });

  it("applies overrides", () => {
    const result = buildDepthReviewV4Result({
      paper_id: "p2",
      status: "failed",
      final_score: 55,
      q5a_result: {
        critique_points: [{ point: "test", severity: "fatal" }],
      },
    });

    expect(result.paper_id).toBe("p2");
    expect(result.status).toBe("failed");
    expect(result.final_score).toBe(55);
    expect(result.q5a_result?.critique_points).toHaveLength(1);
  });

  it("accepts a custom evidence_pool", () => {
    const result = buildDepthReviewV4Result({
      evidence_pool: [
        { id: "E1", content: "claim", section: "Figures", keywords: [], severity: "fatal" },
      ],
    });

    expect(result.evidence_pool).toHaveLength(1);
    expect(result.evidence_pool?.[0].id).toBe("E1");
  });
});
