export type EvidenceSeverity = "fatal" | "minor";

export const EVIDENCE_COLORS = {
  fatal: { border: "#ff4d4f", bg: "rgba(255, 77, 79, 0.06)" },
  minor: { border: "#faad14", bg: "var(--pf-bg-secondary)" },
  highlight: { border: "#faad14", bg: "#fffbe6" },
};

export function getEvidenceSeverityStyles(
  severity?: string | null,
  highlighted?: boolean,
) {
  if (highlighted) return EVIDENCE_COLORS.highlight;
  if (severity === "fatal") return EVIDENCE_COLORS.fatal;
  if (severity === "minor") return EVIDENCE_COLORS.minor;
  return { border: "transparent", bg: "var(--pf-bg-secondary)" };
}
