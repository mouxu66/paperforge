import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { AskReference } from '@/api/types'

/**
 * 问答历史 store —— 使用 Zustand + persist 存储到 localStorage。
 *
 * - 每次提交问答成功后调用 add()，自动生成唯一 ID，新记录插入列表头部。
 * - remove() 删除单条；clear() 清空全部。
 * - localStorage key: 'ask-history'
 */
export interface HistoryItem {
  /** 唯一 ID（Date.now + random） */
  id: string
  query: string
  answer: string
  references: AskReference[]
  timestamp: number
  /** 当时的论文范围（选中的 paper IDs） */
  selectedIds: string[]
}

interface HistoryState {
  items: HistoryItem[]
  add: (item: Omit<HistoryItem, 'id'>) => void
  remove: (id: string) => void
  clear: () => void
}

export const useHistoryStore = create<HistoryState>()(
  persist(
    (set) => ({
      items: [],

      add: (item) =>
        set((state) => ({
          items: [
            {
              ...item,
              id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            },
            ...state.items,
          ],
        })),

      remove: (id) =>
        set((state) => ({
          items: state.items.filter((it) => it.id !== id),
        })),

      clear: () => set({ items: [] }),
    }),
    {
      name: 'ask-history',
      storage: createJSONStorage(() => localStorage),
    },
  ),
)
