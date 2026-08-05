import { Loader, ArrowLeftRight } from "lucide-react";
import { Alert } from "antd";

import type { QwenStatus } from "@/api/qwen";

interface Props {
  status: QwenStatus | null;
}

function formatEta(etaSeconds: number): string {
  if (etaSeconds <= 0) return "";
  const minutes = Math.ceil(etaSeconds / 60);
  return `（预计 ${minutes} 分钟）`;
}

/**
 * DEPTH/OCR 等待期提示：冷启动 / 互斥切换时展示，避免用户误判卡死。
 */
export default function QwenLoadingBanner({ status }: Props) {
  if (!status) return null;
  const { kind, eta_seconds } = status.event;

  if (kind === "loading") {
    return (
      <Alert
        type="info"
        showIcon
        icon={<Loader />}
        message="Qwen 模型加载中"
        description={`llama-server 正在冷启动（CUDA kernel 编译），DEPTH 需等待其就绪。${formatEta(eta_seconds)}完成后自动继续，请勿关闭页面。`}
        style={{ marginBottom: 16 }}
      />
    );
  }

  if (kind === "switching") {
    return (
      <Alert
        type="warning"
        showIcon
        icon={<ArrowLeftRight />}
        message="正在切换模型（显存互斥）"
        description={`OCR 与 Qwen 互斥：一端释放显存，另一端加载中（llama-server 冷启动）。${formatEta(eta_seconds)}完成后自动继续，请勿关闭页面。`}
        style={{ marginBottom: 16 }}
      />
    );
  }

  return null;
}
