/**
 * ReflectionRadar 组件：感悟能力 6 维雷达图（能力 4 维 + 交叉评价 2 维）
 *
 * 设计碰撞 DepthRadar 的 SVG 模式——同心圆 + 轴线 + 多边形 + 端点圆 +
 * 外围轴标签。4 个轴正好对应 ReflectionScores 的 4 个 0~1 维度。
 *
 * 视觉差异（避免与 DepthRadar 混淆）：
 * - 颜色使用 indigo (#6366f1) 而非 blue (#3b82f6)，与“感悟”品牌色一致
 * - 紧凑尺寸 (默认 110px)，适合塞进表格单元格
 * - 下方显示 avg% 标识均值（与 DepthRadar 的 final_score + type + confidence 不同）
 *
 * 防越界：value 通过 Math.max(0, Math.min(1, v)) clamp，防越界数据爆炸
 *
 * @author PaperForge
 */
import { useTranslation } from "react-i18next";
import { Tooltip } from "antd";
import type { ReflectionScores } from "@/api/types";

const DIMS = [
  { key: "understanding_accuracy", labelKey: "reflection.dims.understanding" },
  { key: "analysis_depth", labelKey: "reflection.dims.analysis" },
  { key: "innovative_insights", labelKey: "reflection.dims.innovation" },
  { key: "evidence_support", labelKey: "reflection.dims.evidence" },
  { key: "fidelity", labelKey: "reflection.dims.fidelity" },
  { key: "coverage", labelKey: "reflection.dims.coverage" },
] as const;

interface Props {
  /** 4 维分数（0~1）；null/undefined 时降级为全 0，避免雷达炸开 */
  scores: ReflectionScores | null | undefined;
  /** SVG 像素尺寸（正方形）；推荐 100~140 用于表格 */
  size?: number;
  /** hover tooltip 是否显示明细数值 */
  showDetail?: boolean;
}

/** 类型守卫：禁止论文评分对象（DepthScore）误入报告雷达。 */
function isReflectionScores(value: unknown): value is ReflectionScores {
  if (typeof value !== "object" || value === null) return false;
  const s = value as Record<string, unknown>;
  return (
    (typeof s.understanding_accuracy === "number" || s.understanding_accuracy === null) &&
    (typeof s.analysis_depth === "number" || s.analysis_depth === null) &&
    (typeof s.innovative_insights === "number" || s.innovative_insights === null) &&
    (typeof s.evidence_support === "number" || s.evidence_support === null)
  );
}

export default function ReflectionRadar({ scores, size = 120, showDetail = true }: Props) {
  const { t } = useTranslation();

  if (scores != null && !isReflectionScores(scores)) {
    return (
      <div style={{ display: "inline-block", textAlign: "center", color: "#ef4444", fontSize: 12 }}>
        报告雷达数据格式错误
      </div>
    );
  }

  const cx = size / 2;
  const cy = size / 2;
  const r = size * 0.34;
  const n = DIMS.length;
  const angleStep = (2 * Math.PI) / n;

  // 兑底 null/undefined → 全 0（避免雷达炸开 + 避免 NaN）
  const safe: ReflectionScores = scores ?? {
    understanding_accuracy: 0,
    analysis_depth: 0,
    innovative_insights: 0,
    evidence_support: 0,
    fidelity: 0,
    coverage: 0,
    average: 0,
  };

  // NaN-safe clamp：Math.max(0, Math.min(1, NaN)) 会返回 NaN，
  // 会污染 SVG geometry 产生「NaN,NaN」 多边形点导致整个雷达劫。
  const safe01 = (v: number): number => {
    if (!Number.isFinite(v)) return 0;
    return Math.max(0, Math.min(1, v));
  };

  // 信任入参：values[] 已在 DIMS.map 中 safe01 过，这里不再重复 NaN 防护。
  // 仅保留 0..1 范围截断作为「几何上限」护栏。
  const clamp = (v: number) => Math.max(0, Math.min(1, v));

  // value 范围 0..1（后端 Pydantic Field(ge=0, le=1)）
  const getPoint = (i: number, value: number) => {
    const v = clamp(value);
    const angle = -Math.PI / 2 + i * angleStep;
    return {
      x: cx + v * r * Math.cos(angle),
      y: cy + v * r * Math.sin(angle),
    };
  };

  const values = DIMS.map((d) => safe01(Number((safe as unknown as Record<string, number>)[d.key] ?? 0)));
  const points = values.map((v, i) => getPoint(i, v));
  const polyPoints = points.map((p) => `${p.x},${p.y}`).join(" ");

  // labelR 紧缩到 0.40，English 4 字「Ana./Und./Inn./Evi.」 右侧轴标签
  // 跨度 22px，能在 110px viewport (cx ± 55 = 99) 完整裁内
  const labelR = size * 0.43;
  const labelPoints = DIMS.map((_, i) => {
    const angle = -Math.PI / 2 + i * angleStep;
    return { x: cx + labelR * Math.cos(angle), y: cy + labelR * Math.sin(angle) };
  });

  const avgPct = Math.round(Number(safe.average ?? 0) * 100);

  const radar = (
    <div style={{ display: "inline-block", textAlign: "center", lineHeight: 1.25 }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {/* 4 圈同心环 (25/50/75/100%) */}
        {[0.25, 0.5, 0.75, 1].map((ratio) => (
          <circle
            key={ratio}
            cx={cx}
            cy={cy}
            r={r * ratio}
            fill="none"
            style={{ stroke: "var(--pf-border-light)" }}
            strokeWidth={1}
          />
        ))}
        {/* 4 条轴线 */}
        {Array.from({ length: n }).map((_, i) => {
          const p = getPoint(i, 1);
          return (
            <line
              key={i}
              x1={cx}
              y1={cy}
              x2={p.x}
              y2={p.y}
              style={{ stroke: "var(--pf-border-light)" }}
              strokeWidth={1}
            />
          );
        })}
        {/* 评分多边形（indigo） */}
        <polygon
          points={polyPoints}
          fill="rgba(99, 102, 241, 0.22)"
          stroke="#6366f1"
          strokeWidth={2}
        />
        {/* 4 个端点圆 */}
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={2.5} fill="#6366f1" />
        ))}
        {/* 4 个轴标签 */}
        {DIMS.map((d, i) => {
          const lp = labelPoints[i];
          return (
            <text
              key={d.key}
              x={lp.x}
              y={lp.y}
              textAnchor="middle"
              dominantBaseline="middle"
              style={{
                fontSize: Math.max(9, size * 0.09),
                fill: "var(--pf-text-secondary)",
                fontWeight: 500,
                pointerEvents: "none",
              }}
            >
              {t(d.labelKey)}
            </text>
          );
        })}
      </svg>
      <div style={{ marginTop: 2, fontSize: 11, color: "var(--pf-text-muted)" }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "#4338ca" }}>{avgPct}%</span>
        <span style={{ marginLeft: 4 }}>{t("reflection.dimsAvg")}</span>
      </div>
    </div>
  );

  if (!showDetail) return radar;

  // 包裹 Tooltip，便于在表格中 hover 看 4 维明细
  const detail = (
    <div style={{ fontSize: 12, lineHeight: 1.7 }}>
      {DIMS.map((d, i) => (
        <div key={d.key}>
          {t(d.labelKey)}：{Math.round(values[i] * 100)}%
        </div>
      ))}
      <div style={{ borderTop: "1px solid var(--pf-border-light)", marginTop: 4, paddingTop: 4 }}>
        {t("reflection.dimsAvg")}：{avgPct}%
      </div>
    </div>
  );

  return <Tooltip title={detail}>{radar}</Tooltip>;
}
