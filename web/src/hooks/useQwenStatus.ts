import { useCallback, useEffect, useRef, useState } from "react";
import { getQwenStatus, type QwenStatus } from "@/api/qwen";

const POLL_INTERVAL = 3000;

interface UseQwenStatusResult {
  status: QwenStatus | null;
  /** 是否正在显示『加载中/切换中』提示（前端据此渲染 Banner） */
  showHint: boolean;
  start: () => void;
  stop: () => void;
}

/**
 * 轮询 /api/qwen/status，供 DEPTH/OCR 交互期间展示『模型加载中/切换中』提示。
 *
 * 用法：
 *   const { showHint, status, start, stop } = useQwenStatus();
 *   // 触发 DEPTH 前 start()，组件卸载或任务完成 stop()
 *   {showHint && <QwenLoadingBanner status={status} />}
 */
export function useQwenStatus(): UseQwenStatusResult {
  const [status, setStatus] = useState<QwenStatus | null>(null);
  const timerRef = useRef<number | null>(null);
  const activeRef = useRef(false);

  const tick = useCallback(async () => {
    try {
      const s = await getQwenStatus();
      setStatus(s);
    } catch {
      // 轮询失败静默（不影响主流程）
    }
  }, []);

  const start = useCallback(() => {
    activeRef.current = true;
    tick();
    if (timerRef.current === null) {
      timerRef.current = window.setInterval(tick, POLL_INTERVAL);
    }
  }, [tick]);

  const stop = useCallback(() => {
    activeRef.current = false;
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => () => stop(), [stop]);

  const showHint =
    status !== null && (status.event.kind === "loading" || status.event.kind === "switching");

  return { status, showHint, start, stop };
}
