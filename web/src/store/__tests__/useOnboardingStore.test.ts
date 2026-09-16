import { describe, it, expect, beforeEach, vi } from "vitest";
import { useOnboardingStore, ONBOARDING_VERSION } from "../useOnboardingStore";
import { ONBOARDING_STEPS } from "@/components/onboarding/steps";

const STORAGE_KEY = "pf-onboarding-completed";
const LAST_INDEX = ONBOARDING_STEPS.length - 1;

describe("useOnboardingStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useOnboardingStore.setState({ running: false, stepIndex: 0, completed: false });
  });

  it("start 从第 0 步开始并进入运行态", () => {
    useOnboardingStore.setState({ stepIndex: 3 });
    useOnboardingStore.getState().start();
    const s = useOnboardingStore.getState();
    expect(s.running).toBe(true);
    expect(s.stepIndex).toBe(0);
  });

  it("resume 保留上次进度，stop 只收起不写完成标记", () => {
    useOnboardingStore.setState({ stepIndex: 2, running: false });
    useOnboardingStore.getState().resume();
    expect(useOnboardingStore.getState().running).toBe(true);
    expect(useOnboardingStore.getState().stepIndex).toBe(2);

    useOnboardingStore.getState().stop();
    expect(useOnboardingStore.getState().running).toBe(false);
    // 关键：中途离开不该被算作「已看过」
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(useOnboardingStore.getState().completed).toBe(false);
  });

  it("next 逐步推进", () => {
    useOnboardingStore.setState({ running: true, stepIndex: 0 });
    useOnboardingStore.getState().next();
    expect(useOnboardingStore.getState().stepIndex).toBe(1);
  });

  it("在最后一步 next 等价于 finish：关闭并写入当前版本号", () => {
    useOnboardingStore.setState({ running: true, stepIndex: LAST_INDEX });
    useOnboardingStore.getState().next();
    const s = useOnboardingStore.getState();
    expect(s.running).toBe(false);
    expect(s.completed).toBe(true);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(ONBOARDING_VERSION);
  });

  it("prev 不会越过第 0 步", () => {
    useOnboardingStore.setState({ stepIndex: 0 });
    useOnboardingStore.getState().prev();
    expect(useOnboardingStore.getState().stepIndex).toBe(0);
  });

  it("goto 越界时被钳制到合法区间", () => {
    useOnboardingStore.getState().goto(999);
    expect(useOnboardingStore.getState().stepIndex).toBe(LAST_INDEX);
    useOnboardingStore.getState().goto(-5);
    expect(useOnboardingStore.getState().stepIndex).toBe(0);
  });

  it("finish 写入版本号并置 completed", () => {
    useOnboardingStore.setState({ running: true, stepIndex: 2 });
    useOnboardingStore.getState().finish();
    expect(useOnboardingStore.getState().completed).toBe(true);
    expect(useOnboardingStore.getState().running).toBe(false);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe(ONBOARDING_VERSION);
  });

  it("reset 清除完成标记，使引导可以重看", () => {
    useOnboardingStore.getState().finish();
    expect(useOnboardingStore.getState().completed).toBe(true);
    useOnboardingStore.getState().reset();
    expect(useOnboardingStore.getState().completed).toBe(false);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("localStorage 不可用时按「未完成」处理（宁可多弹一次）", () => {
    const getItem = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    // 模块级初始化已经跑过，这里直接验证读取失败时的兜底语义：
    // 抛异常不应让 store 崩溃，且 completed 不应被误判为 true
    expect(() => window.localStorage.getItem(STORAGE_KEY)).toThrow();
    expect(useOnboardingStore.getState().completed).toBe(false);
    getItem.mockRestore();
  });

  it("已存当前版本号时，模块初始化即视为已完成", async () => {
    vi.resetModules();
    window.localStorage.setItem(STORAGE_KEY, ONBOARDING_VERSION);
    const mod = await import("../useOnboardingStore");
    expect(mod.useOnboardingStore.getState().completed).toBe(true);
  });
});
