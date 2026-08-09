import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  Alert,
  Card,
  Collapse,
  Empty,
  Image,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import type { PaperFigure } from "@/api/types";
import { fetchPaperFigures } from "@/api/papers";

const { Text, Title } = Typography;

interface FigureDetailsListProps {
  paperId: string;
}

type ClaimItem = {
  metric?: string;
  value?: number | string;
  operator?: string;
  comparator?: string;
  context?: string;
  [key: string]: unknown;
};

type ValidatedItem = {
  valid?: boolean | null;
  metricMatched?: boolean;
  reason?: string;
  axisRange?: string | number;
  actualValue?: number;
  axisMin?: number;
  axisMax?: number;
  expectedRange?: number[];
  curveCorrected?: boolean;
  curveYMin?: number;
  curveYMax?: number;
  [key: string]: unknown;
};

interface ValidatedPair {
  claim: ClaimItem;
  validation: ValidatedItem;
  index: number;
}

export default function FigureDetailsList({ paperId }: FigureDetailsListProps) {
  const { t } = useTranslation();
  const [figures, setFigures] = useState<PaperFigure[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchPaperFigures(paperId)
      .then((data) => {
        if (!active) return;
        setFigures(data);
      })
      .catch((err: Error) => {
        if (!active) return;
        setError(err?.message || t("figureDetails.fetchError", "获取图表详情失败"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [paperId, t]);

  const stats = useMemo(() => {
    let totalClaims = 0;
    let invalidClaims = 0;
    const figuresWithIssues: PaperFigure[] = [];

    figures.forEach((fig) => {
      const pairs = getClaimValidationPairs(fig);
      if (pairs.some((p) => p.validation.valid === false)) {
        figuresWithIssues.push(fig);
      }
      totalClaims += pairs.length;
      invalidClaims += pairs.filter((p) => p.validation.valid === false).length;
    });

    return {
      totalFigures: figures.length,
      figuresWithIssues: figuresWithIssues.length,
      totalClaims,
      invalidClaims,
    };
  }, [figures]);

  if (loading) {
    return <Spin description={t("common.loading")} />;
  }

  if (error) {
    return <Alert type="error" title={error} showIcon />;
  }

  if (figures.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t("figureDetails.noFigures", "暂无图表数据")}
      />
    );
  }

  return (
    <Space orientation="vertical" style={{ width: "100%" }} size="middle">
      <FigureSummary stats={stats} t={t} />

      {figures.map((fig) => {
        const outOfRange = getOutOfRangePairs(fig);
        const pairs = getClaimValidationPairs(fig);
        const hasIssues = outOfRange.length > 0;

        return (
          <Card
            key={fig.id}
            size="small"
            title={
              <Space>
                <Text strong>
                  {t("figureDetails.figureTitle", "图 {{index}}", {
                    index: fig.figureNumber ?? fig.figureIndex + 1,
                  })}
                </Text>
                {hasIssues ? (
                  <Tag icon={<WarningOutlined />} color="error">
                    {t("figureDetails.outOfRange", "{{count}} 条越界", {
                      count: outOfRange.length,
                    })}
                  </Tag>
                ) : pairs.length > 0 ? (
                  <Tag icon={<CheckCircleOutlined />} color="success">
                    {t("figureDetails.allValid", "全部通过")}
                  </Tag>
                ) : null}
              </Space>
            }
          >
            <Space orientation="vertical" style={{ width: "100%" }} size="middle">
              {fig.imageUrl && (
                <div>
                  <Image
                    src={fig.imageUrl}
                    alt={t("figureDetails.figureTitle", "图 {{index}}", {
                      index: fig.figureNumber ?? fig.figureIndex + 1,
                    })}
                    style={{ maxHeight: 200, borderRadius: 4 }}
                  />
                </div>
              )}

              {fig.captionText && (
                <div>
                  <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
                    {t("figureDetails.caption", "图注")}
                  </Text>
                  <CaptionView captionText={fig.captionText} pairs={pairs} />
                </div>
              )}

              {fig.sourceTextSpan && (
                <div>
                  <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
                    {t("figureDetails.sourceTextSpan", "正文引用")}
                  </Text>
                  <Text
                    style={{
                      display: "block",
                      padding: 8,
                      background: "var(--pf-bg-secondary)",
                      borderRadius: 4,
                    }}
                  >
                    {fig.sourceTextSpan}
                  </Text>
                </div>
              )}

              <AxisInfoView axisInfo={fig.axisInfo} t={t} />

              {pairs.length > 0 && (
                <div>
                  <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
                    {t("figureDetails.claimValidation", "数值断言校验")}
                  </Text>
                  <Space orientation="vertical" style={{ width: "100%" }} size="small">
                    {pairs.map((pair, idx) => (
                      <ClaimValidationItem key={idx} pair={pair} t={t} />
                    ))}
                  </Space>
                </div>
              )}

              <Collapse
                size="small"
                items={[
                  {
                    key: "ocr",
                    label: t("figureDetails.ocr", "OCR 文本"),
                    children: (
                      <Text style={{ whiteSpace: "pre-wrap" }}>{fig.ocrText || t("common.none")}</Text>
                    ),
                  },
                  {
                    key: "axisJson",
                    label: t("figureDetails.rawAxisInfo", "原始 axis_info"),
                    children: <pre style={{ margin: 0 }}>{JSON.stringify(fig.axisInfo, null, 2)}</pre>,
                  },
                  {
                    key: "claimJson",
                    label: t("figureDetails.rawClaimValidation", "原始 claim_validation"),
                    children: (
                      <pre style={{ margin: 0 }}>{JSON.stringify(fig.claimValidation, null, 2)}</pre>
                    ),
                  },
                ]}
              />
            </Space>
          </Card>
        );
      })}
    </Space>
  );
}

function AxisInfoView({
  axisInfo,
  t,
}: {
  axisInfo: PaperFigure["axisInfo"];
  t: TFunction;
}) {
  if (!axisInfo) return null;
  const { xLabel, yLabel, xTicks, yTicks, legendItems, captionSummary } = axisInfo;

  const xMin = xTicks && xTicks.length > 0 ? Math.min(...xTicks) : undefined;
  const xMax = xTicks && xTicks.length > 0 ? Math.max(...xTicks) : undefined;
  const yMin = yTicks && yTicks.length > 0 ? Math.min(...yTicks) : undefined;
  const yMax = yTicks && yTicks.length > 0 ? Math.max(...yTicks) : undefined;

  return (
    <div>
      <Text type="secondary" style={{ display: "block", marginBottom: 4 }}>
        {t("figureDetails.axisInfo", "轴信息")}
      </Text>
      <div
        style={{
          padding: 12,
          background: "var(--pf-bg-secondary)",
          borderRadius: 4,
        }}
      >
        <Space orientation="vertical" style={{ width: "100%" }} size={8}>
          {(xLabel || yLabel) && (
            <Space wrap>
              {xLabel && (
                <Tag color="blue">
                  X: {xLabel}
                </Tag>
              )}
              {yLabel && (
                <Tag color="cyan">
                  Y: {yLabel}
                </Tag>
              )}
            </Space>
          )}
          {(xMin !== undefined || yMin !== undefined) && (
            <Space wrap>
              {xMin !== undefined && xMax !== undefined && (
                <Tag color="default">
                  X {t("figureDetails.range", "范围")}: [{xMin}, {xMax}]
                </Tag>
              )}
              {yMin !== undefined && yMax !== undefined && (
                <Tag color="default">
                  Y {t("figureDetails.range", "范围")}: [{yMin}, {yMax}]
                </Tag>
              )}
            </Space>
          )}
          {legendItems && legendItems.length > 0 && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("figureDetails.legend", "图例")}: {legendItems.join(", ")}
            </Text>
          )}
          {captionSummary && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t("figureDetails.summary", "摘要")}: {captionSummary}
            </Text>
          )}
        </Space>
      </div>
    </div>
  );
}

function getOutOfRangePairs(fig: PaperFigure): ValidatedPair[] {
  return getClaimValidationPairs(fig).filter((p) => p.validation.valid === false);
}

/**
 * Split a caption into sentences and highlight those that contain an invalid claim.
 * Matching is done by the claim's original context first, then by metric + value.
 */
function CaptionView({
  captionText,
  pairs,
}: {
  captionText: string;
  pairs: ValidatedPair[];
}) {
  const segments = useMemo(() => {
    // Only invalid claims need to be highlighted in the caption
    const invalidPairs = pairs.filter((p) => p.validation.valid === false);

    const result: { text: string; delimiter: string; highlighted: boolean }[] = [];

    // Split while keeping delimiters: [text, delimiter, text, delimiter, ...]
    const parts = captionText.split(/([.!?]+\s*)/).filter((s) => s.length > 0);

    for (let i = 0; i < parts.length; i += 2) {
      const text = parts[i];
      const delimiter = parts[i + 1] || "";
      const sentence = `${text}${delimiter}`.trim();
      const normalizedSentence = sentence.toLowerCase();

      const highlighted = invalidPairs.some(({ claim }) =>
        matchClaimToSentence(claim, sentence, normalizedSentence),
      );

      result.push({ text, delimiter, highlighted });
    }

    return result;
  }, [captionText, pairs]);

  return (
    <Text
      style={{
        display: "block",
        padding: 8,
        background: "var(--pf-bg-secondary)",
        borderRadius: 4,
        lineHeight: 1.7,
      }}
    >
      {segments.map((seg, idx) => (
        <span
          key={idx}
          data-highlight={seg.highlighted ? "invalid" : undefined}
          style={
            seg.highlighted
              ? {
                  backgroundColor: "#fff1f0",
                  borderBottom: "2px solid #ff4d4f",
                  padding: "0 2px",
                  borderRadius: 2,
                }
              : undefined
          }
        >
          {seg.text}
          {seg.delimiter}
        </span>
      ))}
    </Text>
  );
}

function matchClaimToSentence(
  claim: ClaimItem,
  sentence: string,
  normalizedSentence: string,
): boolean {
  // Prefer exact context match (the sentence the claim was extracted from)
  const context = claim.context;
  if (context && context.trim()) {
    const normalizedContext = context.trim().toLowerCase();
    if (normalizedSentence.includes(normalizedContext)) {
      return true;
    }
  }

  // Fallback: metric and value must both appear in the sentence
  const metric = claim.metric;
  if (metric && metric.trim()) {
    const metricMatches = normalizedSentence.includes(metric.toLowerCase());
    if (!metricMatches) return false;

    const value = claim.value;
    if (value !== undefined && value !== null) {
      const valueStr = String(value);
      const percentValue = `${Number(value) * 100}`;
      return sentence.includes(valueStr) || sentence.includes(percentValue);
    }

    return true;
  }

  return false;
}

function getClaimValidationPairs(fig: PaperFigure): ValidatedPair[] {
  const pairs: ValidatedPair[] = [];
  if (!fig.claimValidation) return pairs;
  const claims = fig.claimValidation.claims || [];
  const validated = fig.claimValidation.validated || [];
  for (let i = 0; i < Math.min(claims.length, validated.length); i++) {
    pairs.push({
      claim: claims[i],
      validation: validated[i],
      index: i,
    });
  }
  return pairs;
}

function formatClaim(claim: ClaimItem, t: TFunction): string {
  const { metric, value, operator, comparator, context } = claim;
  const parts = [metric, value, operator, comparator].filter(
    (v) => v !== undefined && v !== null && v !== "",
  );
  if (parts.length === 0 && context) {
    return String(context).slice(0, 100);
  }
  return parts.length > 0 ? parts.join(" ") : t("figureDetails.unknownClaim");
}

function formatNumberValue(value: unknown): string {
  if (value === undefined || value === null) return "";
  const num = Number(value);
  if (Number.isNaN(num)) return String(value);
  return num.toLocaleString();
}

function ClaimValidationItem({
  pair,
  t,
}: {
  pair: ValidatedPair;
  t: TFunction;
}) {
  const { claim, validation } = pair;
  const isInvalid = validation.valid === false;
  const { metric, value, comparator } = claim;
  const axisRange = validation.axisRange;

  return (
    <div
      style={{
        padding: 10,
        borderRadius: 4,
        background: isInvalid ? "#fff2f0" : "#f6ffed",
        border: `1px solid ${isInvalid ? "#ffccc7" : "#b7eb8f"}`,
      }}
    >
      <Space align="start">
        {isInvalid ? (
          <CloseCircleOutlined style={{ color: "#ff4d4f", marginTop: 4 }} />
        ) : (
          <CheckCircleOutlined style={{ color: "#52c41a", marginTop: 4 }} />
        )}
        <div style={{ flex: 1 }}>
          <div style={{ marginBottom: 4 }}>
            <Text strong>{formatClaim(claim, t)}</Text>
          </div>
          <Space size={[8, 4]} wrap>
            {metric && (
              <Tag color="blue">{String(metric)}</Tag>
            )}
            {value !== undefined && value !== null && (
              <Tag color="default">
                {t("figureDetails.claimValue", "断言值")}: {formatNumberValue(value)}
              </Tag>
            )}
            {comparator !== undefined && comparator !== null && comparator !== "" && (
              <Tag color="default">
                {t("figureDetails.comparator", "比较器")}: {String(comparator)}
              </Tag>
            )}
            {axisRange !== undefined && axisRange !== null && (
              <Tag color="orange">
                {t("figureDetails.axisRange", "轴范围")}: {String(axisRange)}
              </Tag>
            )}
            {Array.isArray(validation.expectedRange) && (
              <Tag color="orange">
                {t("figureDetails.expectedRange", "允许范围")}: {"["}
                {validation.expectedRange.join(", ")}
                {"]"}
              </Tag>
            )}
            {validation.actualValue !== undefined && validation.actualValue !== null && (
              <Tag color="default">
                {t("figureDetails.actualValue", "实际值")}: {formatNumberValue(validation.actualValue)}
              </Tag>
            )}
            {validation.curveCorrected && (
              <Tag color="blue">
                {t("figureDetails.curveCorrected", "曲线修正")}
              </Tag>
            )}
            {validation.reason && (
              <Tag
                color={isInvalid ? "error" : "success"}
                icon={isInvalid ? <ExclamationCircleOutlined /> : undefined}
              >
                {String(validation.reason)}
              </Tag>
            )}
          </Space>
        </div>
      </Space>
    </div>
  );
}

function FigureSummary({
  stats,
  t,
}: {
  stats: {
    totalFigures: number;
    figuresWithIssues: number;
    totalClaims: number;
    invalidClaims: number;
  };
  t: TFunction;
}) {
  const { totalFigures, figuresWithIssues, totalClaims, invalidClaims } = stats;
  const validClaims = totalClaims - invalidClaims;

  return (
    <Card size="small" style={{ background: "var(--pf-bg-secondary)" }}>
      <Space orientation="vertical" style={{ width: "100%" }} size="small">
        <Title level={5} style={{ margin: 0 }}>
          {t("figureDetails.validationSummary", "数值断言校验概览")}
        </Title>
        <Space size="large" wrap>
          <Statistic
            title={t("figureDetails.totalFigures", "图表总数")}
            value={totalFigures}
            styles={{ content: { color: "#1677ff" } }}
          />
          <Statistic
            title={t("figureDetails.figuresWithIssues", "存在不一致的图表")}
            value={figuresWithIssues}
            styles={{ content: { color: figuresWithIssues > 0 ? "#ff4d4f" : "#52c41a" } }}
          />
          <Statistic
            title={t("figureDetails.totalClaims", "断言总数")}
            value={totalClaims}
          />
          <Statistic
            title={t("figureDetails.invalidClaims", "不一致断言")}
            value={invalidClaims}
            styles={{ content: { color: invalidClaims > 0 ? "#ff4d4f" : "#52c41a" } }}
          />
          {totalClaims > 0 && (
            <Statistic
              title={t("figureDetails.validRate", "一致率")}
              value={Math.round((validClaims / totalClaims) * 100)}
              suffix="%"
              styles={{ content: { color: invalidClaims > 0 ? "#fa8c16" : "#52c41a" } }}
            />
          )}
        </Space>
        {invalidClaims > 0 && (
          <Alert
            type="warning"
            showIcon
            title={t(
              "figureDetails.inconsistentWarning",
              "检测到 {{invalidClaims}} 条数值断言与图表 axis 范围不一致，建议复核。",
              { invalidClaims },
            )}
          />
        )}
      </Space>
    </Card>
  );
}
