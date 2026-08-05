import { memo } from "react";
import { useTranslation } from "react-i18next";

interface WordCountBadgeProps {
  /** 字数 */
  count: number;
  /** 字号（默认 11） */
  size?: number;
}

/**
 * 字数小标签 —— 用于大纲树节点右侧显示章节字数。
 *
 * - count <= 0 时不渲染（避免空标签挤占空间）
 * - 超过 1000 字以「1.2k」形式简写
 */
function WordCountBadgeBase({ count, size = 11 }: WordCountBadgeProps) {
  const { t } = useTranslation();
  if (count <= 0) return null;
  const text = count >= 1000 ? `${(count / 1000).toFixed(1)}k` : String(count);
  return (
    <span
      className="pf-word-badge"
      style={{ fontSize: size }}
      title={t("version.wordCount", { count })}
    >
      {text}
    </span>
  );
}

const WordCountBadge = memo(WordCountBadgeBase);
export default WordCountBadge;
