import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ElementType, ReactNode } from "react";

type RevealCallback = (entry: IntersectionObserverEntry) => void;

// 模块级共享一个 IntersectionObserver，避免每个元素各建一个实例（数百卡片也轻量）。
const elementCallbacks = new Map<Element, RevealCallback>();
let sharedObserver: IntersectionObserver | null = null;

function getObserver(): IntersectionObserver {
  if (sharedObserver) return sharedObserver;
  sharedObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        elementCallbacks.get(entry.target)?.(entry);
      }
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
  );
  return sharedObserver;
}

export interface RevealProps {
  children: ReactNode;
  /** 渲染的容器标签，默认 div */
  as?: ElementType;
  /** 入场延迟（毫秒），用于错峰 stagger */
  delay?: number;
  className?: string;
  style?: CSSProperties;
  /** 仅触发一次（默认 true）；false 时离开视口会淡出 */
  once?: boolean;
}

/**
 * 滚动进入视口时的淡入组件。
 * - 仅用 transform/opacity，不触发重排；
 * - 尊重 prefers-reduced-motion：直接显示，无动画；
 * - 无 IntersectionObserver 时（极旧浏览器）直接显示，内容不会不可见。
 */
export default function Reveal({
  children,
  as: Tag = "div",
  delay = 0,
  className = "",
  style,
  once = true,
}: RevealProps) {
  const ref = useRef<HTMLElement | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    // 减少动效偏好：直接显示
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      setVisible(true);
      return;
    }

    // 不支持 IntersectionObserver：直接显示，避免内容永久隐藏
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }

    const observer = getObserver();
    const cb: RevealCallback = (entry) => {
      if (entry.isIntersecting) {
        setVisible(true);
        if (once) {
          observer.unobserve(entry.target);
          elementCallbacks.delete(entry.target);
        }
      } else if (!once) {
        setVisible(false);
      }
    };
    elementCallbacks.set(el, cb);
    observer.observe(el);

    return () => {
      observer.unobserve(el);
      elementCallbacks.delete(el);
    };
  }, [once]);

  const Component = (Tag ?? "div") as ElementType;
  return (
    <Component
      ref={ref}
      className={`pf-reveal${visible ? " is-visible" : ""}${className ? " " + className : ""}`}
      style={delay ? { transitionDelay: `${delay}ms`, ...style } : style}
    >
      {children}
    </Component>
  );
}
