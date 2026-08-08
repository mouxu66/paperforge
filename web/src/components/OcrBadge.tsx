import { Tag, Tooltip } from "antd";
import { useTranslation } from "react-i18next";

interface OcrBadgeProps {
  ocrStatus?: string | null;
  isScanned?: boolean | null;
  showLabel?: boolean;
}

export default function OcrBadge({ ocrStatus, isScanned, showLabel = true }: OcrBadgeProps) {
  const { t } = useTranslation();

  // 未解析过 / 无扫描件信息：不显示任何徽章
  if (isScanned == null && ocrStatus == null) return null;

  // 扫描件且 OCR 失败
  if (isScanned && ocrStatus === "failed") {
    return (
      <Tooltip title={t("paper.ocrFailedTooltip", "OCR 识别失败，可进入详情页重试")}>
        <Tag
          color="red"
          style={{ marginInlineEnd: 4, fontSize: 11, lineHeight: "18px", borderRadius: 10 }}
          variant="filled"
        >
          ⚠️ {showLabel && t("paper.ocrFailed", "OCR 失败")}
        </Tag>
      </Tooltip>
    );
  }

  // 扫描件且 OCR 成功
  if (isScanned && ocrStatus === "done") {
    return (
      <Tooltip title={t("paper.scannedTooltip", "扫描版 PDF，已通过 OCR 提取文本")}>
        <Tag
          color="orange"
          style={{ marginInlineEnd: 4, fontSize: 11, lineHeight: "18px", borderRadius: 10 }}
          variant="filled"
        >
          🔍 {showLabel && t("paper.scanned", "扫描件")}
        </Tag>
      </Tooltip>
    );
  }

  // 扫描件但还在识别中
  if (isScanned && ocrStatus === "pending") {
    return (
      <Tooltip title={t("paper.ocrPendingTooltip", "正在识别扫描版 PDF")}>
        <Tag
          color="blue"
          style={{ marginInlineEnd: 4, fontSize: 11, lineHeight: "18px", borderRadius: 10 }}
          variant="filled"
        >
          ⏳ {showLabel && t("paper.ocrPending", "识别中")}
        </Tag>
      </Tooltip>
    );
  }

  // 非扫描件且 OCR 完成（普通可提取文本）
  if (!isScanned && ocrStatus === "done") {
    return null;
  }

  return null;
}
