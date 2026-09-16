import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "antd";
import { ArrowLeft, ArrowRight, Check, Compass } from "lucide-react";
import { ONBOARDING_STEPS, type TourPlacement } from "./steps";
import { useOnboardingStore } from "@/store/useOnboardingStore";

/** 高亮框在目标元素外扩的像素（避免边框紧贴元素） */
const HIGHLIGHT_PAD = 8;
/** 高亮框与气泡之间的间距 */
const BUBBLE_GAP = 14;
/** 气泡距视口边缘的最小留白 */
const VIEWPORT_EDGE = 12;
/** 气泡宽度（窄屏自动收缩） */
const BUBBLE_WIDTH = 340;
/** 锚点未出现时的轮询间隔 */
const POLL_MISSING_MS = 300;
/** 锚点已出现后的低频跟随间隔：页面/路由变化可能让元素移动或换一个 */
const POLL_FOLLOW_MS = 800;
/** 超过该时长仍未找到锚点，就在气泡里显示「入口暂未找到」提示（但仍继续重试） */
const ANCHOR_MISSING_NOTICE_MS = 2000;
/** 浮点比较容差：低于 0.5px 的位置变化不值得重渲染 */
const EPSILON = 0.5;

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

function findTarget(name: string): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[data-tour="${name}"]`);
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(value, Math.max(min, max)));
}

function sameRect(a: Rect | null, b: Rect | null): boolean {
  if (a === null || b === null) return a === b;
  return (
    Math.abs(a.top - b.top) < EPSILON &&
    Math.abs(a.left - b.left) < EPSILON &&
    Math.abs(a.width - b.width) < EPSILON &&
    Math.abs(a.height - b.height) < EPSILON
  );
}

/**
 * 计算气泡的 fixed 定位。
 *
 * rect 为 null 表示居中（开场/收尾步骤，或锚点缺失时的降级展示）。
 * 优先使用步骤声明的方位，该方向空间不足且反方向更宽裕时自动翻转。
 */
function computeBubblePosition(
  rect: Rect | null,
  placement: TourPlacement,
  bubbleW: number,
  bubbleH: number,
): CSSProperties {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  if (!rect) {
    return {
      top: clamp(vh / 2 - bubbleH / 2, VIEWPORT_EDGE, vh - bubbleH - VIEWPORT_EDGE),
      left: clamp(vw / 2 - bubbleW / 2, VIEWPORT_EDGE, vw - bubbleW - VIEWPORT_EDGE),
    };
  }

  const boxTop = rect.top - HIGHLIGHT_PAD;
  const boxLeft = rect.left - HIGHLIGHT_PAD;
  const boxW = rect.width + HIGHLIGHT_PAD * 2;
  const boxH = rect.height + HIGHLIGHT_PAD * 2;

  const spaceBelow = vh - (boxTop + boxH);
  const spaceAbove = boxTop;
  const spaceRight = vw - (boxLeft + boxW);
  const spaceLeft = boxLeft;

  let place: Exclude<TourPlacement, "center"> = placement === "center" ? "bottom" : placement;

  if (place === "bottom" && spaceBelow < bubbleH + BUBBLE_GAP && spaceAbove > spaceBelow) {
    place = "top";
  } else if (place === "top" && spaceAbove < bubbleH + BUBBLE_GAP && spaceBelow > spaceAbove) {
    place = "bottom";
  } else if (place === "right" && spaceRight < bubbleW + BUBBLE_GAP && spaceLeft > spaceRight) {
    place = "left";
  } else if (place === "left" && spaceLeft < bubbleW + BUBBLE_GAP && spaceRight > spaceLeft) {
    place = "right";
  }

  let top = 0;
  let left = 0;
  switch (place) {
    case "bottom":
      top = boxTop + boxH + BUBBLE_GAP;
      left = boxLeft + boxW / 2 - bubbleW / 2;
      break;
    case "top":
      top = boxTop - BUBBLE_GAP - bubbleH;
      left = boxLeft + boxW / 2 - bubbleW / 2;
      break;
    case "right":
      top = boxTop + boxH / 2 - bubbleH / 2;
      left = boxLeft + boxW + BUBBLE_GAP;
      break;
    case "left":
      top = boxTop + boxH / 2 - bubbleH / 2;
      left = boxLeft - BUBBLE_GAP - bubbleW;
      break;
  }

  return {
    top: clamp(top, VIEWPORT_EDGE, vh - bubbleH - VIEWPORT_EDGE),
    left: clamp(left, VIEWPORT_EDGE, vw - bubbleW - VIEWPORT_EDGE),
  };
}

/**
 * 新手引导（spotlight 分步导览）。
 *
 * 行为约定：
 * - 锚点用 `data-tour` 值查找，元素懒加载/路由切换时会持续重试，一旦出现立即切回高亮态；
 * - 超过 ANCHOR_MISSING_NOTICE_MS 仍未找到时**降级为居中展示**并在气泡注明原因，
 *   而不是静默跳过——静默跳过会让用户以为引导本来就只有一半内容；
 * - 遮罩用 box-shadow 造洞而非真实覆盖层，不拦截点击：用户可以在引导过程中真实
 *   点一下被高亮的按钮。也正因如此，点击遮罩不关闭引导，退出走「跳过」或 Esc。
 */
export default function OnboardingTour() {
  const { t } = useTranslation();
  const running = useOnboardingStore((s) => s.running);
  const stepIndex = useOnboardingStore((s) => s.stepIndex);
  const next = useOnboardingStore((s) => s.next);
  const prev = useOnboardingStore((s) => s.prev);
  const finish = useOnboardingStore((s) => s.finish);

  const [rect, setRect] = useState<Rect | null>(null);
  const [anchorMissing, setAnchorMissing] = useState(false);
  const [bubbleSize, setBubbleSize] = useState({ w: BUBBLE_WIDTH, h: 200 });
  const [measured, setMeasured] = useState(false);

  const bubbleRef = useRef<HTMLDivElement>(null);
  /** 标记本轮解析是否已经把锚点滚进视口，避免每轮轮询都触发滚动 */
  const scrolledRef = useRef(false);

  const step = ONBOARDING_STEPS[stepIndex];
  const total = ONBOARDING_STEPS.length;
  const isLast = stepIndex >= total - 1;

  const measure = useCallback((target: string | null) => {
    if (!target) {
      setRect((prevRect) => (prevRect === null ? prevRect : null));
      return;
    }
    const el = findTarget(target);
    if (!el) return;
    const r = el.getBoundingClientRect();
    const nextRect: Rect = { top: r.top, left: r.left, width: r.width, height: r.height };
    // 位置没变就不 setState，避免低频轮询造成无意义重渲染
    setRect((prevRect) => (sameRect(prevRect, nextRect) ? prevRect : nextRect));
  }, []);

  // 解析当前步骤的锚点：找不到就一直重试，找到后低频跟随
  useEffect(() => {
    if (!running || !step) return;
    let cancelled = false;
    let timer: number | null = null;
    let raf: number | null = null;
    const startedAt = Date.now();
    scrolledRef.current = false;

    const tick = () => {
      if (cancelled) return;

      if (!step.target) {
        setRect((prevRect) => (prevRect === null ? prevRect : null));
        setAnchorMissing(false);
        return;
      }

      const el = findTarget(step.target);
      if (!el) {
        setRect((prevRect) => (prevRect === null ? prevRect : null));
        if (Date.now() - startedAt > ANCHOR_MISSING_NOTICE_MS) setAnchorMissing(true);
        timer = window.setTimeout(tick, POLL_MISSING_MS);
        return;
      }

      setAnchorMissing(false);
      if (!scrolledRef.current) {
        // instant 滚动：避免平滑滚动动画期间测量到中间态位置
        el.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
        scrolledRef.current = true;
        raf = window.requestAnimationFrame(() => {
          if (!cancelled) measure(step.target);
        });
      } else {
        measure(step.target);
      }
      timer = window.setTimeout(tick, POLL_FOLLOW_MS);
    };

    setMeasured(false);
    tick();

    return () => {
      cancelled = true;
      if (timer !== null) window.clearTimeout(timer);
      if (raf !== null) window.cancelAnimationFrame(raf);
    };
  }, [running, step, stepIndex, measure]);

  // 视口变化/页面滚动时立即重新测量，保证高亮框跟随目标
  useEffect(() => {
    if (!running) return;
    const onReflow = () => measure(step?.target ?? null);
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);
    return () => {
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, true);
    };
  }, [running, measure, step?.target]);

  // 测量气泡真实尺寸用于精确定位（宽度固定，高度随文案行数变化）
  useLayoutEffect(() => {
    if (!running || !bubbleRef.current) return;
    const r = bubbleRef.current.getBoundingClientRect();
    setBubbleSize((prev) =>
      Math.abs(prev.w - r.width) < EPSILON && Math.abs(prev.h - r.height) < EPSILON
        ? prev
        : { w: r.width, h: r.height },
    );
    setMeasured(true);
  }, [running, stepIndex, rect, anchorMissing, t]);

  // 键盘操作：Esc 跳过、左右方向键切换、Enter 下一步
  useEffect(() => {
    if (!running) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        finish();
      } else if (e.key === "ArrowRight" || e.key === "Enter") {
        e.preventDefault();
        next();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        prev();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [running, next, prev, finish]);

  // 焦点管理：引导开始时把焦点移入气泡，结束后归还给触发元素
  useEffect(() => {
    if (!running) return;
    const previous = document.activeElement as HTMLElement | null;
    bubbleRef.current?.focus();
    return () => previous?.focus?.();
  }, [running]);

  if (!running || !step) return null;

  const highlight = rect
    ? {
        top: rect.top - HIGHLIGHT_PAD,
        left: rect.left - HIGHLIGHT_PAD,
        width: rect.width + HIGHLIGHT_PAD * 2,
        height: rect.height + HIGHLIGHT_PAD * 2,
      }
    : null;

  const bubbleStyle = computeBubblePosition(rect, step.placement, bubbleSize.w, bubbleSize.h);

  return (
    <div
      className="pf-onboarding"
      role="dialog"
      aria-modal="true"
      aria-label={t("onboarding.ariaLabel", "新手引导")}
    >
      {highlight ? (
        <div
          className="pf-onboarding-highlight"
          style={{
            top: highlight.top,
            left: highlight.left,
            width: highlight.width,
            height: highlight.height,
          }}
          aria-hidden="true"
        />
      ) : (
        // 无锚点时整屏压暗，让居中气泡成为唯一焦点
        <div className="pf-onboarding-veil" aria-hidden="true" />
      )}

      <div
        ref={bubbleRef}
        tabIndex={-1}
        className="pf-onboarding-bubble"
        style={{
          ...bubbleStyle,
          width: `min(${BUBBLE_WIDTH}px, calc(100vw - ${VIEWPORT_EDGE * 2}px))`,
          opacity: measured ? 1 : 0,
        }}
      >
        <div className="pf-onboarding-bubble-head">
          <span className="pf-onboarding-step-badge">
            <Compass size={13} />
            {t("onboarding.progress", { current: stepIndex + 1, total, defaultValue: "第 {{current}} / {{total}} 步" })}
          </span>
          <button
            type="button"
            className="pf-onboarding-skip"
            onClick={finish}
            aria-label={t("onboarding.skip", "跳过引导")}
          >
            {t("onboarding.skip", "跳过引导")}
          </button>
        </div>

        <div className="pf-onboarding-title">{t(step.titleKey)}</div>
        <div className="pf-onboarding-desc">{t(step.descKey)}</div>

        {step.tipKey && (
          <div className="pf-onboarding-tip">
            <span aria-hidden="true">💡</span>
            <span>{t(step.tipKey)}</span>
          </div>
        )}

        {anchorMissing && (
          <div className="pf-onboarding-warning">
            {t("onboarding.anchorMissing", "这一步对应的界面元素当前没有找到，可能因为页面还在加载或布局不同。引导会继续尝试定位。")}
          </div>
        )}

        <div className="pf-onboarding-actions">
          <div className="pf-onboarding-dots" aria-hidden="true">
            {ONBOARDING_STEPS.map((s, i) => (
              <span
                key={s.id}
                className={
                  i === stepIndex ? "pf-onboarding-dot pf-onboarding-dot-active" : "pf-onboarding-dot"
                }
              />
            ))}
          </div>
          <div className="pf-onboarding-buttons">
            <Button
              size="small"
              disabled={stepIndex === 0}
              onClick={prev}
              icon={<ArrowLeft size={13} />}
            >
              {t("onboarding.prev", "上一步")}
            </Button>
            <Button
              size="small"
              type="primary"
              onClick={next}
              icon={isLast ? <Check size={13} /> : undefined}
            >
              {isLast ? t("onboarding.done", "开始使用") : t("onboarding.next", "下一步")}
              {!isLast && <ArrowRight size={13} style={{ marginLeft: 4 }} />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
