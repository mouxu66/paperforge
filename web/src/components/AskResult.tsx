import { HelpCircle, FileText, ArrowRight } from "lucide-react";
import { Typography, Card, Space, Tag, Empty, Spin } from "antd";

import { useTranslation } from "react-i18next";
import type { AskResponse, AskReference } from "@/api/types";
import ReferenceCard from "./ReferenceCard";

const { Paragraph } = Typography;

interface AskResultProps {
  result: AskResponse | null;
  loading: boolean;
  onNavigate: (path: string) => void;
  /** 流式问答：正在生成的文本 */
  streamingText?: string;
  /** 流式问答：已收到的参考文献 */
  streamingRefs?: AskReference[];
  /** 是否处于流式输出中 */
  streaming?: boolean;
  /** 空状态下点击示例问法：把问题填进输入框（不直接发起请求） */
  onPickExample?: (question: string) => void;
}

/** 结果展示区：加载中 / 流式输出 / 回答+参考文献 / 空状态 */
export default function AskResult({
  result,
  loading,
  onNavigate,
  streamingText = "",
  streamingRefs = [],
  streaming = false,
  onPickExample,
}: AskResultProps) {
  const { t } = useTranslation();
  // 加载中（RAG 检索阶段）
  if (loading) {
    return (
      <Card
        className="pf-glass-card pf-ask-loading"
        variant="borderless"
        style={{ textAlign: "center", padding: 48 }}
      >
        <Spin size="large" />
        <div style={{ marginTop: 16, color: "var(--pf-text-muted)", fontSize: 14 }}>
          {t("ask.retrievingAndGenerating")}
        </div>
      </Card>
    );
  }

  // 流式输出中（打字机效果）
  if (streaming) {
    return (
      <>
        <Card
          className="pf-glass-card pf-ask-result pf-ask-result--streaming"
          variant="borderless"
          style={{ marginBottom: 20, padding: 8 }}
          title={
            <Space>
              <span className="pf-ask-result-icon">
                <HelpCircle />
              </span>
              <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
                {t("ask.answer")}
              </span>
              <span style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }}>
                {t("ask.generating")}
              </span>
            </Space>
          }
        >
          <Paragraph
            style={{
              color: "var(--pf-text-secondary)",
              fontSize: 15,
              lineHeight: 1.9,
              textAlign: "justify",
              margin: 0,
              whiteSpace: "pre-wrap",
            }}
          >
            {streamingText}
            <span className="pf-streaming-cursor" />
          </Paragraph>
        </Card>

        {streamingRefs.length > 0 && (
          <div>
            <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
              <FileText style={{ color: "var(--pf-primary)" }} />
              <span
                className="pf-serif"
                style={{ fontSize: 16, fontWeight: 600, color: "var(--pf-text-primary)" }}
              >
                {t("ask.references")}
              </span>
              <Tag color="blue" style={{ marginInlineStart: 0 }}>
                {streamingRefs.length}
              </Tag>
            </div>
            {streamingRefs.map((refItem, idx) => (
              <ReferenceCard
                key={refItem.id || idx}
                refItem={refItem}
                index={idx}
                onClick={() => onNavigate(`/paper/${refItem.id}`)}
              />
            ))}
          </div>
        )}
      </>
    );
  }

  // 有结果
  if (result) {
    return (
      <>
        <Card
          className="pf-glass-card pf-ask-result"
          variant="borderless"
          style={{ marginBottom: 20, padding: 8 }}
          title={
            <Space>
              <span className="pf-ask-result-icon">
                <HelpCircle />
              </span>
              <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
                {t("ask.answer")}
              </span>
            </Space>
          }
        >
          <Paragraph
            style={{
              color: "var(--pf-text-secondary)",
              fontSize: 15,
              lineHeight: 1.9,
              textAlign: "justify",
              margin: 0,
              whiteSpace: "pre-wrap",
            }}
          >
            {result.answer}
          </Paragraph>
        </Card>

        {result.references && result.references.length > 0 ? (
          <div>
            <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 8 }}>
              <FileText style={{ color: "var(--pf-primary)" }} />
              <span
                className="pf-serif"
                style={{ fontSize: 16, fontWeight: 600, color: "var(--pf-text-primary)" }}
              >
                {t("ask.references")}
              </span>
              <Tag color="blue" style={{ marginInlineStart: 0 }}>
                {result.references.length}
              </Tag>
            </div>
            {result.references.map((refItem, idx) => (
              <ReferenceCard
                key={refItem.id || idx}
                refItem={refItem}
                index={idx}
                onClick={() => onNavigate(`/paper/${refItem.id}`)}
              />
            ))}
          </div>
        ) : (
          <Empty description={t("ask.noReferences")} />
        )}
      </>
    );
  }

  // 空状态：不是一个"什么都没有"的白框，而是一组可直接点击的示例问法。
  // 此前这里只有问号 + 两行说明，占掉一大块高度却没有任何可点击出口。
  // 点击示例只把问题填进输入框（不直接发起请求），避免误触产生模型调用。
  const exampleKeys = ["exampleQ1", "exampleQ2", "exampleQ3", "exampleQ4"] as const;

  return (
    <Card className="pf-glass-card pf-ask-empty" variant="borderless">
      <div className="pf-ask-empty-head">
        <HelpCircle style={{ fontSize: 32, color: "var(--pf-text-placeholder)" }} />
        <div className="pf-serif pf-ask-empty-title">{t("ask.askLibraryHint")}</div>
        <div className="pf-ask-empty-desc">{t("ask.askLibraryDesc")}</div>
      </div>

      {onPickExample && (
        <div className="pf-ask-empty-examples">
          <div className="pf-ask-empty-examples-label">{t("ask.examplesLabel")}</div>
          {exampleKeys.map((key) => {
            const question = t(`ask.${key}`);
            return (
              <button
                type="button"
                key={key}
                className="pf-ask-example"
                onClick={() => onPickExample(question)}
              >
                <span className="pf-ask-example-text">{question}</span>
                <ArrowRight size={14} aria-hidden="true" />
              </button>
            );
          })}
        </div>
      )}
    </Card>
  );
}
