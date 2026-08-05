import { useTranslation } from "react-i18next";
import type { DepthScore } from "@/api/types";

const DIMS = [
  { key: "novelty_score", labelKey: "depth.novelty" },
  { key: "rigor_score", labelKey: "depth.rigor" },
  { key: "influence_score", labelKey: "depth.influence" },
  { key: "reproducibility_score", labelKey: "depth.reproducibility" },
] as const;

interface Props {
  score: DepthScore;
  size?: number;
}

/** 类型守卫：禁止非论文评分对象（如 ReflectionScores）误入论文雷达。 */
function isDepthScore(value: unknown): value is DepthScore {
  if (typeof value !== "object" || value === null) return false;
  const s = value as Record<string, unknown>;
  return (
    typeof s.paper_id === "string" &&
    typeof s.novelty_score === "number" &&
    typeof s.rigor_score === "number" &&
    typeof s.influence_score === "number" &&
    typeof s.reproducibility_score === "number"
  );
}

export default function DepthRadar({ score, size = 160 }: Props) {
  const { t } = useTranslation();

  if (!isDepthScore(score)) {
    return (
      <div style={{ display: "inline-block", textAlign: "center", color: "#ef4444", fontSize: 12 }}>
        雷达图数据格式错误
      </div>
    );
  }

  const cx = size / 2;
  const cy = size / 2;
  const r = size * 0.35;
  const n = DIMS.length;
  const angleStep = (2 * Math.PI) / n;

  const getPoint = (i: number, value: number) => {
    const angle = -Math.PI / 2 + i * angleStep;
    return {
      x: cx + (value / 100) * r * Math.cos(angle),
      y: cy + (value / 100) * r * Math.sin(angle),
    };
  };

  const values = DIMS.map((d) => ((score as unknown as Record<string, number>)[d.key] ?? 0) * 100);
  const points = values.map((v, i) => getPoint(i, v));
  const polyPoints = points.map((p) => `${p.x},${p.y}`).join(" ");

  const labelR = size * 0.44;
  const labelPoints = DIMS.map((_, i) => {
    const angle = -Math.PI / 2 + i * angleStep;
    return {
      x: cx + labelR * Math.cos(angle),
      y: cy + labelR * Math.sin(angle),
    };
  });

  const finalScore = score.final_score ?? 0;
  const typeLabel = t(`depth.type${score.type ?? "B"}`);
  const confidencePct = Math.round((score.confidence ?? 0) * 100);

  return (
    <div style={{ display: "inline-block", textAlign: "center" }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {[0.25, 0.5, 0.75, 1].map((ratio) => (
          <circle
            key={ratio}
            cx={cx}
            cy={cy}
            r={r * ratio}
            fill="none"
            stroke="var(--pf-border-light)"
            strokeWidth={1}
          />
        ))}
        {Array.from({ length: n }).map((_, i) => {
          const p = getPoint(i, 100);
          return (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={p.x}
              y2={p.y}
              stroke="var(--pf-border-light)"
              strokeWidth={1}
            />
          );
        })}
        <polygon
          points={polyPoints}
          fill="rgba(59, 130, 246, 0.25)"
          stroke="var(--pf-primary)"
          strokeWidth={2}
        />
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={3} fill="var(--pf-primary)" />
        ))}
        {DIMS.map((d, i) => {
          const lp = labelPoints[i];
          return (
            <text
              key={d.key}
              x={lp.x}
              y={lp.y}
              textAnchor="middle"
              dominantBaseline="middle"
              style={{ fontSize: 11, fill: "var(--pf-text-secondary)" }}
            >
              {t(d.labelKey)}
            </text>
          );
        })}
      </svg>
      <div style={{ marginTop: 4, fontSize: 13, color: "var(--pf-text-primary)" }}>
        <span style={{ fontWeight: 700, fontSize: 16 }}>{finalScore.toFixed(1)}</span>
        <span style={{ marginLeft: 8, color: "var(--pf-text-muted)" }}>
          {typeLabel} · {t("depth.confidence")}: {confidencePct}%
        </span>
      </div>
    </div>
  );
}
