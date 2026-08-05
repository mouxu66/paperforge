import { create } from "zustand";
import type {
  LibraryStats,
  Paper,
  PaperQuery,
  PaperSource,
  RankRequest,
  RankResponse,
} from "@/api/types";
import { fetchPapers, fetchPaperRanking, fetchSemanticSearch, fetchStats } from "@/api/papers";
import { PAGE_SIZE } from "@/utils/constants";
import { measureAsync } from "@/utils/perf";

interface PaperState {
  // 列表
  items: Paper[];
  total: number;
  loading: boolean;
  // 查询条件
  keyword: string;
  category: string;
  sort: string;
  source: PaperSource;
  page: number;
  pageSize: number;
  // 语义搜索开关
  semantic: boolean;
  // 统计
  stats: LibraryStats | null;
  statsLoading: boolean;
  // 综合评分
  ranking: RankResponse | null;
  rankingLoading: boolean;

  setKeyword: (v: string) => void;
  setCategory: (v: string) => void;
  setSort: (v: string) => void;
  setSource: (v: PaperSource) => void;
  /** 原子性地切换分类并重置来源（用于顶部标签页切换） */
  setCategoryAndSource: (category: string, source: PaperSource) => void;
  setPage: (v: number) => void;
  setPageSize: (v: number) => void;
  setSemantic: (v: boolean) => void;
  loadPapers: () => Promise<void>;
  loadStats: () => Promise<void>;
  /** 执行综合评分 */
  loadRanking: (req: RankRequest) => Promise<void>;
  /** 清除评分结果 */
  clearRanking: () => void;
}

let paperRequestSequence = 0;
const paperQueryCache = new Map<string, { timestamp: number; items: Paper[]; total: number }>();
const PAPER_CACHE_TTL_MS = 20_000;

export function invalidatePaperQueryCache(): void {
  paperQueryCache.clear();
}

function paperQueryKey(query: PaperQuery, semantic: boolean): string {
  return JSON.stringify({ ...query, semantic });
}

export const usePaperStore = create<PaperState>((set, get) => ({
  items: [],
  total: 0,
  loading: false,
  keyword: "",
  category: "all",
  sort: "year_desc",
  source: "all",
  page: 1,
  pageSize: PAGE_SIZE,
  semantic: false,
  stats: null,
  statsLoading: false,
  ranking: null,
  rankingLoading: false,

  setKeyword: (v) => set({ keyword: v, page: 1 }),
  setCategory: (v) => set({ category: v, page: 1 }),
  setSort: (v) => set({ sort: v }),
  setSource: (v) => set({ source: v, page: 1 }),
  setCategoryAndSource: (category, source) => set({ category, source, page: 1 }),
  setPage: (v) => set({ page: v }),
  setPageSize: (v) => set({ pageSize: v, page: 1 }),
  setSemantic: (v) => set({ semantic: v, page: 1 }),

  loadPapers: async () => {
    const { keyword, category, sort, source, page, pageSize, semantic } = get();
    const requestSequence = ++paperRequestSequence;
    const q: PaperQuery = { keyword, category, sort, source, page, pageSize };
    const cacheKey = paperQueryKey(q, semantic);
    const cached = paperQueryCache.get(cacheKey);
    set({ loading: true });
    try {
      if (cached && Date.now() - cached.timestamp < PAPER_CACHE_TTL_MS) {
        if (requestSequence === paperRequestSequence) {
          set({ items: cached.items, total: cached.total });
        }
        return;
      }
      if (semantic && keyword.trim()) {
        try {
          const items = await measureAsync("semanticSearch", () => fetchSemanticSearch(keyword), {
            keyword,
            pageSize,
          });
          if (requestSequence === paperRequestSequence) {
            paperQueryCache.set(cacheKey, { timestamp: Date.now(), items, total: items.length });
            set({ items, total: items.length });
          }
          return;
        } catch (e) {
          console.warn("[PaperForge] 语义搜索不可用，降级为 FTS5 关键词搜索", e);
        }
      }
      const res = await measureAsync("fetchPapers", () => fetchPapers(q), {
        keyword,
        page,
        pageSize,
      });
      // 防御性过滤：非「感悟报告」视图下，前端兜底排除 category='report' 的数据，
      // 避免后端旧缓存/异常数据导致报告混入论文分区。
      // 注意：total 仍信任后端分页总数，仅对当前页 items 做兜底过滤。
      const filteredItems =
        category === "report" ? res.items : res.items.filter((p) => p.category !== "report");
      if (requestSequence === paperRequestSequence) {
        paperQueryCache.set(cacheKey, { timestamp: Date.now(), items: filteredItems, total: res.total });
        set({ items: filteredItems, total: res.total });
      }
    } finally {
      if (requestSequence === paperRequestSequence) {
        set({ loading: false });
      }
    }
  },

  loadStats: async () => {
    if (get().statsLoading) return;
    set({ statsLoading: true });
    try {
      const stats = await fetchStats();
      set({ stats });
    } finally {
      set({ statsLoading: false });
    }
  },

  loadRanking: async (req: RankRequest) => {
    set({ rankingLoading: true });
    try {
      const ranking = await fetchPaperRanking(req);
      set({ ranking });
    } catch (e) {
      console.warn("[PaperForge] 综合评分失败", e);
    } finally {
      set({ rankingLoading: false });
    }
  },

  clearRanking: () => set({ ranking: null }),
}));
