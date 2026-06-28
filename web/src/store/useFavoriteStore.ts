import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import type { Paper } from '@/api/types'
import { addFavorite, fetchFavorites, removeFavorite } from '@/api/papers'

/**
 * 收藏 store —— localStorage 缓存 + 后端 SQLite 双向同步。
 *
 * - persist：本地缓存 items，刷新瞬间先渲染缓存，再由 load() 用后端数据覆盖。
 * - load()：拉取 GET /api/favorites（完整 Paper[]），以后端为准覆盖本地与 localStorage。
 * - toggle()：乐观更新本地（UI 瞬间反馈 + 心跳动画），随后异步 POST/DELETE 同步后端，
 *             失败仅 console.error，不回滚本地（保证操作不卡顿）。
 *
 * localStorage key: 'paper-favorites'
 */
interface FavoriteState {
  /** 已收藏的论文列表（完整 Paper 对象） */
  items: Paper[]
  /** load 防抖标记：并发调用时直接返回，避免重复请求 */
  loading: boolean
  /** 拉取后端收藏并覆盖本地 */
  load: () => Promise<void>
  /** 切换收藏（乐观更新 + 异步同步后端） */
  toggle: (paper: Paper) => void
  /** 判断某篇论文是否已收藏 */
  isFavorited: (id: string) => boolean
}

export const useFavoriteStore = create<FavoriteState>()(
  persist(
    (set, get) => ({
      items: [],
      loading: false,

      load: async () => {
        // 防抖：正在进行拉取时，跳过并发调用
        if (get().loading) return
        set({ loading: true })
        try {
          // 后端返回完整 Paper[]，直接覆盖本地 items（并写入 localStorage）
          const items = await fetchFavorites()
          set({ items })
        } catch (e) {
          // 拉取失败保留本地缓存，不影响渲染
          console.error('[favorites] load failed:', e)
        } finally {
          set({ loading: false })
        }
      },

      toggle: (paper) => {
        const exists = get().items.some((p) => p.id === paper.id)

        // 1) 乐观更新：立即修改本地 items + localStorage，UI 瞬间反馈
        set((state) => ({
          items: exists
            ? state.items.filter((p) => p.id !== paper.id)
            : [...state.items, paper],
        }))

        // 2) 异步同步后端：添加 → POST，取消 → DELETE；失败仅记录，不回滚
        if (exists) {
          removeFavorite(paper.id).catch((e) =>
            console.error('[favorites] remove failed:', e),
          )
        } else {
          addFavorite(paper.id).catch((e) =>
            console.error('[favorites] add failed:', e),
          )
        }
      },

      isFavorited: (id) => get().items.some((p) => p.id === id),
    }),
    {
      name: 'paper-favorites',
      storage: createJSONStorage(() => localStorage),
      // 仅持久化数据字段；loading 与方法不写入 localStorage
      partialize: (state) => ({ items: state.items }),
    },
  ),
)
