import { create } from "zustand";
import { ONBOARDING_STEPS } from "@/components/onboarding/steps";

/**
 * 新手引导状态。
 *
 * 完成标记按「引导版本号」记录，而不是一个布尔值：
 * - 布尔标记（done=true）意味着引导一旦看过就永远不再出现，即使后来加了关键新步骤；
 * - 版本号标记让「内容有实质变化」时老用户能再看一遍新内容，且不重复打扰。
 * 递增 ONBOARDING_VERSION 即可让所有用户重新看一次。
 */
export const ONBOARDING_VERSION = "1.0.0";

const STORAGE_KEY = "pf-onboarding-completed";

function readCompletedVersion(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // localStorage 不可用（隐私模式/被禁用）时按「未完成」处理：
    // 宁可多弹一次，也不要让新手彻底看不到引导。
    return null;
  }
}

function writeCompletedVersion(version: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, version);
  } catch {
    // 写不进去就只在本次会话内生效，不阻断流程。
  }
}

interface OnboardingState {
  /** 引导是否正在展示 */
  running: boolean;
  /** 当前步骤下标 */
  stepIndex: number;
  /** 当前版本是否已完成/跳过过 */
  completed: boolean;
  /** 从第 0 步开始（自动启动与「重看引导」共用） */
  start: () => void;
  /** 从上次进度继续（离开首页后返回时使用，不重置步骤） */
  resume: () => void;
  /** 收起引导但不写完成标记（用户中途导航去了别的页面） */
  stop: () => void;
  /** 完成或跳过：写完成标记并关闭 */
  finish: () => void;
  /** 下一步；已在最后一步时等同于 finish */
  next: () => void;
  /** 上一步（不会越界到负数） */
  prev: () => void;
  /** 跳到指定步（越界自动钳制） */
  goto: (index: number) => void;
  /** 清除完成标记（仅「重看引导」与测试使用） */
  reset: () => void;
}

export const useOnboardingStore = create<OnboardingState>((set, get) => ({
  running: false,
  stepIndex: 0,
  completed: readCompletedVersion() === ONBOARDING_VERSION,

  start: () => set({ running: true, stepIndex: 0 }),

  resume: () => set({ running: true }),

  stop: () => set({ running: false }),

  finish: () => {
    writeCompletedVersion(ONBOARDING_VERSION);
    set({ running: false, completed: true });
  },

  next: () => {
    const { stepIndex } = get();
    if (stepIndex >= ONBOARDING_STEPS.length - 1) {
      get().finish();
      return;
    }
    set({ stepIndex: stepIndex + 1 });
  },

  prev: () => set({ stepIndex: Math.max(0, get().stepIndex - 1) }),

  goto: (index) =>
    set({ stepIndex: Math.min(Math.max(0, index), ONBOARDING_STEPS.length - 1) }),

  reset: () => {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // 忽略：清除失败时仍把内存状态置为未完成，用户能立刻重看
    }
    set({ completed: false });
  },
}));
