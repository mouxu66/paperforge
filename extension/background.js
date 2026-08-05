importScripts("utils.js");

const DEFAULT_API_BASE = "http://localhost:8770";

/**
 * 从 storage 读取用户配置的后端地址。
 */
async function getApiBase() {
  try {
    const result = await chrome.storage.local.get("apiBase");
    return result.apiBase || DEFAULT_API_BASE;
  } catch {
    return DEFAULT_API_BASE;
  }
}

/**
 * 按标题在后端查重。
 * 失败时不抛错，返回 null，避免阻塞保存流程。
 */
async function checkDuplicate(title, apiBase, signal) {
  if (!title) return null;
  try {
    const searchUrl = `${apiBase}/api/papers?keyword=${encodeURIComponent(title)}&page_size=3`;
    const searchResp = await fetch(searchUrl, { signal });
    if (!searchResp.ok) return null;
    const searchData = await searchResp.json().catch(() => ({}));
    const items = searchData.items || [];
    const titleLower = title.toLowerCase().trim();
    const match = items.find(
      (p) => (p.title || "").toLowerCase().trim() === titleLower
    );
    if (match) {
      return {
        alreadyExists: true,
        existingPaperId: match.id,
        existingTitle: match.title,
      };
    }
  } catch {
    // 查重失败不阻塞保存
  }
  return null;
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  // WP-4.1b: 原始 PDF 字节直传（知网浏览器登录态下载后上传）
  if (request.action === "save_raw") {
    handleSaveRaw(request, sendResponse);
    return true;
  }

  if (request.action !== "save_url") {
    return false;
  }

  const url = request.url;
  if (!url) {
    sendResponse({ success: false, error: "No URL provided" });
    return true;
  }
  if (!isValidClipperUrl(url)) {
    sendResponse({
      success: false,
      error: "Only arXiv / CNKI / Google Scholar pages are supported",
    });
    return true;
  }

  (async () => {
    const FETCH_TIMEOUT_MS = 30000; // P0-2: MV3 SW 休眠超时兜底
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

    try {
      const apiBase = await getApiBase();
      const body = {
        url,
        title: request.title,
        authors: request.authors,
        abstract: request.abstract,
        year: request.year,
        journal: request.journal,
        pdf_url: request.pdfUrl,
        source: request.source,
        tags: request.tags || [],
      };

      // P1-6: 查重反馈 — 保存前检查论文是否已在库
      const duplicateInfo = await checkDuplicate(
        request.title,
        apiBase,
        controller.signal
      );

      const resp = await fetch(`${apiBase}/api/ingest/url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      const data = await resp.json().catch(() => ({}));

      if (resp.ok && data.paperId) {
        sendResponse({
          success: true,
          paperId: data.paperId,
          title: data.title,
          mode: data.mode || "pdf",
          message: data.message || "",
          ...(duplicateInfo || {}),
        });
      } else {
        sendResponse({
          success: false,
          error: data.detail || data.message || `HTTP ${resp.status}`,
        });
      }
    } catch (err) {
      sendResponse({
        success: false,
        error:
          err.name === "AbortError"
            ? "Request timed out — the server may be busy. Please try again."
            : err.toString(),
      });
    } finally {
      clearTimeout(timeoutId);
    }
  })();

  return true;
});

/**
 * WP-4.1b: 处理 save_raw —— 把 content script 下载好的 PDF 字节以
 * multipart/form-data 上传到后端 /api/ingest/raw。
 *
 * MV3 service worker 没有 DOM，但可用 Blob + FormData 构造 multipart 请求体。
 * pdfBuffer 来自 content script，是 ArrayBuffer（结构化克隆可跨消息传递）。
 */
async function handleSaveRaw(request, sendResponse) {
  if (!request.pdfBuffer) {
    sendResponse({ success: false, error: "No PDF buffer provided" });
    return;
  }

  const FETCH_TIMEOUT_MS = 60000; // PDF 上传+解析较慢
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);

  try {
    const apiBase = await getApiBase();

    // 查重反馈（与 save_url 一致）
    const duplicateInfo = await checkDuplicate(
      request.title,
      apiBase,
      controller.signal
    );

    // 构造 multipart/form-data：file + 元数据字段
    const blob = new Blob([request.pdfBuffer], { type: "application/pdf" });
    const formData = new FormData();
    formData.append("file", blob, request.filename || "cnki_paper.pdf");
    formData.append("url", request.url || "");
    formData.append("title", request.title || "");
    formData.append("authors", JSON.stringify(request.authors || []));
    formData.append("abstract", request.abstract || "");
    formData.append("year", String(request.year || 0));
    formData.append("journal", request.journal || "");
    formData.append("source", request.source || "cnki");
    formData.append("tags", JSON.stringify(request.tags || []));

    const resp = await fetch(`${apiBase}/api/ingest/raw`, {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });

    const data = await resp.json().catch(() => ({}));

    if (resp.ok && data.paperId) {
      sendResponse({
        success: true,
        paperId: data.paperId,
        title: data.title,
        mode: data.mode || "pdf",
        message: data.message || "",
        ...(duplicateInfo || {}),
      });
    } else {
      sendResponse({
        success: false,
        error: data.detail || data.message || `HTTP ${resp.status}`,
      });
    }
  } catch (err) {
    sendResponse({
      success: false,
      error:
        err.name === "AbortError"
          ? "Request timed out — the server may be busy. Please try again."
          : err.toString(),
    });
  } finally {
    clearTimeout(timeoutId);
  }
}
