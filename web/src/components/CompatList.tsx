import type { CSSProperties, HTMLAttributes, ReactNode } from "react";

export interface CompatListProps<T> extends HTMLAttributes<HTMLDivElement> {
  dataSource?: T[];
  renderItem?: (item: T, index: number) => ReactNode;
  /** Stable keys prevent input state from jumping when a row is removed. */
  rowKey?: keyof T | ((item: T) => string | number);
  size?: "small" | "default" | "large";
  bordered?: boolean;
}

interface CompatListItemProps extends HTMLAttributes<HTMLDivElement> {
  actions?: ReactNode[];
}

function CompatListItem({ actions, children, className, style, ...rest }: CompatListItemProps) {
  return (
    <div
      {...rest}
      className={["pf-compat-list-item", className].filter(Boolean).join(" ")}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        ...style,
      }}
    >
      <div style={{ minWidth: 0, flex: 1 }}>{children}</div>
      {actions && actions.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {actions}
        </div>
      )}
    </div>
  );
}

function CompatListMeta({
  title,
  description,
}: {
  title?: ReactNode;
  description?: ReactNode;
}) {
  return (
    <div className="pf-compat-list-meta">
      {title && <div className="pf-compat-list-meta-title">{title}</div>}
      {description && <div className="pf-compat-list-meta-description">{description}</div>}
    </div>
  );
}

export default function CompatList<T>({
  dataSource = [],
  renderItem,
  rowKey,
  size = "default",
  bordered = false,
  className,
  style,
  ...rest
}: CompatListProps<T>) {
  const sizeStyle: CSSProperties =
    size === "small" ? { fontSize: 13 } : size === "large" ? { fontSize: 16 } : {};

  return (
    <div
      {...rest}
      className={["pf-compat-list", bordered ? "pf-compat-list-bordered" : "", className]
        .filter(Boolean)
        .join(" ")}
      style={{ ...sizeStyle, ...style }}
    >
      {dataSource.map((item, index) => {
        const key =
          typeof rowKey === "function"
            ? rowKey(item)
            : rowKey
              ? String(item[rowKey])
              : typeof item === "object" && item !== null && "id" in item
                ? String((item as { id: string | number }).id)
                : index;
        return <div key={key}>{renderItem?.(item, index)}</div>;
      })}
    </div>
  );
}

const List = Object.assign(CompatList, {
  Item: Object.assign(CompatListItem, { Meta: CompatListMeta }),
});

export { List };
