const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert");

// Minimal DOM mock for popup.js
function createMockElement(tag, attrs = {}) {
  const listeners = [];
  const el = {
    tagName: tag.toUpperCase(),
    textContent: attrs.textContent || "",
    className: attrs.className || "",
    value: attrs.value || "",
    disabled: false,
    dataset: {},
    style: {},
    _listeners: listeners,
    addEventListener: (event, handler) => listeners.push({ event, handler }),
  };
  return el;
}

function createMockDocument(html) {
  const elements = {};
  html.forEach((spec) => {
    elements[spec.id] = createMockElement(spec.tag, spec);
  });
  return {
    getElementById: (id) => elements[id] || null,
    _elements: elements,
  };
}

function loadPopupWithUrl(activeUrl, storageRef) {
  global.document = createMockDocument([
    { id: "sourceBadge", tag: "div", className: "source-badge source-other", textContent: "Detecting..." },
    { id: "sourceDesc", tag: "p", textContent: "Save the current paper..." },
    { id: "saveBtn", tag: "button", textContent: "Save Current Page" },
    { id: "status", tag: "div", textContent: "" },
    { id: "apiBase", tag: "input", value: "http://localhost:8770" },
    { id: "saveConfig", tag: "button", textContent: "Save Settings" },
    { id: "cnkiPdfToggle", tag: "input", value: "" },
  ]);

  global.chrome = {
    storage: {
      local: {
        get: (keys, cb) => {
          const result = {};
          if (typeof keys === "string") {
            if (storageRef[keys] !== undefined) result[keys] = storageRef[keys];
          } else if (Array.isArray(keys)) {
            keys.forEach((k) => {
              if (storageRef[k] !== undefined) result[k] = storageRef[k];
            });
          }
          if (cb) cb(result);
          return Promise.resolve(result);
        },
        set: (items, cb) => {
          Object.assign(storageRef, items);
          if (cb) cb();
          return Promise.resolve();
        },
      },
    },
    tabs: {
      query: (_queryInfo, cb) => {
        const result = [{ id: 1, url: activeUrl }];
        if (cb) cb(result);
        return Promise.resolve(result);
      },
      sendMessage: () => {
        // Tests override this as needed
      },
    },
    runtime: {
      lastError: null,
    },
  };

  const utils = require("./utils");
  global.isValidClipperUrl = utils.isValidClipperUrl;
  global.detectSource = utils.detectSource;

  delete require.cache[require.resolve("./popup.js")];
  require("./popup.js");
}

function cleanupPopupMocks(originalChrome) {
  global.chrome = originalChrome;
  delete global.document;
  delete global.isValidClipperUrl;
  delete global.detectSource;
  delete require.cache[require.resolve("./popup.js")];
}

describe("popup.js DOM setup", () => {
  let originalChrome;
  let storage = {};

  beforeEach(() => {
    originalChrome = global.chrome;
    storage = {};
    loadPopupWithUrl("https://arxiv.org/abs/2101.00001", storage);
  });

  afterEach(() => {
    cleanupPopupMocks(originalChrome);
  });

  it("detects arXiv source on load", () => {
    const badge = document.getElementById("sourceBadge");
    assert.strictEqual(badge.textContent, "arXiv");
    assert.match(badge.className, /source-arxiv/);
  });

  it("populates apiBase from storage", () => {
    // The storage.get("apiBase") callback should have run; if no stored value,
    // the input keeps its default value.
    const input = document.getElementById("apiBase");
    assert.strictEqual(input.value, "http://localhost:8770");
  });

  it("leaves save button enabled when chrome APIs are available", () => {
    const saveBtn = document.getElementById("saveBtn");
    assert.strictEqual(saveBtn.disabled, false);
  });

  it("sends save_url message to content script when save button clicked", async () => {
    let sentMessage = null;
    let sentTabId = null;
    global.chrome.tabs.sendMessage = (tabId, message, cb) => {
      sentTabId = tabId;
      sentMessage = message;
      cb({ success: true, title: "Test Paper", mode: "pdf" });
    };

    const saveBtn = document.getElementById("saveBtn");
    const statusEl = document.getElementById("status");
    saveBtn._listeners.find((l) => l.event === "click").handler();

    // Wait for async handling
    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.strictEqual(sentTabId, 1);
    assert.strictEqual(sentMessage.action, "save_url");
    assert.strictEqual(sentMessage.url, "https://arxiv.org/abs/2101.00001");
    assert.strictEqual(saveBtn.disabled, false);
    assert.strictEqual(saveBtn.textContent, "Save Current Page");
    assert.match(statusEl.textContent, /Saved: Test Paper/);
    assert.match(statusEl.className, /success/);
  });

  it("falls back to background when content script is unavailable", async () => {
    let backgroundMessage = null;
    global.chrome.tabs.sendMessage = (_tabId, _message, cb) => {
      // Simulate content script unavailable: set lastError before invoking callback
      global.chrome.runtime.lastError = { message: "Could not establish connection" };
      cb();
    };
    global.chrome.runtime.sendMessage = (message, cb) => {
      backgroundMessage = message;
      cb({ success: true, title: "Fallback Paper", mode: "metadata" });
    };

    const saveBtn = document.getElementById("saveBtn");
    const statusEl = document.getElementById("status");
    saveBtn._listeners.find((l) => l.event === "click").handler();

    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.strictEqual(backgroundMessage.action, "save_url");
    assert.strictEqual(backgroundMessage.url, "https://arxiv.org/abs/2101.00001");
    assert.match(statusEl.textContent, /Saved: Fallback Paper \(metadata only\)/);
    assert.match(statusEl.className, /success/);
    assert.strictEqual(saveBtn.disabled, false);

    // Clean up lastError so the next test starts fresh
    global.chrome.runtime.lastError = null;
  });

  it("shows error status when save fails", async () => {
    global.chrome.tabs.sendMessage = (_tabId, _message, cb) => {
      cb({ success: false, error: "Unsupported site" });
    };

    const saveBtn = document.getElementById("saveBtn");
    const statusEl = document.getElementById("status");
    saveBtn._listeners.find((l) => l.event === "click").handler();

    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.strictEqual(statusEl.textContent, "Unsupported site");
    assert.match(statusEl.className, /error/);
    assert.strictEqual(saveBtn.disabled, false);
  });

  it("shows loading state while saving", async () => {
    global.chrome.tabs.sendMessage = (_tabId, _message, cb) => {
      // Delay response to inspect loading state
      setTimeout(() => cb({ success: true, title: "Delayed" }), 5);
    };

    const saveBtn = document.getElementById("saveBtn");
    const statusEl = document.getElementById("status");
    saveBtn._listeners.find((l) => l.event === "click").handler();

    assert.strictEqual(saveBtn.disabled, true);
    assert.strictEqual(saveBtn.textContent, "Saving...");
    assert.strictEqual(statusEl.textContent, "Saving...");

    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.strictEqual(saveBtn.disabled, false);
    assert.match(statusEl.textContent, /Saved: Delayed/);
  });

  it("shows error when active tab has no URL", async () => {
    global.chrome.tabs.query = (_queryInfo, cb) => {
      const result = [{ id: 1 }]; // no url
      if (cb) cb(result);
      return Promise.resolve(result);
    };

    const saveBtn = document.getElementById("saveBtn");
    const statusEl = document.getElementById("status");
    saveBtn._listeners.find((l) => l.event === "click").handler();

    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.strictEqual(statusEl.textContent, "No active tab URL");
    assert.match(statusEl.className, /error/);
    assert.strictEqual(saveBtn.disabled, false);
  });

  it("saves apiBase to storage and shows status when save config clicked", async () => {
    const input = document.getElementById("apiBase");
    input.value = "http://localhost:8770";

    const saveConfigBtn = document.getElementById("saveConfig");
    const statusEl = document.getElementById("status");
    saveConfigBtn._listeners.find((l) => l.event === "click").handler();

    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.strictEqual(storage.apiBase, "http://localhost:8770");
    assert.strictEqual(statusEl.textContent, "Settings saved");
    assert.match(statusEl.className, /success/);
  });

  it("rejects non-local apiBase and shows error", async () => {
    const input = document.getElementById("apiBase");
    input.value = "http://custom:8080";

    const saveConfigBtn = document.getElementById("saveConfig");
    const statusEl = document.getElementById("status");
    saveConfigBtn._listeners.find((l) => l.event === "click").handler();

    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.strictEqual(storage.apiBase, undefined);
    assert.match(statusEl.textContent, /API address must be/);
    assert.match(statusEl.className, /error/);
  });

  it("shows error status when tab query throws", async () => {
    global.chrome.tabs.query = () => {
      return Promise.reject(new Error("Query failed"));
    };

    const saveBtn = document.getElementById("saveBtn");
    const statusEl = document.getElementById("status");
    saveBtn._listeners.find((l) => l.event === "click").handler();

    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.match(statusEl.textContent, /Query failed/);
    assert.match(statusEl.className, /error/);
    assert.strictEqual(saveBtn.disabled, false);
  });
});

describe("popup.js source detection for non-arXiv URLs", () => {
  let originalChrome;
  let storage = {};

  beforeEach(() => {
    originalChrome = global.chrome;
    storage = {};
    loadPopupWithUrl("https://scholar.google.com/scholar?q=paper", storage);
  });

  afterEach(() => {
    cleanupPopupMocks(originalChrome);
  });

  it("detects Google Scholar source on load", () => {
    const badge = document.getElementById("sourceBadge");
    assert.strictEqual(badge.textContent, "Google Scholar");
    assert.match(badge.className, /source-scholar/);
  });
});

describe("popup.js source detection for CNKI URLs", () => {
  let originalChrome;
  let storage = {};

  beforeEach(() => {
    originalChrome = global.chrome;
    storage = {};
    loadPopupWithUrl("https://kns.cnki.net/kcms/detail/detail.aspx?dbcode=CJFD&filename=TEST202401001", storage);
  });

  afterEach(() => {
    cleanupPopupMocks(originalChrome);
  });

  it("detects CNKI source on load", () => {
    const badge = document.getElementById("sourceBadge");
    assert.strictEqual(badge.textContent, "CNKI 知网");
    assert.match(badge.className, /source-cnki/);
  });
});

describe("popup.js when chrome APIs are unavailable", () => {
  let originalChrome;

  beforeEach(() => {
    originalChrome = global.chrome;
    delete global.chrome;
    global.document = createMockDocument([
      { id: "sourceBadge", tag: "div", className: "source-badge source-other", textContent: "Detecting..." },
      { id: "sourceDesc", tag: "p", textContent: "Save the current paper..." },
      { id: "saveBtn", tag: "button", textContent: "Save Current Page" },
      { id: "status", tag: "div", textContent: "" },
      { id: "apiBase", tag: "input", value: "http://localhost:8770" },
      { id: "saveConfig", tag: "button", textContent: "Save Settings" },
      { id: "cnkiPdfToggle", tag: "input", value: "" },
    ]);

    const utils = require("./utils");
    global.isValidClipperUrl = utils.isValidClipperUrl;
    global.detectSource = utils.detectSource;

    delete require.cache[require.resolve("./popup.js")];
    require("./popup.js");
  });

  afterEach(() => {
    global.chrome = originalChrome;
    delete global.document;
    delete global.isValidClipperUrl;
    delete global.detectSource;
    delete require.cache[require.resolve("./popup.js")];
  });

  it("disables UI and shows warning when chrome APIs are unavailable", () => {
    const saveBtn = document.getElementById("saveBtn");
    const saveConfigBtn = document.getElementById("saveConfig");
    const statusEl = document.getElementById("status");
    assert.strictEqual(saveBtn.disabled, true);
    assert.strictEqual(saveConfigBtn.disabled, true);
    assert.strictEqual(statusEl.textContent, "Extension APIs unavailable");
  });
});

// ===========================================================================
// WP-4.1b: CNKI PDF 导入开关测试
// ===========================================================================
describe("popup.js CNKI PDF import toggle", () => {
  let originalChrome;
  let storage = {};

  beforeEach(() => {
    originalChrome = global.chrome;
    storage = {};
    loadPopupWithUrl("https://kns.cnki.net/kcms/detail/detail.aspx", storage);
  });

  afterEach(() => {
    cleanupPopupMocks(originalChrome);
  });

  it("loads CNKI toggle as unchecked by default (no stored value)", () => {
    const toggle = document.getElementById("cnkiPdfToggle");
    assert.ok(toggle, "toggle element should exist");
    assert.strictEqual(toggle.checked, false, "toggle should default to off");
  });

  it("loads CNKI toggle as checked when stored value is true", async () => {
    // Re-load with stored value
    cleanupPopupMocks(originalChrome);
    originalChrome = global.chrome;
    storage = { cnkiPdfImportEnabled: true };
    loadPopupWithUrl("https://kns.cnki.net/kcms/detail/detail.aspx", storage);

    const toggle = document.getElementById("cnkiPdfToggle");
    assert.strictEqual(toggle.checked, true, "toggle should reflect stored true value");
  });

  it("persists toggle to storage when changed to on", async () => {
    const toggle = document.getElementById("cnkiPdfToggle");
    const statusEl = document.getElementById("status");
    toggle.checked = true;
    // Trigger change listener
    toggle._listeners.find((l) => l.event === "change").handler();

    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(storage.cnkiPdfImportEnabled, true, "should persist true to storage");
    assert.match(statusEl.textContent, /知网 PDF 导入已开启/);
    assert.match(statusEl.className, /info/);
  });

  it("persists toggle to storage when changed to off", async () => {
    const toggle = document.getElementById("cnkiPdfToggle");
    const statusEl = document.getElementById("status");
    toggle.checked = false;
    toggle._listeners.find((l) => l.event === "change").handler();

    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(storage.cnkiPdfImportEnabled, false, "should persist false to storage");
    assert.match(statusEl.textContent, /知网 PDF 导入已关闭/);
  });
});

describe("popup.js source detection for unsupported URLs", () => {
  let originalChrome;
  let storage = {};

  beforeEach(() => {
    originalChrome = global.chrome;
    storage = {};
    loadPopupWithUrl("https://example.com/paper", storage);
  });

  afterEach(() => {
    cleanupPopupMocks(originalChrome);
  });

  it("falls back to Other source on load", () => {
    const badge = document.getElementById("sourceBadge");
    assert.strictEqual(badge.textContent, "Other");
    assert.match(badge.className, /source-other/);
  });
});
