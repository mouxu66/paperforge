import { create } from 'zustand'
import type { LibraryStats, Paper, PaperQuery } from '@/api/types'
import { fetchPapers, fetchSemanticSearch, fetchStats } from '@/api/papers'
import { PAGE_SIZE } from '@/utils/constants'

interface PaperState {
  // 列表
  items: Paper[]
  total: number
  loading: boolean
  // 查询条件
  keyword: string
  category: string
  sort: string
  page: number
  pageSize: number
  // 语义搜索开关
  semantic: boolean
  // 统计
  stats: LibraryStats | null
  statsLoading: boolean

  setKeyword: (v: string) => void
  setCategory: (v: string) => void
  setSort: (v: string) => void
  setPage: (v: number) => void
  setSemantic: (v: boolean) => void
  loadPapers: () => Promise<void>
  loadStats: () => Promise<void>
}

export const usePaperStore = create<PaperState>((set, get) => ({
  items: [],
  total: 0,
  loading: false,
  keyword: '',
  category: 'all',
  sort: 'year_desc',
  page: 1,
  pageSize: PAGE_SIZE,
  semantic: false,
  stats: null,
  statsLoading: false,

  setKeyword: (v) => set({ keyword: v, page: 1 }),
  setCategory: (v) => set({ category: v, page: 1 }),
  setSort: (v) => set({ sort: v }),
  setPage: (v) => set({ page: v }),
  setSemantic: (v) => set({ semantic: v, page: 1 }),

  loadPapers: async () => {
    const { keyword, category, sort, page, pageSize, semantic } = get()
    set({ loading: true })
    try {
      const q: PaperQuery = { keyword, category, sort, page, pageSize }
      // 语义搜索：仅在有关键词时启用；失败则静默降级为 FTS5 关键词搜索
      if (semantic && keyword.trim()) {
        try {
          const items = await fetchSemanticSearch(keyword)
          set({ items, total: items.length })
          return
        } catch (e) {
          console.warn('[PaperForge] 语义搜索不可用，降级为 FTS5 关键词搜索', e)
        }
      }
      const res = await fetchPapers(q)
      set({ items: res.items, total: res.total })
    } finally {
      set({ loading: false })
    }
  },

  loadStats: async () => {
    // 防抖：并发调用直接返回，避免重复请求
    if (get().statsLoading) return
    set({ statsLoading: true })
    try {
      const stats = await fetchStats()
      set({ stats })
    } finally {
      set({ statsLoading: false })
    }
  },
}))
