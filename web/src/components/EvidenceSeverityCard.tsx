import * as React from "react";
import { AlertCircle } from "lucide-react";
import {
  EVIDENCE_COLORS,
  getEvidenceSeverityStyles,
  type EvidenceSeverity,
} from "./EvidenceSeverityCard.utils";

export interface EvidenceSeverityCardProps {
  severity?: EvidenceSeverity | string | null;
  highlighted?: boolean;
  children: React.ReactNode;
  /** 是否在 fatal 时显示右上角 AlertCircle 图标（仅 card 模式生效） */
  showIcon?: boolean;
  onClick?: React.MouseEventHandler<HTMLElement>;
  onKeyDown?: React.KeyboardEventHandler<HTMLElement>;
  className?: string;
  style?: React.CSSProperties;
  /** 渲染元素类型，默认 div，在 antd Table 中可传 "tr" */
  as?: React.ElementType;
  "data-testid"?: string;
  "data-active"?: boolean;
  "data-severity"?: string;
  id?: string;
  role?: string;
  tabIndex?: number;
}

const EvidenceSeverityCard = React.forwardRef<HTMLElement, EvidenceSeverityCardProps>(
  function EvidenceSeverityCard(
    {
      severity,
      highlighted: highlightedProp = false,
      children,
      showIcon = false,
      as: Component = "div",
      style,
      ...rest
    },
    ref,
  ) {
    const isTr = Component === "tr";
    const effectiveSeverity = severity || rest["data-severity"];
    const isFatal = effectiveSeverity === "fatal";
    const highlighted = highlightedProp || rest["data-active"] === true;

    const { bg, border } = getEvidenceSeverityStyles(effectiveSeverity, highlighted);

    const baseStyle: React.CSSProperties = {
      position: isTr ? undefined : "relative",
      cursor: rest.onClick ? "pointer" : undefined,
      background: bg,
      border: highlighted && !isTr
        ? `1px solid ${EVIDENCE_COLORS.highlight.border}`
        : "1px solid transparent",
      // border-left on <tr> is unreliable in antd Table because of
      // border-collapse / cell backgrounds; callers that need a visible left
      // indicator in tables should apply it to the first cell (e.g. onCell).
      borderLeft: isTr ? undefined : `3px solid ${border}`,
      transition: "all 0.3s",
      ...style,
    };

    if (!isTr) {
      baseStyle.padding = 12;
      baseStyle.borderRadius = 4;
      if (showIcon && isFatal) {
        baseStyle.paddingRight = 40;
      }
    }

    return (
      <Component
        {...rest}
        ref={ref}
        data-severity={effectiveSeverity}
        data-active={highlighted}
        style={baseStyle}
      >
        {children}
        {showIcon && isFatal && !isTr && (
          <AlertCircle
            color={EVIDENCE_COLORS.fatal.border}
            size={16}
            aria-label="fatal"
            style={{ position: "absolute", top: 12, right: 12 }}
          />
        )}
      </Component>
    );
  },
);

export default EvidenceSeverityCard;
