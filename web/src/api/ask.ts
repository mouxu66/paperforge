import { API_BASE, createHttpClient, getAuthHeaders } from "./client";
import type { AskRequest, AskResponse, AskReference } from "./types";

// 问答专用实例：RAG 检索+生成，超时放宽至 60s
const askHttp = createHttpClient({ timeout: 60000, defaultMessage: "问答请求失败" });

/** 调用 /ask 接口进行论文问答（非流式） */
export async function askPaper(req: AskRequest): Promise<AskResponse> {
  const { data } = await askHttp.post<AskResponse>("/ask", req);
  return data;
}

// ---------------------------------------------------------------------------
// SSE 流式问答（POST /ask/stream）
// ---------------------------------------------------------------------------

export interface StreamCallbacks {
  onRefs?: (refs: AskReference[]) => void;
  onToken?: (token: string) => void;
  onError?: (error: string) => void;
  onDone?: (model: string) => void;
}

export interface AskStreamOptions {
  /**
   * 流式响应整体超时（毫秒）。超时后中止请求并触发 onError + 一个 "done" 兜底，
   * 确保 UI 不会无限停留在打字机状态。默认 120000（2 分钟）。
   */
  timeoutMs?: number;
  /** 超时时触发的错误文案 key（由调用方 i18n 解析）。默认直接抛超时错误。 */
}

/**
 * 流式问答：通过 fetch + ReadableStream 消费 SSE 事件。
 *
 * 后端事件格式：
 *   data: {"type": "refs",  "data": "[...]"}   ← 参考文献列表
 *   data: {"type": "token", "data": "文本片段"} ← 逐 token 输出
 *   data: {"type": "error", "data": "错误信息"} ← 友好错误
 *   data: {"type": "done",  "data": "模型名"}   ← 流结束
 *
 * 兜底保证（修复 "onDone 不触发导致 UI 卡死" 的真实缺陷）：
 * - 流自然结束（reader.read() done=true）但未收到 `done` 事件时，
 *   以空模型名触发一次 onDone，让调用方把已累积文本落库 + 退出流式态。
 * - 提供整体超时 timeoutMs：超时自动 abort 并触发 onError + onDone 兜底，
 *   避免本地大模型卡住时前端永久转圈。
 * - 非 2xx 响应体优先解析后端 `detail` 字段，给出可读错误而非裸 "流式请求失败"。
 */
export async function askPaperStream(
  req: AskRequest,
  callbacks: StreamCallbacks,
  options: AskStreamOptions = {},
): Promise<void> {
  const baseURL = API_BASE;
  const timeoutMs = options.timeoutMs ?? 120_000;
  const controller = new AbortController();
  let receivedTerminal = false;
  let firstByteSeen = false;

  const fireTerminal = (model: string) => {
    if (receivedTerminal) return;
    receivedTerminal = true;
    callbacks.onDone?.(model);
  };

  const timer = setTimeout(() => {
    if (!receivedTerminal) {
      try {
        controller.abort();
      } catch {
        /* 已关闭则忽略 */
      }
      // 传 i18n key，由调用方（AskPage）翻译；保持与其它错误一致的契约。
      callbacks.onError?.("streamTimeout");
      fireTerminal("");
    }
  }, timeoutMs);

  try {
    const resp = await fetch(`${baseURL}/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeaders() },
      body: JSON.stringify(req),
      signal: controller.signal,
    });

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      // 后端 /api/ask/stream 在未配置模型时返回 502 + { detail, error_code }
      const detail = err?.detail || err?.message || "流式请求失败";
      callbacks.onError?.(detail);
      fireTerminal("");
      return;
    }

    if (!resp.body) {
      callbacks.onError?.("流式响应无 body");
      fireTerminal("");
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      firstByteSeen = true;
      buffer += decoder.decode(value, { stream: true });

      // SSE 事件以 \n\n 分隔
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";

      for (const evt of events) {
        if (!evt.startsWith("data: ")) continue;
        try {
          const parsed = JSON.parse(evt.slice(6));
          switch (parsed.type) {
            case "refs":
              callbacks.onRefs?.(JSON.parse(parsed.data));
              break;
            case "token":
              callbacks.onToken?.(parsed.data);
              break;
            case "error":
              callbacks.onError?.(parsed.data);
              fireTerminal("");
              return;
            case "done":
              fireTerminal(parsed.data ?? "");
              return;
          }
        } catch {
          /* 跳过格式异常的事件，继续读后续事件 */
        }
      }
    }

    // 流自然结束但未收到 done 事件（连接被对端关闭 / 后端漏发 done）
    // 兜底触发一次 onDone，让 UI 把已累积文本落库并退出流式态。
    if (!receivedTerminal) {
      if (!firstByteSeen) {
        // 一个字节都没收到就结束 —— 大概率是后端 502/异常但 fetch 仍 resolve
        callbacks.onError?.("streamUnexpectedEnd");
      }
      // 有部分 token 但后端漏发 done：静默兜底触发 onDone，让已累积文本落库。
      fireTerminal("");
    }
  } catch (err0: unknown) {
    const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
    if (e?.name === "AbortError") {
      // 超时路径已在 timer 里触发 onError + fireTerminal，这里不再重复
      return;
    }
    const detail = e?.message || "流式请求失败";
    callbacks.onError?.(detail);
    fireTerminal("");
  } finally {
    clearTimeout(timer);
  }
}

// 错误契约：前端原产错误统一传 i18n key（streamTimeout / streamUnexpectedEnd / streamAborted），
// 由 AskPage 翻译；后端错误（HTTP 非 2xx、SSE error 事件）直接传原始 detail 文案。
