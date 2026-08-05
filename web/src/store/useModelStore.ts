import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import http from "@/api/client";

/**
 * 模型切换 store —— 管理当前选中的大模型与可用模型列表。
 *
 * - persist：把 currentModel 持久化到 localStorage，刷新页面保留选择。
 * - load()：拉取 GET /api/model/current，用后端真实状态校准。
 * - switchModel()：调用 POST /api/model/switch，成功后更新本地；失败回退。
 *
 * localStorage key: 'paper-model'
 */
export interface ModelOption {
  value: string;
  label: string;
  provider: string;
  model: string;
}

interface ModelState {
  /** 当前选中的模型标识（DB id 字符串） */
  current: string;
  /** 当前模型的展示名称 */
  label: string;
  /** 可用模型列表（从 DB 加载） */
  available: ModelOption[];
  /** 是否启用模型切换功能 */
  enabled: boolean;
  /** 切换中标记 */
  switching: boolean;
  /** 拉取后端当前模型 + 可用列表 */
  load: () => Promise<void>;
  /** 切换模型，成功更新本地；失败回退并提示 */
  switchModel: (value: string) => Promise<boolean>;
}

export const useModelStore = create<ModelState>()(
  persist(
    (set, get) => ({
      current: "",
      label: "",
      available: [],
      enabled: true,
      switching: false,

      load: async () => {
        try {
          const { data } = await http.get("/model/current");
          set({
            current: data.current,
            label: data.label,
            available: data.available ?? [],
            enabled: data.enabled ?? true,
          });
        } catch (e) {
          console.error("[model] load failed:", e);
        }
      },

      switchModel: async (value: string) => {
        const prev = get().current;
        const prevLabel = get().label;
        // 乐观更新
        const target = get().available.find((m) => m.value === value);
        if (target) {
          set({ current: value, label: target.label });
        }
        set({ switching: true });
        try {
          const { data } = await http.post("/model/switch", { model: value });
          if (data.success) {
            set({ current: data.current, label: data.label, switching: false });
            return true;
          }
          set({ current: prev, label: prevLabel, switching: false });
          return false;
        } catch (e) {
          set({ current: prev, label: prevLabel, switching: false });
          throw e;
        }
      },
    }),
    {
      name: "paper-model",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        current: state.current,
        label: state.label,
      }),
    },
  ),
);
