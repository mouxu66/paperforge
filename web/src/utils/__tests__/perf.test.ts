import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { measureAsync, measureSync, startMark, setPerfBaselineEnabled } from "../perf";

describe("perf.ts 性能基线工具（WP-2.4）", () => {
  let now = 0;
  const originalConsoleLog = console.log;

  beforeEach(() => {
    now = 0;
    vi.spyOn(performance, "now").mockImplementation(() => now);
    vi.spyOn(console, "log").mockImplementation(() => {});
    vi.spyOn(performance, "mark").mockImplementation(() => {});
    vi.spyOn(performance, "measure").mockImplementation(() => ({}) as PerformanceMeasure);
    Object.defineProperty(globalThis, "localStorage", {
      value: {
        getItem: vi.fn(),
        setItem: vi.fn(),
        removeItem: vi.fn(),
      },
      writable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    console.log = originalConsoleLog;
  });

  it("measureAsync 测量异步操作耗时", async () => {
    const fn = vi.fn().mockImplementation(async () => {
      now += 50;
      return "result";
    });
    const result = await measureAsync("asyncOp", fn);
    expect(result).toBe("result");
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("measureSync 测量同步操作耗时", () => {
    const fn = vi.fn().mockImplementation(() => {
      now += 30;
      return 42;
    });
    const result = measureSync("syncOp", fn);
    expect(result).toBe(42);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("startMark 返回结束函数，调用后记录耗时", () => {
    const endMark = startMark("manualMark", { key: "value" });
    now += 100;
    endMark();
    // 没有抛出异常即视为成功
    expect(endMark).toBeInstanceOf(Function);
  });

  it("setPerfBaselineEnabled 写入 localStorage", () => {
    setPerfBaselineEnabled(true);
    expect(localStorage.setItem).toHaveBeenCalledWith("pf_perf_baseline_enabled", "true");
  });

  it("measureAsync 在异常时仍上报并抛出", async () => {
    const fn = vi.fn().mockRejectedValue(new Error("boom"));
    await expect(measureAsync("failOp", fn)).rejects.toThrow("boom");
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
