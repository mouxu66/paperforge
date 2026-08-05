import { Loader, Image, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Tag, Tooltip } from "antd";

import { getQwenStatus, type QwenStatus } from "@/api/qwen";

const POLL_INTERVAL = 5000;

function stateColor(state: string, kind: string): string {
  if (kind === "loading" || kind === "switching") return "processing";
  if (state === "ocr_active") return "warning";
  if (state === "qwen_active") return "success";
  return "default";
}

function stateIcon(state: string, kind: string) {
  if (kind === "loading" || kind === "switching") {
    return <Loader className="pf-spin" />;
  }
  if (state === "ocr_active") return <Image />;
  return <Zap />;
}

function stateKey(state: string, kind: string): string {
  if (kind === "loading") return "vramStatus.loading";
  if (kind === "switching") return "vramStatus.switching";
  return `vramStatus.${state}`;
}

/**
 * 顶部导航栏 VRAM 状态指示器：显示当前是 Qwen / OCR / 空闲 / 切换中。
 */
export default function VramStatusIndicator() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<QwenStatus | null>(null);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const s = await getQwenStatus();
        setStatus(s);
      } catch {
        // 静默失败
      }
    };

    fetchStatus();
    timerRef.current = window.setInterval(fetchStatus, POLL_INTERVAL);
    return () => {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
      }
    };
  }, []);

  if (!status || !status.managed) return null;

  const { state } = status.event;
  const kind = status.event.kind;
  const label = t(stateKey(state, kind));

  return (
    <span className="pf-nav-status">
      <Tooltip title={t("vramStatus.tooltip")}>
        <Tag
        color={stateColor(state, kind)}
        icon={stateIcon(state, kind)}
        style={{ cursor: "default" }}
      >
        {label}
        </Tag>
      </Tooltip>
    </span>
  );
}
