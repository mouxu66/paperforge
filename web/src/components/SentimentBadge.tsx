import { Tag, Tooltip } from "antd";
import { useTranslation } from "react-i18next";

interface CitationSentimentCounts {
  support: number;
  criticize: number;
  background: number;
}

interface SentimentBadgeProps {
  size?: "small" | "default";
  /** 被引情感统计（WP-2.2） */
  citationCounts?: CitationSentimentCounts;
}

/**
 * 被引情感徽章。
 * 展示 support / criticize / background 三类被引情感统计。
 */
export default function SentimentBadge({ size = "default", citationCounts }: SentimentBadgeProps) {
  const { t } = useTranslation();

  if (!citationCounts) {
    return null;
  }

  const total = citationCounts.support + citationCounts.criticize + citationCounts.background;
  if (total === 0) {
    return null;
  }

  const dominant: "support" | "criticize" | "background" =
    citationCounts.support >= citationCounts.criticize
      ? citationCounts.support >= citationCounts.background
        ? "support"
        : "background"
      : citationCounts.criticize >= citationCounts.background
        ? "criticize"
        : "background";

  const colorMap = {
    support: "success",
    criticize: "error",
    background: "default",
  } as const;

  const tooltipTitle = (
    <div style={{ lineHeight: 1.6 }}>
      <div>{t("paper.citationSentiment", "被引情感")}</div>
      <div>
        {t("paper.supportSentiment", "支持")}: {citationCounts.support}
      </div>
      <div>
        {t("paper.criticizeSentiment", "批评")}: {citationCounts.criticize}
      </div>
      <div>
        {t("paper.backgroundSentiment", "背景引用")}: {citationCounts.background}
      </div>
    </div>
  );

  return (
    <Tooltip title={tooltipTitle}>
      <Tag
        color={colorMap[dominant]}
        style={{
          fontSize: size === "small" ? 11 : 12,
          lineHeight: size === "small" ? "18px" : "20px",
          margin: 0,
        }}
        variant="filled"
      >
        {t(`paper.${dominant}Sentiment`, dominant)} {total}
      </Tag>
    </Tooltip>
  );
}
