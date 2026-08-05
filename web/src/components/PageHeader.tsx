import { Typography } from "antd";
import type { ReactNode } from "react";

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  kicker?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

/** Consistent semantic heading rhythm for non-home routes. */
export default function PageHeader({
  title,
  description,
  kicker,
  actions,
  className = "",
}: PageHeaderProps) {
  return (
    <header className={`pf-page-header ${className}`.trim()}>
      <div className="pf-page-header-copy">
        {kicker && <div className="pf-page-header-kicker">{kicker}</div>}
        <Typography.Title level={1} className="pf-page-header-title">
          {title}
        </Typography.Title>
        {description && <div className="pf-page-header-description">{description}</div>}
      </div>
      {actions && <div className="pf-page-header-actions">{actions}</div>}
    </header>
  );
}
