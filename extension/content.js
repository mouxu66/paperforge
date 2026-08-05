(() => {
  "use strict";

  // 避免重复注入（右下角悬浮按钮）
  if (document.getElementById("pf-clipper-btn")) return;

  // ---------------------------------------------------------------------------
  // 通用工具
  // ---------------------------------------------------------------------------
  function sanitizeText(text) {
    if (!text) return "";
    return text.replace(/\s+/g, " ").trim();
  }

  function getMetaContent(selector) {
    const el = document.querySelector(selector);
    return el ? sanitizeText(el.getAttribute("content") || el.textContent) : "";
  }

  function extractYear(text) {
    if (!text) return 0;
    const match = text.match(/\b(19|20)\d{2}\b/);
    return match ? parseInt(match[0], 10) : 0;
  }

  function createFloatingButton(onClick) {
    const button = document.createElement("button");
    button.id = "pf-clipper-btn";
    button.innerText = "Save to PaperForge";
    button.style.cssText = [
      "position: fixed",
      "bottom: 24px",
      "right: 24px",
      "z-index: 9999",
      "padding: 12px 18px",
      "background: #2563eb",
      "color: #fff",
      "border: none",
      "border-radius: 8px",
      "font-family: sans-serif",
      "font-size: 14px",
      "font-weight: 600",
      "cursor: pointer",
      "box-shadow: 0 4px 12px rgba(0,0,0,0.15)",
    ].join(";");
    button.addEventListener("click", onClick);
    document.body.appendChild(button);
    return button;
  }

  function createInlineButton(text, onClick) {
    const button = document.createElement("button");
    button.innerText = text;
    button.dataset.originalText = text;
    button.className = "pf-clipper-inline";
    button.style.cssText = [
      "margin-left: 8px",
      "padding: 4px 10px",
      "background: #2563eb",
      "color: #fff",
      "border: none",
      "border-radius: 4px",
      "font-family: sans-serif",
      "font-size: 12px",
      "font-weight: 600",
      "cursor: pointer",
    ].join(";");
    button.addEventListener("click", onClick);
    return button;
  }

  function getButtonOriginalText(button) {
    return (
      button?.dataset?.originalText ||
      button?.innerText ||
      "Save to PaperForge"
    );
  }

  const ButtonState = {
    SAVING: "saving",
    FETCHING: "fetching",
    ALREADY_EXISTS: "alreadyExists",
    SAVED: "saved",
    SAVED_METADATA: "savedMetadata",
    ERROR: "error",
    TIMEOUT: "timeout",
  };

  function setButtonState(button, state) {
    if (!button) return;
    const states = {
      [ButtonState.SAVING]: { text: "Saving...", color: "#6b7280" },
      [ButtonState.FETCHING]: { text: "Fetching...", color: "#6b7280" },
      [ButtonState.ALREADY_EXISTS]: { text: "Already in library", color: "#f59e0b" },
      [ButtonState.SAVED]: { text: "Saved!", color: "#16a34a" },
      [ButtonState.SAVED_METADATA]: { text: "Saved (metadata)", color: "#16a34a" },
      [ButtonState.ERROR]: { text: "Error", color: "#dc2626" },
      [ButtonState.TIMEOUT]: { text: "Timeout", color: "#f59e0b" },
    };
    const preset = states[state];
    if (!preset) return;
    button.innerText = preset.text;
    if (button.style) button.style.background = preset.color;
  }

  function resetButton(button, originalText) {
    if (!button) return;
    button.innerText = originalText;
    if (button.style) button.style.background = "#2563eb";
    if (button?.dataset) delete button.dataset.pfResetTimer;
  }

  function clearPendingReset(button) {
    if (button?.dataset?.pfResetTimer) {
      clearTimeout(Number(button.dataset.pfResetTimer));
      delete button.dataset.pfResetTimer;
    }
  }

  function scheduleReset(button, originalText) {
    clearPendingReset(button);
    const timerId = setTimeout(() => resetButton(button, originalText), 4000);
    if (button) button.dataset.pfResetTimer = String(timerId);
  }

  function handleSaveResponse(button, response, originalText) {
    if (response?.alreadyExists) {
      setButtonState(button, ButtonState.ALREADY_EXISTS);
    } else if (response?.success) {
      setButtonState(
        button,
        response.mode === "metadata" ? ButtonState.SAVED_METADATA : ButtonState.SAVED
      );
    } else {
      setButtonState(button, ButtonState.ERROR);
      console.error("PaperForge Clipper error:", response?.error);
    }
    scheduleReset(button, originalText);
  }

  // ---------------------------------------------------------------------------
  // 站点适配器
  // ---------------------------------------------------------------------------
  const adapters = {
    arxiv: {
      detect: () => window.location.hostname.endsWith("arxiv.org"),
      extract: () => {
        const title =
          getMetaContent('meta[name="citation_title"]') ||
          getMetaContent('meta[property="og:title"]') ||
          document.title;
        const authors = Array.from(
          document.querySelectorAll('meta[name="citation_author"]')
        ).map((m) => sanitizeText(m.getAttribute("content")));
        const abstract =
          getMetaContent('meta[name="citation_abstract"]') ||
          getMetaContent('meta[property="og:description"]') ||
          "";
        const date = getMetaContent('meta[name="citation_date"]') || "";
        const year = date ? parseInt(date.split("/")[0], 10) || 0 : 0;
        const pdfUrl = window.location.href.replace("/abs/", "/pdf/") + ".pdf";
        return {
          title,
          authors,
          abstract,
          year,
          journal: "arXiv",
          pdfUrl,
          source: "arxiv",
        };
      },
    },

    cnki: {
      detect: () =>
        window.location.hostname.includes("cnki.net") ||
        window.location.hostname.includes("cnki.com.cn"),
      extract: () => {
        let title =
          getMetaContent('meta[name="citation_title"]') ||
          getMetaContent('meta[name="DC.Title"]');
        if (!title) {
          const titleEl =
            document.querySelector(".wx_tit h1") ||
            document.querySelector(".title h1") ||
            document.querySelector("#Title") ||
            document.querySelector("h1.title");
          title = titleEl ? sanitizeText(titleEl.textContent) : "";
        }
        if (!title) {
          title = document.title;
        }

        let authors = [];
        const authorMeta = document.querySelectorAll(
          'meta[name="citation_author"], meta[name="DC.Creator"]'
        );
        if (authorMeta.length > 0) {
          authors = Array.from(authorMeta)
            .map((el) => sanitizeText(el.getAttribute("content")))
            .filter(Boolean);
        } else {
          const authorEl = document.querySelector(
            "#authorpart, .author, .wx_author, .author-info"
          );
          if (authorEl) {
            authors = authorEl.textContent
              .split(/[,;]/)
              .map((a) => sanitizeText(a))
              .filter(Boolean);
          }
        }

        let abstract =
          getMetaContent('meta[name="citation_abstract"]') ||
          getMetaContent('meta[name="DC.Description"]') ||
          "";
        if (!abstract) {
          const absEl = document.querySelector(
            "#ChDivSummary, .abstract-text, .wx_abstract, #abstract"
          );
          if (absEl) abstract = sanitizeText(absEl.textContent);
        }

        const year =
          parseInt(
            getMetaContent('meta[name="citation_date"]') ||
              getMetaContent('meta[name="DC.Date"]') ||
              "",
            10
          ) || extractYear(document.title);

        const journal =
          getMetaContent('meta[name="citation_journal_title"]') ||
          getMetaContent('meta[name="DC.Publisher"]') ||
          "";

        const pdfLink = document.querySelector("#pdfDown, a.pdf-download");
        const pdfUrl = pdfLink ? pdfLink.href : "";

        return {
          title,
          authors,
          abstract,
          year,
          journal,
          pdfUrl,
          source: "cnki",
        };
      },
    },

    googleScholar: {
      detect: () => window.location.hostname.startsWith("scholar.google"),
      extract: () => {
        const titleEl =
          document.querySelector("h3.gs_rt a") ||
          document.querySelector("#gsc_oci_title a") ||
          document.querySelector("#gsc_oci_title");
        const title = titleEl ? sanitizeText(titleEl.textContent) : document.title;

        let authors = [];
        let year = 0;
        let journal = "";
        const infoEl =
          document.querySelector(".gs_a") ||
          document.querySelector(".gsc_oci_field") ||
          document.querySelector("[class*='gs_ri']");
        if (infoEl) {
          const infoText = infoEl.textContent || "";
          year = extractYear(infoText);
          const parts = infoText.split(" - ");
          if (parts.length > 0) {
            authors = parts[0]
              .split(",")
              .map((a) => sanitizeText(a))
              .filter(Boolean);
          }
          if (parts.length > 1) {
            journal = sanitizeText(parts[1]);
          }
        }

        let abstract = "";
        const absEl =
          document.querySelector(".gs_rs") ||
          document.querySelector(".gsc_oci_value") ||
          document.querySelector("[class*='abstract']");
        if (absEl) abstract = sanitizeText(absEl.textContent);

        let pdfUrl = "";
        const pdfLink =
          document.querySelector(".gs_or_ggsm a") ||
          document.querySelector("a[href*='pdf']") ||
          document.querySelector("[class*='pdf'] a");
        if (pdfLink) pdfUrl = pdfLink.href;

        return {
          title,
          authors,
          abstract,
          year,
          journal,
          pdfUrl,
          source: "google_scholar",
        };
      },
      injectPerResult: true,
    },
  };

  // ---------------------------------------------------------------------------
  // 检测当前站点
  // ---------------------------------------------------------------------------
  function detectAdapter() {
    for (const [name, adapter] of Object.entries(adapters)) {
      if (adapter.detect()) {
        return { name, adapter };
      }
    }
    return null;
  }

  const detected = detectAdapter();
  if (!detected) {
    return;
  }

  const { name: sourceName, adapter } = detected;

  // ---------------------------------------------------------------------------
  // CNKI PDF 直传模式（浏览器内登录态下载 + 字节直传后端）
  // ---------------------------------------------------------------------------
  // WP-4.1b: 知网 PDF 通常需要登录/机构权限，后端无法直接下载。
  // 用户在扩展 popup 中开启「允许导入知网 PDF」后，content script 会：
  // 1. 在知网页面显示「Save to PaperForge」悬浮按钮
  // 2. 用户点击时，在页面上下文内 fetch(pdfUrl, {credentials:'include'})
  //    借浏览器当前登录态把 PDF 下载为 blob（不触发下载对话框）
  // 3. 把 PDF 字节 + 元数据交给 background → POST /api/ingest/raw
  //
  // 安全约束：仅在用户主动点击时触发，默认关闭，不做任何自动/批量导入。

  const CNKI_PDF_TOGGLE_KEY = "cnkiPdfImportEnabled";

  function isCnkiRawImportEnabled() {
    return new Promise((resolve) => {
      if (!chrome?.storage?.local) {
        resolve(false);
        return;
      }
      chrome.storage.local.get(CNKI_PDF_TOGGLE_KEY, (result) => {
        resolve(!!result[CNKI_PDF_TOGGLE_KEY]);
      });
    });
  }

  /**
   * 在页面上下文内用浏览器登录态下载 PDF 为 ArrayBuffer。
   * 仅对同源/已授权的知网 PDF 链接生效；失败时抛错由调用方处理。
   */
  async function fetchPdfInPage(pdfUrl) {
    const resp = await fetch(pdfUrl, {
      method: "GET",
      credentials: "include",
      redirect: "follow",
    });
    if (!resp.ok) {
      throw new Error(`PDF 下载失败：HTTP ${resp.status}`);
    }
    const buffer = await resp.arrayBuffer();
    if (!buffer || buffer.byteLength < 4) {
      throw new Error("PDF 下载内容为空或过小");
    }
    return buffer;
  }

  /**
   * 发送原始 PDF 字节到后端 /api/ingest/raw（经 background 中转）。
   */
  function sendSaveRaw(metadata, buffer, button) {
    const originalText = getButtonOriginalText(button);
    clearPendingReset(button);
    setButtonState(button, ButtonState.SAVING);

    const payload = {
      action: "save_raw",
      url: window.location.href,
      pdfBuffer: buffer,
      filename: (metadata.title || "cnki_paper") + ".pdf",
      title: metadata.title || "",
      authors: metadata.authors || [],
      abstract: metadata.abstract || "",
      year: metadata.year || 0,
      journal: metadata.journal || "",
      source: metadata.source || "cnki",
      tags: metadata.tags || [],
    };

    const SEND_TIMEOUT_MS = 60000; // PDF 上传+解析较慢，给更长超时

    return new Promise((resolve) => {
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        setButtonState(button, ButtonState.TIMEOUT);
        scheduleReset(button, originalText);
        resolve({ success: false, error: "Request timed out — the server may be busy." });
      }, SEND_TIMEOUT_MS);

      chrome.runtime.sendMessage(payload, (response) => {
        if (timedOut) return;
        clearTimeout(timer);

        handleSaveResponse(button, response, originalText);
        resolve(response);
      });
    });
  }

  /**
   * 知网保存处理：优先尝试浏览器登录态下载真 PDF，失败降级到 save_url。
   */
  async function handleCnkiSave(metadata, button) {
    const originalText = getButtonOriginalText(button);
    clearPendingReset(button);
    setButtonState(button, ButtonState.FETCHING);

    // 无 PDF 链接 → 直接走元数据保存
    if (!metadata.pdfUrl) {
      return sendSave(metadata, button);
    }

    try {
      const buffer = await fetchPdfInPage(metadata.pdfUrl);
      // 校验 PDF 魔数（防止知网返回登录页 HTML）
      const header = new Uint8Array(buffer.slice(0, 5));
      const isPdf =
        header[0] === 0x25 && // '%'
        header[1] === 0x50 && // 'P'
        header[2] === 0x44 && // 'D'
        header[3] === 0x46 && // 'F'
        header[4] === 0x2d;   // '-'
      if (!isPdf) {
        throw new Error("下载内容不是有效 PDF（可能是登录页/付费墙）");
      }
      await sendSaveRaw(metadata, buffer, button);
    } catch (err) {
      console.warn("PaperForge Clipper: 浏览器内 PDF 下载失败，降级保存元数据:", err);
      // 降级到 save_url（后端尝试下载，失败则保存元数据）
      resetButton(button, originalText);
      return sendSave(metadata, button);
    }
  }

  // ---------------------------------------------------------------------------
  // 发送保存请求
  // ---------------------------------------------------------------------------
  function sendSave(metadata, button, urlOverride) {
    const originalText = getButtonOriginalText(button);
    clearPendingReset(button);
    setButtonState(button, ButtonState.SAVING);

    const payload = {
      action: "save_url",
      url: urlOverride || window.location.href,
      ...metadata,
    };

    const SEND_TIMEOUT_MS = 35000; // P0-2: 略长于 background 30s 超时，防止 SW 休眠后 Promise 永远 pending

    return new Promise((resolve) => {
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        setButtonState(button, "timeout");
        setTimeout(() => resetButton(button, originalText), 4000);
        resolve({ success: false, error: "Request timed out — the server may be busy. Please try again." });
      }, SEND_TIMEOUT_MS);

      chrome.runtime.sendMessage(payload, (response) => {
        if (timedOut) return;
        clearTimeout(timer);

        handleSaveResponse(button, response, originalText);
        resolve(response);
      });
    });
  }

  // ---------------------------------------------------------------------------
  // 注入按钮
  // ---------------------------------------------------------------------------
  if (sourceName === "googleScholar" && adapter.injectPerResult) {
    const results = document.querySelectorAll(".gs_ri");
    results.forEach((result) => {
      // 去重：每个结果只注入一次
      if (result.querySelector(".pf-clipper-inline")) return;

      const titleLink = result.querySelector("h3.gs_rt a");
      const pdfLink = result.querySelector(".gs_or_ggsm a");
      const infoEl = result.querySelector(".gs_a");
      const absEl = result.querySelector(".gs_rs");

      const title = titleLink ? sanitizeText(titleLink.textContent) : "";
      const paperUrl = titleLink ? titleLink.href : window.location.href;
      let authors = [];
      let year = 0;
      let journal = "";
      if (infoEl) {
        const infoText = infoEl.textContent || "";
        year = extractYear(infoText);
        const parts = infoText.split(" - ");
        if (parts.length > 0) {
          authors = parts[0]
            .split(",")
            .map((a) => sanitizeText(a))
            .filter(Boolean);
        }
        if (parts.length > 1) journal = sanitizeText(parts[1]);
      }
      const abstract = absEl ? sanitizeText(absEl.textContent) : "";
      const pdfUrl = pdfLink ? pdfLink.href : "";

      const metadata = {
        title,
        authors,
        abstract,
        year,
        journal,
        pdfUrl,
        source: "google_scholar",
      };

      const btn = createInlineButton("Save to PF", () => sendSave(metadata, btn, paperUrl));
      const titleContainer = result.querySelector("h3.gs_rt");
      if (titleContainer) {
        titleContainer.appendChild(btn);
      } else {
        result.appendChild(btn);
      }
    });
  } else {
    const metadata = adapter.extract();
    // CNKI：按钮默认隐藏，仅在用户开启「允许导入知网 PDF」时显示
    if (sourceName === "cnki") {
      isCnkiRawImportEnabled().then((enabled) => {
        if (!enabled) return;
        const btn = createFloatingButton((event) => {
          const button = event.currentTarget;
          handleCnkiSave(metadata, button);
        });
        // 标记以便 popup 切换后能动态刷新
        btn.dataset.pfCnki = "1";
      });
    } else {
      createFloatingButton((event) => {
        const button = event.currentTarget;
        sendSave(metadata, button);
      });
    }
  }

  // ---------------------------------------------------------------------------
  // 监听 popup 等发来的保存请求
  // ---------------------------------------------------------------------------
  chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action !== "save_url") {
      sendResponse({ success: false, error: "Unknown action" });
      return false;
    }
    const metadata = adapter.extract ? adapter.extract() : {};
    // P1-7: 透传 popup 中用户输入的 Tags
    if (request.tags && Array.isArray(request.tags)) {
      metadata.tags = request.tags;
    }
    const button = document.getElementById("pf-clipper-btn");
    // CNKI 直传模式：popup 触发时也走 handleCnkiSave（按钮已存在的前提是开关已开）
    if (sourceName === "cnki" && button) {
      handleCnkiSave(metadata, button).then(sendResponse);
      return true;
    }
    sendSave(metadata, button || { innerText: "", style: {} }).then(sendResponse);
    return true;
  });

  // ---------------------------------------------------------------------------
  // 监听 popup 开关变更：动态显示/隐藏 CNKI 按钮（无需刷新页面）
  // ---------------------------------------------------------------------------
  if (chrome?.storage?.onChanged) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "local" || !(CNKI_PDF_TOGGLE_KEY in changes)) return;
      const enabled = !!changes[CNKI_PDF_TOGGLE_KEY].newValue;
      const existing = document.getElementById("pf-clipper-btn");
      if (sourceName !== "cnki") return;
      if (enabled && !existing) {
        const metadata = adapter.extract ? adapter.extract() : {};
        const btn = createFloatingButton((event) => {
          const button = event.currentTarget;
          handleCnkiSave(metadata, button);
        });
        btn.dataset.pfCnki = "1";
      } else if (!enabled && existing && existing.dataset?.pfCnki === "1") {
        existing.remove();
      }
    });
  }
})();
