/**
 * 新手引导步骤定义（显式 spec）。
 *
 * 设计约定：
 * - 每步只声明「锚点 + 文案 key + 气泡方位」，不含定位/渲染逻辑；
 *   定位、滚动、降级全部由 OnboardingTour 统一处理。
 * - `target` 为 null 时居中显示，用于开场/收尾这类没有锚点的步骤。
 * - 锚点用 `data-tour` 值而非 CSS 选择器：目标组件自己声明可被引导，
 *   引导不依赖 DOM 结构或类名（重构页面不会静默打断引导）。
 * - 新增步骤只需在此追加，UI 与状态机自动跟随；步骤增减不会让老用户重新看一遍，
 *   因为完成标记按「引导版本号」记录（见 useOnboardingStore）。
 */

export type TourPlacement = "top" | "bottom" | "left" | "right" | "center";

export interface TourStep {
  /** 稳定标识，用于测试与定位问题（不参与展示） */
  id: string;
  /** 锚点：目标元素上的 data-tour 值；null = 居中显示 */
  target: string | null;
  /** 气泡相对锚点的偏好方位；视口空间不足时自动翻转 */
  placement: TourPlacement;
  /** 标题 i18n key */
  titleKey: string;
  /** 说明 i18n key（这一步是做什么的） */
  descKey: string;
  /** 提示 i18n key（新手最容易踩的点） */
  tipKey?: string;
}

/**
 * 引导主线：导入 → 索引 → 检索 → 问答/审稿入口 → 帮助中心。
 *
 * 刻意全部落在首页完成，不自动跳页：新手第一次打开时问答/审稿未必可用
 * （未配模型、库还是空的），跳过去只会让人困惑；这两处的入口改用
 * 「高亮顶部菜单」的方式指路，零跳转风险。
 */
export const ONBOARDING_STEPS: TourStep[] = [
  {
    id: "welcome",
    target: null,
    placement: "center",
    titleKey: "onboarding.step.welcome.title",
    descKey: "onboarding.step.welcome.desc",
    tipKey: "onboarding.step.welcome.tip",
  },
  {
    id: "upload",
    target: "upload",
    placement: "top",
    titleKey: "onboarding.step.upload.title",
    descKey: "onboarding.step.upload.desc",
    tipKey: "onboarding.step.upload.tip",
  },
  {
    id: "index",
    target: "stats",
    placement: "bottom",
    titleKey: "onboarding.step.index.title",
    descKey: "onboarding.step.index.desc",
    tipKey: "onboarding.step.index.tip",
  },
  {
    id: "search",
    target: "search",
    placement: "bottom",
    titleKey: "onboarding.step.search.title",
    descKey: "onboarding.step.search.desc",
    tipKey: "onboarding.step.search.tip",
  },
  {
    id: "understand",
    target: "nav-understand",
    placement: "bottom",
    titleKey: "onboarding.step.understand.title",
    descKey: "onboarding.step.understand.desc",
    tipKey: "onboarding.step.understand.tip",
  },
  {
    id: "help",
    target: "nav-help",
    placement: "bottom",
    titleKey: "onboarding.step.help.title",
    descKey: "onboarding.step.help.desc",
    tipKey: "onboarding.step.help.tip",
  },
];

/** 引导只在首页自动启动：首页承载了全部锚点，其他路由进来时不打断用户 */
export const ONBOARDING_AUTO_START_PATH = "/";
