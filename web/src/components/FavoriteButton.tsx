import { Star } from "lucide-react";
import { useState } from "react";
import { Button, Tooltip } from "antd";

import { useTranslation } from "react-i18next";
import type { Paper } from "@/api/types";
import { useFavoriteStore } from "@/store/useFavoriteStore";

interface FavoriteButtonProps {
  paper: Paper;
  /** 按钮样式：text=纯图标（卡片用），primary=带背景色（详情页用） */
  variant?: "text" | "primary";
  /** 按钮尺寸 */
  size?: "small" | "middle";
  /** 是否显示文字标签（"已收藏"/"收藏"），默认 false */
  showLabel?: boolean;
  /** 是否阻止事件冒泡（卡片场景需要，避免触发卡片跳转） */
  stopPropagation?: boolean;
}

/**
 * 收藏按钮：封装星标图标 + 心跳动画 + store 读写。
 * 替代 PaperCard / DetailPage 中重复的 starAnim 逻辑。
 */
export default function FavoriteButton({
  paper,
  variant = "text",
  size = "small",
  showLabel = false,
  stopPropagation = false,
}: FavoriteButtonProps) {
  const toggle = useFavoriteStore((s) => s.toggle);
  const favorited = useFavoriteStore((s) => s.items.some((p) => p.id === paper.id));
  const [starAnim, setStarAnim] = useState(false);
  const { t } = useTranslation();

  const handleClick = (e: React.MouseEvent) => {
    if (stopPropagation) e.stopPropagation();
    setStarAnim(true);
    toggle(paper);
    window.setTimeout(() => setStarAnim(false), 420);
  };

  const starIcon = favorited ? (
    <Star
      className={starAnim ? "pf-star-anim" : ""}
      fill="#faad14"
      style={{ color: "#faad14", fontSize: 16 }}
    />
  ) : (
    <Star
      className={starAnim ? "pf-star-anim" : ""}
      style={{ color: "#d9d9d9", fontSize: 16 }}
    />
  );

  return (
    <Tooltip title={favorited ? t("paper.unfavorite") : t("paper.favorite")}>
      <Button
        type={variant === "primary" ? "primary" : "text"}
        ghost={variant === "primary" && !favorited}
        size={size}
        icon={starIcon}
        onClick={handleClick}
      >
        {showLabel ? (favorited ? t("paper.favorited") : t("paper.favorite")) : null}
      </Button>
    </Tooltip>
  );
}
