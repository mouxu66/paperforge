import { create } from "zustand";

export type ThemeMode = "light" | "dark" | "system";

const STORAGE_KEY = "pf-theme";

interface ThemeState {
  mode: ThemeMode;
  /** Current resolved theme, taking system preference into account */
  resolved: "light" | "dark";
  setMode: (mode: ThemeMode) => void;
  init: () => void;
}

function resolveTheme(mode: ThemeMode): "light" | "dark" {
  if (mode === "system") {
    if (typeof window === "undefined") return "light";
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return mode;
}

function applyTheme(resolved: "light" | "dark") {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  // 始终写入解析后的主题，确保 system 模式下 CSS 变量与组件覆盖也能进入正确主题。
  // mode 仍保留在 store 中，用于下次初始化和系统偏好变化时重新解析。
  root.setAttribute("data-theme", resolved);
  root.classList.remove("light", "dark");
  root.classList.add(resolved);
}

export const useThemeStore = create<ThemeState>((set) => ({
  mode: "light",
  resolved: "light",
  setMode: (mode) => {
    const resolved = resolveTheme(mode);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, mode);
    }
    applyTheme(resolved);
    set({ mode, resolved });
  },
  init: () => {
    if (typeof window === "undefined") return;
    const saved = window.localStorage.getItem(STORAGE_KEY) as ThemeMode | null;
    const mode: ThemeMode =
      saved === "dark" || saved === "light" || saved === "system" ? saved : "light";
    const resolved = resolveTheme(mode);
    applyTheme(resolved);
    set({ mode, resolved });

    // Listen for system preference changes when in system mode
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    media.addEventListener("change", (e) => {
      const current = useThemeStore.getState().mode;
      if (current === "system") {
        const nextResolved = e.matches ? "dark" : "light";
        applyTheme(nextResolved);
        set({ resolved: nextResolved });
      }
    });
  },
}));
