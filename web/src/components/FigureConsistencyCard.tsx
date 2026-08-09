import { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Card, Progress, Space, Tag, Typography, Button } from "antd";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { FinalVerdictV4, EvidenceItem } from "@/api/depth";
import EvidenceSeverityCard from "./EvidenceSeverityCard";
import { EVIDENCE_COLORS } from "./EvidenceSeverityCard.utils";

const { Text } = Typography;

interface FigureConsistencyCardProps {
  finalVerdict: FinalVerdictV4;
  evidencePool?: EvidenceItem[] | null;
  activeEvidenceIds?: string[];
  onEvidenceClick?: (id: string) => void;
}

function M0Status({ active }: { active: boolean }) {
  const { t } = useTranslation();
  return (
    <div
      style={{
        marginTop: 12,
        padding: 8,
        background: "var(--pf-bg-secondary)",
        borderRadius: 6,
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}
    >
      <Tag color={active ? "blue" : "default"}>
        {active
          ? t("depth.figureVectorRender", "矢量渲染已启用")
          : t("depth.figureVectorRenderDisabled", "矢量渲染已关闭")}
      </Tag>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {active
          ? t(
              "depth.figureVectorRenderHint",
              "M0 矢量渲染兜底已开启，matplotlib 等矢量图会被抽取并参与图文一致性分析。",
            )
          : t(
              "depth.figureVectorRenderDisabledHint",
              "M0 矢量渲染兜底已关闭，仅抽取 PDF 中的嵌入位图。",
            )}
      </Text>
    </div>
  );
}

export default function FigureConsistencyCard({
  finalVerdict,
  evidencePool,
  activeEvidenceIds = [],
  onEvidenceClick,
}: FigureConsistencyCardProps) {
  const { t } = useTranslation();
  const coverage = finalVerdict.figure_coverage;
  const m0Active = finalVerdict.m0_active ?? false;

  if (!coverage) {
    return null;
  }

  if (coverage === "disabled") {
    return (
      <Card title={t("depth.figureConsistency", "图表一致性")} style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Tag color="default">{t("depth.figureDisabled", "已禁用")}</Tag>
          <Text type="secondary">
            {t(
              "depth.figureDisabledHint",
              "图表证据功能已关闭，DEPTH 审稿将不使用论文中的实验图表信息。"
            )}
          </Text>
        </div>
        <M0Status active={m0Active} />
      </Card>
    );
  }

  return (
    <Card
      title={t("depth.figureConsistency", "图表一致性")}
      style={{ marginBottom: 16 }}
      extra={
        <Tag
          color={
            coverage === "analyzed" ? "green" : coverage === "missing" ? "orange" : "default"
          }
        >
          {coverage === "analyzed"
            ? t("depth.figureAnalyzed", "已分析")
            : coverage === "missing"
              ? t("depth.figureMissing", "无图表数据")
              : t("depth.figureDisabled", "已禁用")}
        </Tag>
      }
    >
      {typeof finalVerdict.figure_consistency_score === "number" && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
            <Text type="secondary">{t("depth.figureScore", "图文一致度")}</Text>
            <Text strong>
              {(finalVerdict.figure_consistency_score * 100).toFixed(0)}%
            </Text>
          </div>
          <Progress
            percent={Math.round(finalVerdict.figure_consistency_score * 100)}
            strokeColor="#0891b2"
            showInfo={false}
            size="small"
          />
        </div>
      )}

      {finalVerdict.figure_flags && finalVerdict.figure_flags.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <Text strong style={{ display: "block", marginBottom: 8 }}>
            {t("depth.figureFlags", "不一致标记 ({{count}})", {
              count: finalVerdict.figure_flags.length,
            })}
          </Text>
          <Space size={[0, 8]} wrap>
            {finalVerdict.figure_flags.map((flag, i) => (
              <Tag key={i} color="warning">
                {flag}
              </Tag>
            ))}
          </Space>
        </div>
      )}

      <FigureEvidenceList
        evidencePool={evidencePool}
        t={t}
        activeEvidenceIds={activeEvidenceIds}
        onEvidenceClick={onEvidenceClick}
      />


      {typeof finalVerdict.figure_evidence_count === "number" && (
        <Text type="secondary" style={{ display: "block", marginTop: 12, fontSize: 12 }}>
          {t("depth.figureEvidenceCount", "图表证据引用：{{count}} 条", {
            count: finalVerdict.figure_evidence_count,
          })}
        </Text>
      )}

      <M0Status active={m0Active} />
    </Card>
  );
}

function FigureEvidenceList({
  evidencePool,
  t,
  activeEvidenceIds,
  onEvidenceClick,
}: {
  evidencePool: EvidenceItem[] | null | undefined;
  t: TFunction<"translation", "translation">;
  activeEvidenceIds: string[];
  onEvidenceClick?: (id: string) => void;
}) {
  const { i18n } = useTranslation();
  const figureEvidence = (evidencePool || []).filter(
    (item) => item.section === "Figures",
  );

  if (figureEvidence.length === 0) {
    return null;
  }

  const isEnglish = i18n.language.startsWith("en");

  return (
    <div style={{ marginTop: 12 }}>
      <Text strong style={{ display: "block", marginBottom: 8 }}>
        {t("depth.figureEvidence", "图表证据 ({{count}})", {
          count: figureEvidence.length,
        })}
      </Text>
      <div
        data-testid="severity-legend"
        style={{
          marginBottom: 12,
          padding: 8,
          background: "var(--pf-bg-secondary)",
          borderRadius: 4,
        }}
      >
        <Space size={16} wrap>
          <Space size={4}>
            <span
              aria-hidden="true"
              style={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                background: EVIDENCE_COLORS.fatal.border,
              }}
            />
            <Text strong>{t("depth.evidence.fatal", "致命")}</Text>
            <Text type="secondary">
              {t(
                "depth.evidence.fatalDescription",
                "图文一致性存在严重冲突",
              )}
            </Text>
          </Space>
          <Space size={4}>
            <span
              aria-hidden="true"
              style={{
                width: 10,
                height: 10,
                borderRadius: "50%",
                background: EVIDENCE_COLORS.minor.border,
              }}
            />
            <Text strong>{t("depth.evidence.minor", "轻微")}</Text>
            <Text type="secondary">
              {t(
                "depth.evidence.minorDescription",
                "图文一致性存在轻微偏差，建议复核",
              )}
            </Text>
          </Space>
        </Space>
      </div>
      <Space orientation="vertical" style={{ width: "100%" }} size={8}>
        {figureEvidence.map((item) => (
          <FigureEvidenceItem
            key={item.id}
            item={item}
            isEnglish={isEnglish}
            isHighlighted={activeEvidenceIds.includes(item.id)}
            onEvidenceClick={onEvidenceClick}
          />
        ))}
      </Space>
    </div>
  );
}

function FigureEvidenceItem({
  item,
  isEnglish,
  isHighlighted,
  onEvidenceClick,
}: {
  item: EvidenceItem;
  isEnglish: boolean;
  isHighlighted: boolean;
  onEvidenceClick?: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { t } = useTranslation();

  const displayContent = isEnglish
    ? item.contentEn || item.content
    : item.contentZh || item.content;
  const altContent = isEnglish ? item.contentZh : item.contentEn;

  return (
    <EvidenceSeverityCard
      as="div"
      severity={item.severity}
      highlighted={isHighlighted}
      showIcon
      id={`figure-evidence-${item.id}`}
      data-testid={`evidence-item-${item.id}`}
      data-active={isHighlighted}
      tabIndex={onEvidenceClick ? 0 : undefined}
      onClick={() => onEvidenceClick?.(item.id)}
      onKeyDown={(e) => {
        if (!onEvidenceClick) return;
        if ((e.target as HTMLElement).closest("button")) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onEvidenceClick(item.id);
        }
      }}
    >
      <div style={{ display: "flex", gap: 12, alignItems: "stretch" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Space style={{ marginBottom: 4 }} wrap>
            <Tag
              color={item.severity === "fatal" ? "red" : "orange"}
              style={{ fontWeight: 600 }}
            >
              {item.id}
            </Tag>
            <Tag
              color={item.severity === "fatal" ? "red" : "orange"}
              style={{ fontWeight: 600 }}
            >
              {item.severity === "fatal"
                ? t("depth.evidence.fatal", "致命")
                : t("depth.evidence.minor", "轻微")}
            </Tag>
          </Space>
          <Text style={{ display: "block" }}>{displayContent}</Text>

          {expanded && (
            <div
              data-testid={`evidence-detail-${item.id}`}
              style={{
                marginTop: 12,
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
              onClick={(e) => e.stopPropagation()}
            >
              {item.section && (
                <div>
                  <Text type="secondary" style={{ marginRight: 8 }}>
                    {t("depth.evidence.section", "来源章节:")}
                  </Text>
                  <Text>{item.section}</Text>
                </div>
              )}
              {item.keywords && item.keywords.length > 0 && (
                <div>
                  <Text
                    type="secondary"
                    style={{
                      marginRight: 8,
                      display: "inline-block",
                      marginBottom: 4,
                    }}
                  >
                    {t("depth.evidence.keywords", "关键词:")}
                  </Text>
                  <Space size={[0, 4]} wrap>
                    {item.keywords.map((kw) => (
                      <Tag key={kw} style={{ margin: 0 }}>
                        {kw}
                      </Tag>
                    ))}
                  </Space>
                </div>
              )}
              {altContent && altContent !== displayContent && (
                <div>
                  <Text type="secondary" style={{ marginRight: 8 }}>
                    {t("depth.evidence.altContent", "原文对照:")}
                  </Text>
                  <Text>{altContent}</Text>
                </div>
              )}
            </div>
          )}
        </div>
        <Button
          type="text"
          size="small"
          data-testid={`expand-btn-${item.id}`}
          icon={expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          style={{ marginTop: "auto", alignSelf: "flex-end", flexShrink: 0 }}
          onClick={(e) => {
            e.stopPropagation();
            setExpanded(!expanded);
          }}
          aria-label={
            expanded
              ? t("depth.evidence.collapse", "收起详情")
              : t("depth.evidence.expand", "展开详情")
          }
        />
      </div>
    </EvidenceSeverityCard>
  );
}
