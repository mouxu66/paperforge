/**
 * WP-2.4: 大库性能 UX — 性能基线测量工具
 *
 * 提供轻量级 render/API 耗时打点，默认只在开发环境或显式开启时输出。
 * 所有测量均使用 Performance API，不会阻塞主线程。
 */

export interface PerfMark {
  name: string;
  start: number;
  end?: number;
  duration?: number;
  metadata?: Record<string, unknown>;
}

const STORAGE_KEY = "pf_perf_baseline_enabled";

function isDev(): boolean {
  try {
    return import.meta.env?.DEV === true;
  } catch {
    return false;
  }
}

function isEnabled(): boolean {
  if (typeof window === "undefined") return isDev();
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "true" || isDev();
  } catch {
    return isDev();
  }
}

/** 开启/关闭性能基线输出 */
export function setPerfBaselineEnabled(enabled: boolean): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, String(enabled));
}

/** 测量一段异步操作的耗时 */
export async function measureAsync<T>(
  name: string,
  fn: () => Promise<T>,
  metadata?: Record<string, unknown>,
): Promise<T> {
  const start = performance.now();
  try {
    const result = await fn();
    reportMark({ name, start, end: performance.now(), metadata });
    return result;
  } catch (e) {
    reportMark({ name, start, end: performance.now(), metadata: { ...metadata, error: true } });
    throw e;
  }
}

/** 同步测量 */
export function measureSync<T>(name: string, fn: () => T, metadata?: Record<string, unknown>): T {
  const start = performance.now();
  try {
    const result = fn();
    reportMark({ name, start, end: performance.now(), metadata });
    return result;
  } catch (e) {
    reportMark({ name, start, end: performance.now(), metadata: { ...metadata, error: true } });
    throw e;
  }
}

/** 开始一个手动标记 */
export function startMark(name: string, metadata?: Record<string, unknown>): () => void {
  const start = performance.now();
  return () => {
    reportMark({ name, start, end: performance.now(), metadata });
  };
}

function reportMark(mark: PerfMark): void {
  const end = mark.end ?? performance.now();
  const duration = mark.duration ?? end - mark.start;
  const entry = { ...mark, end, duration };
  if (isEnabled()) {

    console.log(`[PF Perf] ${mark.name}: ${duration.toFixed(2)}ms`, entry.metadata ?? "");
  }
  // 同时写入 performance mark，方便 DevTools 查看
  if (typeof performance !== "undefined" && performance.mark) {
    try {
      performance.mark(`${mark.name}-start`, { startTime: mark.start });
      performance.mark(`${mark.name}-end`, { startTime: end });
      performance.measure(mark.name, `${mark.name}-start`, `${mark.name}-end`);
    } catch {
      // ignore duplicate marks
    }
  }
}
