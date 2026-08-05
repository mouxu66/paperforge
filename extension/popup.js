/* global detectSource */

const saveBtn = document.getElementById("saveBtn");
const statusEl = document.getElementById("status");
const sourceBadge = document.getElementById("sourceBadge");
const sourceDesc = document.getElementById("sourceDesc");
const apiBaseInput = document.getElementById("apiBase");
const saveConfigBtn = document.getElementById("saveConfig");
const tagsInput = document.getElementById("tags");
const cnkiPdfToggle = document.getElementById("cnkiPdfToggle");

const CNKI_PDF_TOGGLE_KEY = "cnkiPdfImportEnabled";

// Guard against running outside the extension context (e.g., tests)
if (typeof chrome === "undefined" || !chrome.storage || !chrome.tabs) {
  // eslint-disable-next-line no-console
  console.warn("PaperForge Clipper popup: chrome APIs are not available");
  // Disable UI to avoid runtime errors
  if (saveBtn) saveBtn.disabled = true;
  if (saveConfigBtn) saveConfigBtn.disabled = true;
  if (statusEl) statusEl.textContent = "Extension APIs unavailable";
  return;
}

const SOURCE_LABELS = {
  arxiv: { label: "arXiv", class: "source-arxiv", desc: "Save this arXiv paper to PaperForge." },
  cnki: { label: "CNKI 知网", class: "source-cnki", desc: "Save this CNKI paper to PaperForge." },
  google_scholar: { label: "Google Scholar", class: "source-scholar", desc: "Save this Google Scholar result to PaperForge." },
  other: { label: "Other", class: "source-other", desc: "Save the current page to PaperForge." },
};

function updateSourceBadge(url) {
  const source = detectSource(url);
  const info = SOURCE_LABELS[source] || SOURCE_LABELS.other;
  sourceBadge.textContent = info.label;
  sourceBadge.className = `source-badge ${info.class}`;
  sourceDesc.textContent = info.desc;
  return source;
}

function setStatus(text, type) {
  statusEl.textContent = text;
  statusEl.className = type || "";
}

function setLoading(isLoading) {
  saveBtn.disabled = isLoading;
  saveBtn.textContent = isLoading ? "Saving..." : "Save Current Page";
}

// 加载保存的 API 地址，校验合法性
chrome.storage.local.get("apiBase", (result) => {
  if (result.apiBase) {
    apiBaseInput.value = result.apiBase;
    if (!isValidLocalUrl(result.apiBase)) {
      setStatus(
        "Warning: stored API address is not localhost:8770. Requests may be blocked.",
        "info"
      );
    }
  }
});

// WP-4.1b: 加载知网 PDF 导入开关状态（默认关闭）
chrome.storage.local.get(CNKI_PDF_TOGGLE_KEY, (result) => {
  if (cnkiPdfToggle) {
    cnkiPdfToggle.checked = !!result[CNKI_PDF_TOGGLE_KEY];
  }
});

// WP-4.1b: 开关变更时立即持久化（content script 监听 storage.onChanged 动态显隐按钮）
if (cnkiPdfToggle) {
  cnkiPdfToggle.addEventListener("change", () => {
    chrome.storage.local.set({ [CNKI_PDF_TOGGLE_KEY]: cnkiPdfToggle.checked }, () => {
      setStatus(
        cnkiPdfToggle.checked
          ? "知网 PDF 导入已开启"
          : "知网 PDF 导入已关闭",
        "info"
      );
      setTimeout(() => setStatus("", ""), 2000);
    });
  });
}

// P0-1: 锁定本地地址，防止用户改 apiBase 后 host_permissions 不匹配导致静默失败
function isValidLocalUrl(url) {
  try {
    const u = new URL(url);
    return (
      (u.hostname === "localhost" || u.hostname === "127.0.0.1") &&
      u.port === "8770"
    );
  } catch {
    return false;
  }
}

// 检测当前标签页来源
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const url = tabs[0]?.url || "";
  updateSourceBadge(url);
});

saveConfigBtn.addEventListener("click", () => {
  const apiBase = apiBaseInput.value.trim();
  if (!isValidLocalUrl(apiBase)) {
    setStatus(
      "API address must be http://localhost:8770 (local only)",
      "error"
    );
    return;
  }
  chrome.storage.local.set({ apiBase }, () => {
    setStatus("Settings saved", "success");
    setTimeout(() => setStatus("", ""), 2000);
  });
});

saveBtn.addEventListener("click", async () => {
  setLoading(true);
  setStatus("Saving...", "info");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.url) {
      setStatus("No active tab URL", "error");
      setLoading(false);
      return;
    }

    // P0-1: 保存前校验 apiBase 合法性，防止 MV3 host_permissions 拦截导致静默失败
    const stored = await chrome.storage.local.get("apiBase");
    const apiBase = stored.apiBase || "http://localhost:8770";
    if (!isValidLocalUrl(apiBase)) {
      setStatus(
        "API address is not localhost:8770 — request will be blocked. Fix in settings.",
        "error"
      );
      setLoading(false);
      return;
    }

    const tags = (tagsInput?.value || "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    chrome.tabs.sendMessage(tab.id, { action: "save_url", url: tab.url, tags }, (response) => {
      if (chrome.runtime.lastError) {
        // 内容脚本未注入或不可用时，降级为只传 URL
        chrome.runtime.sendMessage({ action: "save_url", url: tab.url, tags }, handleResponse);
        return;
      }
      handleResponse(response);
    });
  } catch (err) {
    setStatus(err.toString(), "error");
    setLoading(false);
  }
});

function handleResponse(response) {
  setLoading(false);
  if (!response) {
    setStatus("No response from extension", "error");
    return;
  }
  if (response.alreadyExists) {
    setStatus(
      `Already in library: ${response.existingTitle || response.title}`,
      "info"
    );
    return;
  }
  if (response.success) {
    let text = `Saved: ${response.title}`;
    if (response.mode === "metadata") {
      text += " (metadata only)";
    }
    setStatus(text, "success");
  } else {
    setStatus(response?.error || "Save failed", "error");
  }
}
