import http from './client'
import type { LLMConfig, LLMConfigCreate, LLMConfigUpdate } from './types'

/** 列出所有模型配置 */
export async function fetchModels(): Promise<LLMConfig[]> {
  const { data } = await http.get<LLMConfig[]>('/models')
  return data
}

/** 新增模型配置 */
export async function createModel(payload: LLMConfigCreate): Promise<LLMConfig> {
  const { data } = await http.post<LLMConfig>('/models', payload)
  return data
}

/** 更新模型配置（apiKey 为空字符串时不更新） */
export async function updateModel(
  id: number,
  payload: LLMConfigUpdate,
): Promise<LLMConfig> {
  const { data } = await http.put<LLMConfig>(`/models/${id}`, payload)
  return data
}

/** 删除模型配置 */
export async function deleteModel(id: number): Promise<void> {
  await http.delete(`/models/${id}`)
}
