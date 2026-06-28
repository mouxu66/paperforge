import { createHttpClient } from './client'
import type { AskRequest, AskResponse, AskReference } from './types'

// 问答专用实例：RAG 检索+生成，超时放宽至 60s
const askHttp = createHttpClient({ timeout: 60000, defaultMessage: '问答请求失败' })

/** 调用 /ask 接口进行论文问答（非流式） */
export async function askPaper(req: AskRequest): Promise<AskResponse> {
  const { data } = await askHttp.post<AskResponse>('/ask', req)
  return data
}

// ---------------------------------------------------------------------------
// SSE 流式问答（POST /ask/stream）
// ---------------------------------------------------------------------------

export interface StreamCallbacks {
  onRefs?: (refs: AskReference[]) => void
  onToken?: (token: string) => void
  onError?: (error: string) => void
  onDone?: (model: string) => void
}

/**
 * 流式问答：通过 fetch + ReadableStream 消费 SSE 事件。
 *
 * 后端事件格式：
 *   data: {"type": "refs",  "data": "[...]"}   ← 参考文献列表
 *   data: {"type": "token", "data": "文本片段"} ← 逐 token 输出
 *   data: {"type": "error", "data": "错误信息"} ← 友好错误
 *   data: {"type": "done",  "data": "模型名"}   ← 流结束
 */
export async function askPaperStream(
  req: AskRequest,
  callbacks: StreamCallbacks,
): Promise<void> {
  const baseURL =
    import.meta.env.VITE_API_BASE || import.meta.env.VITE_ASK_API_BASE || '/api'
  const resp = await fetch(`${baseURL}/ask/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    throw new Error(err.detail || '流式请求失败')
  }

  const reader = resp.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // SSE 事件以 \n\n 分隔
    const events = buffer.split('\n\n')
    buffer = events.pop() || ''

    for (const evt of events) {
      if (!evt.startsWith('data: ')) continue
      try {
        const parsed = JSON.parse(evt.slice(6))
        switch (parsed.type) {
          case 'refs':
            callbacks.onRefs?.(JSON.parse(parsed.data))
            break
          case 'token':
            callbacks.onToken?.(parsed.data)
            break
          case 'error':
            callbacks.onError?.(parsed.data)
            break
          case 'done':
            callbacks.onDone?.(parsed.data)
            break
        }
      } catch {
        /* 跳过格式异常的事件 */
      }
    }
  }
}
