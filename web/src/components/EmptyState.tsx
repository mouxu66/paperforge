import { Inbox, FileSearch, Image, FileText, Search, BookOpen, Cpu } from "lucide-react";
import { Empty, Button } from "antd";
import { useTranslation } from "react-i18next";
import {
  buildFirstRunGuide,
  buildOcrGuide,
  buildNoModelGuide,
  buildIndexPendingGuide,
} from "./EmptyState.guide";


export type EmptyStateType =
  | "default"
  | "search"
  | "figures"
  | "pdf"
  | "ocr"
  | "favorites"
  | "notes"
  | "citations"
  | "upload"
  | "firstRun"
  | "report"
  /** 未配置模型：生成类能力不可用，但检索/审稿不受影响 */
  | "noModel"
  /** 单篇论文索引未完成：语义检索搜不到它，其余功能正常 */
  | "indexPending";

interface GuideStep {
  icon: React.ReactNode;
  title: string;
  desc: string;
}

interface Action {
  label: string;
  onClick: () => void;
  /** 视觉权重：primary（主按钮）/ default（次按钮）/ text（文字按钮，默认） */
  variant?: "primary" | "default" | "text";
}

interface Props {
  type?: EmptyStateType;
  /** 主标题（粗体，显示在描述上方）。未提供时按 type 取 i18n 默认 */
  title?: string;
  /** 描述文案。未提供时按 type 取 i18n 默认 */
  description?: string;
  /** 主行动按钮（primary） */
  action?: Action;
  /** 次要行动按钮（显示在主按钮旁或下方） */
  secondaryAction?: Action;
  /** 引导步骤列表（仅 firstRun / ocr 等需要分步指引的状态使用） */
  guide?: {
    title: string;
    steps: GuideStep[];
  };
}

const ICONS: Record<EmptyStateType, React.ReactNode> = {
  default: <Inbox />,
  search: <FileSearch />,
  figures: <Image />,
  pdf: <FileText />,
  ocr: <Image />,
  favorites: <Inbox />,
  notes: <Inbox />,
  citations: <Inbox />,
  upload: <Inbox />,
  firstRun: <Search />,
  report: <BookOpen />,
  noModel: <Cpu />,
  indexPending: <FileSearch />,
};


function renderAction(action: Action) {
  const variant = action.variant ?? "primary";
  if (variant === "text") {
    return (
      <Button type="text" size="small" onClick={action.onClick} style={{ padding: "0 8px" }}>
        {action.label}
      </Button>
    );
  }
  return (
    <Button type={variant} onClick={action.onClick}>
      {action.label}
    </Button>
  );
}

export default function EmptyState({
  type = "default",
  title,
  description,
  action,
  secondaryAction,
  guide,
}: Props) {
  const { t } = useTranslation();

  // 上下文化空态：标题与描述按 type 取值。
  // 用映射表而非嵌套三元：新增场景只加一行，且「哪些 type 有专属文案」一眼可见。
  const CONTEXTUAL: Partial<
    Record<EmptyStateType, { titleKey: string; title: string; descKey: string; desc: string }>
  > = {
    firstRun: {
      titleKey: "home.firstRun.title",
      title: "欢迎来到 PaperForge",
      descKey: "home.firstRun.desc",
      desc: "导入或上传论文后，搜索、问答、深度审稿即刻可用",
    },
    search: {
      titleKey: "search.noResultsTitle",
      title: "没有匹配的论文",
      descKey: "search.noResults",
      desc: "没有匹配的论文",
    },
    ocr: {
      titleKey: "search.noOcrResultsTitle",
      title: "语义搜索未返回结果",
      descKey: "search.noOcrResultsDesc",
      desc: "当前论文库尚未完成全文索引，语义检索暂时无结果",
    },
    noModel: {
      titleKey: "noModel.title",
      title: "还没有配置模型",
      descKey: "noModel.desc",
      desc: "问答、综述与写作的生成能力需要一个模型；检索、图检索与深度审稿不依赖它，现在就能用",
    },
    indexPending: {
      titleKey: "indexPending.title",
      title: "该论文的索引尚未完成",
      descKey: "indexPending.desc",
      desc: "语义检索暂时搜不到它；关键词搜索、阅读标注与深度审稿不受影响",
    },
    report: {
      titleKey: "reports.emptyTitle",
      title: "暂无感悟报告",
      descKey: "reports.emptyDesc",
      desc: "点击上方「提交感悟」按钮，或切换到「论文」标签开始导入论文。",
    },
  };

  const contextual = CONTEXTUAL[type];
  const isContextual = Boolean(contextual);
  const defaultTitle = contextual ? t(contextual.titleKey, contextual.title) : undefined;
  const defaultDesc =
    description ??
    (contextual
      ? t(contextual.descKey, contextual.desc)
      : type === "figures"
        ? t("figures.empty")
        : t("common.noData"));

  // 引导步骤：未显式传入时按 type 注入默认步骤
  const effectiveGuide =
    guide ??
    (type === "firstRun"
      ? buildFirstRunGuide(t)
      : type === "ocr"
        ? buildOcrGuide(t)
        : type === "noModel"
          ? buildNoModelGuide(t)
          : type === "indexPending"
            ? buildIndexPendingGuide(t)
            : undefined);

  const icon = ICONS[type];

  return (
    <div
      style={{
        textAlign: "center",
        padding: "48px 16px",
        color: "var(--pf-text-placeholder)",
      }}
    >
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <div style={{ color: "var(--pf-text-secondary)", maxWidth: 480, margin: "0 auto" }}>
            {/* 图标：三态用更大的圆形容器 + 主色边框，其余保持原样 */}
            <div
              style={{
                fontSize: isContextual ? 32 : 24,
                marginBottom: 12,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                width: isContextual ? 64 : undefined,
                height: isContextual ? 64 : undefined,
                borderRadius: isContextual ? "50%" : undefined,
                background: isContextual
                  ? "var(--pf-primary-soft, rgba(22,119,255,0.08))"
                  : undefined,
                border: isContextual ? "1px solid var(--pf-border-light)" : undefined,
                color: isContextual ? "var(--pf-primary)" : "var(--pf-text-placeholder)",
              }}
            >
              {icon}
            </div>

            {/* 主标题（仅三态或显式传入时显示，粗体） */}
            {(title ?? defaultTitle) && (
              <div
                style={{
                  fontSize: 16,
                  fontWeight: 600,
                  color: "var(--pf-text-primary)",
                  marginBottom: 6,
                }}
              >
                {title ?? defaultTitle}
              </div>
            )}

            {/* 描述 */}
            <div
              style={{
                fontSize: 14,
                lineHeight: 1.6,
                marginBottom: effectiveGuide || action || secondaryAction ? 16 : 0,
              }}
            >
              {defaultDesc}
            </div>

            {/* 行动按钮组 */}
            {(action || secondaryAction) && (
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  justifyContent: "center",
                  flexWrap: "wrap",
                  marginBottom: effectiveGuide ? 20 : 0,
                }}
              >
                {action && renderAction(action)}
                {secondaryAction && renderAction(secondaryAction)}
              </div>
            )}

            {/* 引导步骤（首跑/索引缺失等场景） */}
            {effectiveGuide && (
              <div
                style={{
                  textAlign: "left",
                  marginTop: 8,
                  padding: "16px 20px",
                  background: "var(--pf-bg-tertiary)",
                  border: "1px solid var(--pf-border-light)",
                  borderRadius: 10,
                }}
              >
                <div
                  style={{
                    fontSize: 12,
                    fontWeight: 600,
                    color: "var(--pf-text-muted)",
                    marginBottom: 12,
                    textTransform: "uppercase",
                    letterSpacing: 0.5,
                  }}
                >
                  {effectiveGuide.title}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                  {effectiveGuide.steps.map((step, i) => (
                    <div
                      key={i}
                      style={{
                        display: "flex",
                        gap: 12,
                        alignItems: "flex-start",
                        transition: "transform 0.15s ease",
                      }}
                    >
                      <div
                        style={{
                          flexShrink: 0,
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          width: 32,
                          height: 32,
                          borderRadius: 8,
                          background: "var(--pf-primary-soft, rgba(22,119,255,0.08))",
                          color: "var(--pf-primary)",
                          fontSize: 16,
                        }}
                      >
                        {step.icon}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div
                          style={{
                            fontSize: 13,
                            fontWeight: 600,
                            color: "var(--pf-text-primary)",
                            marginBottom: 2,
                          }}
                        >
                          {step.title}
                        </div>
                        <div
                          style={{ fontSize: 12, color: "var(--pf-text-muted)", lineHeight: 1.5 }}
                        >
                          {step.desc}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        }
      />
    </div>
  );
}
