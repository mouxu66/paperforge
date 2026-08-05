import { create } from "zustand";
import type { TagInfo, RenameTagRequest } from "@/api/types";
import { fetchAllTags, renameTag, deleteTag } from "@/api/papers";

/**
 * 标签 store —— 全库标签列表缓存 + 重命名/删除操作（WP-2.1）。
 *
 * - load()：拉取 GET /api/papers/tags（标签 + 计数），覆盖本地缓存。
 * - rename/delete：调用后端后自动 reload，保持本地与后端一致。
 * - 不做 persist（标签计数随库变化，每次按需 load 即可）。
 */
interface TagState {
  /** 全库标签列表（按计数降序） */
  tags: TagInfo[];
  /** load 防抖标记 */
  loading: boolean;
  /** 拉取全库标签并覆盖本地 */
  load: () => Promise<void>;
  /** 全库重命名标签，完成后 reload */
  rename: (req: RenameTagRequest) => Promise<boolean>;
  /** 全库删除标签，完成后 reload */
  remove: (name: string) => Promise<boolean>;
}

export const useTagStore = create<TagState>()((set, get) => ({
  tags: [],
  loading: false,

  load: async () => {
    if (get().loading) return;
    set({ loading: true });
    try {
      const tags = await fetchAllTags();
      set({ tags });
    } catch (e) {
      console.error("[tags] load failed:", e);
    } finally {
      set({ loading: false });
    }
  },

  rename: async (req) => {
    try {
      await renameTag(req);
      await get().load();
      return true;
    } catch (e) {
      console.error("[tags] rename failed:", e);
      return false;
    }
  },

  remove: async (name) => {
    try {
      await deleteTag(name);
      await get().load();
      return true;
    } catch (e) {
      console.error("[tags] delete failed:", e);
      return false;
    }
  },
}));
