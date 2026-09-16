import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import OnboardingTour from "../OnboardingTour";
import { useOnboardingStore } from "@/store/useOnboardingStore";
import { ONBOARDING_STEPS } from "../steps";

/** 挂一个带 data-tour 的假锚点，模拟真实页面里的目标元素 */
function mountAnchor(tourId: string, rect?: Partial<DOMRect>): HTMLElement {
  const el = document.createElement("div");
  el.setAttribute("data-tour", tourId);
  el.getBoundingClientRect = () =>
    ({
      top: 100,
      left: 200,
      width: 300,
      height: 80,
      bottom: 180,
      right: 500,
      x: 200,
      y: 100,
      toJSON: () => ({}),
      ...rect,
    }) as DOMRect;
  document.body.appendChild(el);
  return el;
}

const UPLOAD_STEP_INDEX = ONBOARDING_STEPS.findIndex((s) => s.id === "upload");
const LAST_INDEX = ONBOARDING_STEPS.length - 1;

describe("OnboardingTour", () => {
  beforeEach(() => {
    useOnboardingStore.setState({ running: false, stepIndex: 0, completed: false });
  });

  afterEach(() => {
    // 手动挂载的锚点不在 testing-library 的清理范围内
    document.querySelectorAll("[data-tour]").forEach((el) => el.remove());
    vi.useRealTimers();
  });

  it("未运行时什么都不渲染", () => {
    const { container } = render(<OnboardingTour />);
    expect(container.querySelector(".pf-onboarding")).toBeNull();
  });

  it("运行中渲染当前步骤文案、进度与跳过入口", () => {
    useOnboardingStore.setState({ running: true, stepIndex: 0 });
    render(<OnboardingTour />);

    expect(screen.getByText(ONBOARDING_STEPS[0].titleKey)).toBeInTheDocument();
    expect(screen.getByText("onboarding.progress")).toBeInTheDocument();
    expect(screen.getByText("onboarding.skip")).toBeInTheDocument();
    expect(screen.getByText("onboarding.next")).toBeInTheDocument();
  });

  it("第 0 步的「上一步」禁用，推进后可点", () => {
    useOnboardingStore.setState({ running: true, stepIndex: 0 });
    render(<OnboardingTour />);
    expect(screen.getByText("onboarding.prev").closest("button")).toBeDisabled();

    act(() => {
      useOnboardingStore.setState({ stepIndex: 1 });
    });
    expect(screen.getByText("onboarding.prev").closest("button")).not.toBeDisabled();
  });

  it("点「下一步」推进步骤", () => {
    useOnboardingStore.setState({ running: true, stepIndex: 0 });
    render(<OnboardingTour />);

    fireEvent.click(screen.getByText("onboarding.next"));
    expect(useOnboardingStore.getState().stepIndex).toBe(1);
  });

  it("最后一步的按钮文案为「开始使用」，点击后结束引导", () => {
    useOnboardingStore.setState({ running: true, stepIndex: LAST_INDEX });
    render(<OnboardingTour />);

    expect(screen.getByText("onboarding.done")).toBeInTheDocument();
    fireEvent.click(screen.getByText("onboarding.done"));

    const s = useOnboardingStore.getState();
    expect(s.running).toBe(false);
    expect(s.completed).toBe(true);
  });

  it("点「跳过引导」结束引导并记录完成", () => {
    useOnboardingStore.setState({ running: true, stepIndex: 0 });
    render(<OnboardingTour />);

    fireEvent.click(screen.getByText("onboarding.skip"));
    expect(useOnboardingStore.getState().running).toBe(false);
    expect(useOnboardingStore.getState().completed).toBe(true);
  });

  it("Esc 跳过、方向键切换步骤", () => {
    useOnboardingStore.setState({ running: true, stepIndex: 1 });
    render(<OnboardingTour />);

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(useOnboardingStore.getState().stepIndex).toBe(2);

    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(useOnboardingStore.getState().stepIndex).toBe(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(useOnboardingStore.getState().running).toBe(false);
  });

  it("锚点存在时渲染高亮框并定位到目标元素", async () => {
    mountAnchor("upload");
    useOnboardingStore.setState({ running: true, stepIndex: UPLOAD_STEP_INDEX });
    render(<OnboardingTour />);

    await waitFor(() => {
      expect(document.querySelector(".pf-onboarding-highlight")).toBeInTheDocument();
    });

    const highlight = document.querySelector<HTMLElement>(".pf-onboarding-highlight")!;
    // 高亮框在目标元素外扩 8px
    expect(highlight.style.top).toBe("92px");
    expect(highlight.style.left).toBe("192px");
    expect(highlight.style.width).toBe("316px");
    expect(highlight.style.height).toBe("96px");
  });

  it("锚点不存在时降级为整屏压暗，不渲染高亮框", () => {
    useOnboardingStore.setState({ running: true, stepIndex: UPLOAD_STEP_INDEX });
    render(<OnboardingTour />);

    expect(document.querySelector(".pf-onboarding-highlight")).toBeNull();
    expect(document.querySelector(".pf-onboarding-veil")).toBeInTheDocument();
  });

  it("锚点长时间缺失后给出显式提示（而不是静默跳过这一步）", async () => {
    vi.useFakeTimers();
    useOnboardingStore.setState({ running: true, stepIndex: UPLOAD_STEP_INDEX });
    render(<OnboardingTour />);

    expect(screen.queryByText("onboarding.anchorMissing")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2600);
    });

    expect(screen.getByText("onboarding.anchorMissing")).toBeInTheDocument();
  });

  it("锚点后出现时能自动捕获（不依赖步骤切换）", async () => {
    useOnboardingStore.setState({ running: true, stepIndex: UPLOAD_STEP_INDEX });
    render(<OnboardingTour />);

    expect(document.querySelector(".pf-onboarding-highlight")).toBeNull();

    // 模拟「页面渲染慢，锚点稍后才出现」
    mountAnchor("upload");
    await waitFor(
      () => {
        expect(document.querySelector(".pf-onboarding-highlight")).toBeInTheDocument();
      },
      { timeout: 3000 },
    );
  });
});
