import http, { createHttpClient } from './client'
import type { LibraryStats, PageResult, Paper, PaperQuery, SuggestItem } from './types'

// 语义搜索专用实例：30s 超时，不挂载拦截器（调用方自行 try/catch 降级到 FTS5）
const pfHttp = createHttpClient({ timeout: 30000, withInterceptor: false })

/** 分页查询论文（支持关键词过滤、分类、排序） */
export async function fetchPapers(q: PaperQuery): Promise<PageResult<Paper>> {
  const { data } = await http.get<PageResult<Paper>>('/papers', { params: q })
  return data
}

/** 论文详情 */
export async function fetchPaperById(id: string): Promise<Paper | null> {
  const { data } = await http.get<Paper | null>(`/papers/${id}`)
  return data
}

/** 收藏列表（后端返回完整 Paper[]） */
export async function fetchFavorites(): Promise<Paper[]> {
  const { data } = await http.get<Paper[]>('/favorites')
  return data
}

/** 添加收藏（持久化到后端 SQLite） */
export async function addFavorite(paperId: string): Promise<void> {
  await http.post('/favorites', { paper_id: paperId })
}

/** 取消收藏 */
export async function removeFavorite(paperId: string): Promise<void> {
  await http.delete(`/favorites/${paperId}`)
}

/** 搜索建议（按标题模糊匹配，返回前 5 条 {id, title}） */
export async function fetchSuggest(q: string): Promise<SuggestItem[]> {
  const { data } = await http.get<SuggestItem[]>('/search/suggest', { params: { q } })
  return data
}

/** 库统计 */
export async function fetchStats(): Promise<LibraryStats> {
  const { data } = await http.get<LibraryStats>('/stats')
  return data
}

/**
 * 语义搜索（调用 /api/search/semantic）。
 * 返回格式与 fetchPapers 完全一致，可直接复用 PaperList 渲染。
 * 调用方应 try/catch 并在失败时降级为 fetchPapers（FTS5 关键词搜索）。
 */
export async function fetchSemanticSearch(keyword: string): Promise<Paper[]> {
  const { data } = await pfHttp.post<Paper[]>('/search/semantic', { question: keyword })
  return data
}
