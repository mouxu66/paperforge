import http from './client'
import type {
  CitationRelations,
  Note,
  NoteCreate,
  NoteUpdate,
} from './types'

/** 获取某篇论文的所有笔记（按更新时间倒序） */
export async function fetchNotesByPaper(paperId: string): Promise<Note[]> {
  const { data } = await http.get<Note[]>(`/papers/${paperId}/notes`)
  return data
}

/** 获取某写作项目关联的所有笔记 */
export async function fetchNotesByProject(projectId: number): Promise<Note[]> {
  const { data } = await http.get<Note[]>(`/projects/${projectId}/notes`)
  return data
}

/** 新建笔记（可关联写作项目），论文不存在时后端返回 404 */
export async function createNote(payload: NoteCreate): Promise<Note> {
  const { data } = await http.post<Note>('/papers/notes', payload)
  return data
}

/** 更新笔记内容，不存在时后端返回 404 */
export async function updateNote(
  noteId: string,
  payload: NoteUpdate,
): Promise<Note> {
  const { data } = await http.put<Note>(`/papers/notes/${noteId}`, payload)
  return data
}

/** 删除笔记 */
export async function deleteNote(noteId: string): Promise<void> {
  await http.delete(`/papers/notes/${noteId}`)
}

/**
 * 获取论文引用关系。
 * - citations：被引用次数（papers.citations 字段）
 * - references：基于语义相似度推荐的相关论文列表
 */
export async function fetchCitationRelations(
  paperId: string,
): Promise<CitationRelations> {
  const { data } = await http.get<CitationRelations>(
    `/papers/${paperId}/citations`,
  )
  return data
}
