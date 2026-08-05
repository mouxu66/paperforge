import type { ReactNode } from "react";

export type ResearchAccentTone = "blue" | "teal" | "amber" | "violet" | "coral";

interface Props {
  icon: ReactNode;
  label: string;
  detail: string;
  tone?: ResearchAccentTone;
  index?: string;
}

/**
 * Decorative page context for high-whitespace research tools.
 * The visual is CSS-only on purpose: no extra asset request, no broken remote image,
 * and the same archive language works in light and dark themes.
 */
export default function ResearchPageAccent({
  icon,
  label,
  detail,
  tone = "blue",
  index = "FIELD NOTE",
}: Props) {
  return (
    <div className={`pf-page-accent pf-page-accent--${tone}`} aria-hidden="true">
      <div className="pf-page-accent-copy">
        <span className="pf-page-accent-kicker">
          <span className="pf-page-accent-kicker-line" />
          {index}
        </span>
        <span className="pf-page-accent-label">
          <span className="pf-page-accent-icon">{icon}</span>
          {label}
        </span>
        <span className="pf-page-accent-detail">{detail}</span>
      </div>
      <div className="pf-page-accent-materials">
        <span className="pf-accent-sheet pf-accent-sheet--back" />
        <span className="pf-accent-sheet pf-accent-sheet--mid">
          <span className="pf-accent-sheet-lines" />
          <span className="pf-accent-sheet-stamp" />
        </span>
        <span className="pf-accent-sheet pf-accent-sheet--front">
          <span className="pf-accent-front-mark">{icon}</span>
          <span className="pf-accent-front-lines" />
        </span>
        <span className="pf-accent-orbit pf-accent-orbit--one" />
        <span className="pf-accent-orbit pf-accent-orbit--two" />
      </div>
    </div>
  );
}
