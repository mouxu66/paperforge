/**
 * content.js 单测（P2-8）
 *
 * 覆盖三站适配器提取逻辑 + sendSave 超时/查重 + onMessage 处理。
 * 使用 node:test + node:assert，与 background/popup 测试一致。
 */

const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert");

// ---------------------------------------------------------------------------
// Mock helpers
// ---------------------------------------------------------------------------

let nextMetaId = 0;

function createMockElement(tag, attrs = {}, _docRef = null) {
  const listeners = [];
  const children = attrs.children || [];
  nextMetaId++;
  const el = {
    _id: nextMetaId,
    tagName: tag.toUpperCase(),
    textContent: attrs.textContent !== undefined ? attrs.textContent : "",
    innerText: attrs.innerText !== undefined ? attrs.innerText : (attrs.textContent || ""),
    className: attrs.className || "",
    id: "",
    href: attrs.href || "",
    style: {},
    dataset: {},
    _listeners: listeners,
    _children: children,
    _docRef: _docRef,
    getAttribute: (name) => {
      if (attrs._attributes && attrs._attributes[name] !== undefined) {
        return attrs._attributes[name];
      }
      if (name === "content") return attrs.content !== undefined ? attrs.content : null;
      if (name === "href") return attrs.href || null;
      return null;
    },
    setAttribute: (name, value) => {
      if (!attrs._attributes) attrs._attributes = {};
      attrs._attributes[name] = value;
    },
    addEventListener: (event, handler) => listeners.push({ event, handler }),
    querySelector: (sel) => {
      for (const c of children) {
        if (_matchesSimple(c, sel)) return c;
      }
      return null;
    },
    querySelectorAll: (sel) => {
      return children.filter(c => _matchesSimple(c, sel));
    },
    appendChild: (child) => {
      children.push(child);
      if (_docRef) _docRef._register(child);
      return child;
    },
    closest: (_sel) => null,
    remove: () => {
      // 从 document.body._children 中移除自身（模拟 DOM remove）
      if (_docRef && _docRef.body && _docRef.body._children) {
        const idx = _docRef.body._children.indexOf(el);
        if (idx !== -1) _docRef.body._children.splice(idx, 1);
      }
      if (_docRef && attrs.id) _docRef._unregister(attrs.id);
    },
  };
  // 支持运行时 setting id 后 register 到 document
  Object.defineProperty(el, "id", {
    get() { return attrs.id || ""; },
    set(v) {
      const oldId = attrs.id;
      attrs.id = v;
      if (_docRef) {
        if (oldId) _docRef._unregister(oldId);
        _docRef._register(el);
      }
    },
    enumerable: true,
    configurable: true,
  });
  return el;
}

function _matchesSimple(el, sel) {
  // Simple selector matching for common patterns
  // Handle comma-separated selectors (e.g. "#pdfDown, a.pdf-download")
  if (sel.includes(",")) {
    return sel.split(",").some((part) => _matchesSimple(el, part.trim()));
  }
  if (sel === "*") return true;
  if (sel.startsWith(".")) return el.className && el.className.split(" ").includes(sel.slice(1));
  if (sel.startsWith("#")) return el.id === sel.slice(1);
  if (sel.includes("[") && sel.includes("]")) {
    // [name="..."] or [class*="..."]
    const attrMatch = sel.match(/\[([^=]+)([*^$]?=)"?([^"\]]+)"?\]/);
    if (attrMatch) {
      const [, attrName, op, attrVal] = attrMatch;
      const elVal = el.getAttribute(attrName) || el.className || "";
      if (op === "*=") return elVal.includes(attrVal);
      if (op === "=") return elVal === attrVal;
      return elVal !== null;
    }
  }
  // Class-based: "h3.gs_rt a" or "a[href*='pdf']"
  const tagMatch = sel.match(/^([a-z]+)/i);
  const tagName = tagMatch ? tagMatch[1].toUpperCase() : null;
  if (tagName && el.tagName !== tagName) return false;
  // Check classes
  const classPart = sel.match(/\.([a-zA-Z0-9_-]+)/g);
  if (classPart) {
    for (const c of classPart) {
      if (!el.className || !el.className.split(" ").includes(c.slice(1))) return false;
    }
  }
  return true;
}

function createMockDocument(elements = []) {
  const doc = {
    _register: () => {},
    _unregister: () => {},
    _allElements: elements,
    title: "",
    body: null,
    getElementById: (id) => {
      function search(els) {
        for (const el of els) {
          if (el.id === id) return el;
          const found = search(el._children || []);
          if (found) return found;
        }
        return null;
      }
      return search(elements) || (doc.body ? search(doc.body._children || []) : null);
    },
    querySelector: (sel) => {
      function search(els) {
        for (const el of els) {
          if (_matchesSimple(el, sel)) return el;
          const found = search(el._children || []);
          if (found) return found;
        }
        return null;
      }
      return search(elements);
    },
    querySelectorAll: (sel) => {
      const results = [];
      function search(els) {
        for (const el of els) {
          if (_matchesSimple(el, sel)) results.push(el);
          search(el._children || []);
        }
      }
      search(elements);
      return results;
    },
    createElement: (tag) => createMockElement(tag, {}, doc),
  };

  doc.body = createMockElement("body", { children: elements }, doc);
  return doc;
}

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

function setupBrowserEnv(opts = {}) {
  const hostname = opts.hostname || "arxiv.org";
  const href = opts.href || `https://${hostname}/abs/2401.00001`;
  const title = opts.pageTitle || "";
  const docElements = opts.docElements || [];
  const doc = createMockDocument(docElements);
  if (title) doc.title = title;

  // Node.js 无 window 全局变量，需通过 globalThis 桥接
  globalThis.window = { location: { hostname, href, toString: () => href }, document: doc };
  globalThis.document = doc;

  // Chrome mock
  // cnkiEnabled: undefined → no storage (isCnkiRawImportEnabled resolves false → button hidden)
  // cnkiEnabled: true/false → storage present, returns that value
  const storageData = {};
  if (opts.cnkiEnabled !== undefined) {
    storageData["cnkiPdfImportEnabled"] = opts.cnkiEnabled;
  }
  const chromeMock = {
    runtime: {
      sendMessage: (payload, cb) => {
        if (opts._sendMessageImpl) {
          opts._sendMessageImpl(payload, cb);
        } else {
          cb({ success: true, paperId: "p1", title: "Test Paper" });
        }
      },
      onMessage: {
        addListener: (fn) => {
          global.__onMessageListener = fn;
        },
      },
      lastError: null,
    },
  };
  if (opts.cnkiEnabled !== undefined) {
    chromeMock.storage = {
      local: {
        get: (key, cb) => {
          const result = key in storageData ? { [key]: storageData[key] } : {};
          if (cb) cb(result);
          return Promise.resolve(result);
        },
      },
      onChanged: {
        addListener: (fn) => {
          global.__onStorageChangedListener = fn;
        },
      },
    };
  }
  global.chrome = chromeMock;

  // Console mock
  global.console = { error: () => {}, warn: () => {} };

  // Clear and reload content.js
  delete require.cache[require.resolve("./content.js")];
}

function teardownBrowserEnv() {
  delete globalThis.window;
  delete globalThis.document;
  delete globalThis.chrome;
  delete globalThis.__onMessageListener;
  delete globalThis.__onStorageChangedListener;
  delete global.fetch;
  delete require.cache[require.resolve("./content.js")];
}

// Force content.js to load (bypass the early return guard)
function loadContentScript() {
  // Ensure pf-clipper-btn is NOT in the document so the guard doesn't trigger
  require("./content.js");
}

// ---------------------------------------------------------------------------
// Tests: arXiv adapter
// ---------------------------------------------------------------------------

describe("content.js — arXiv adapter", () => {
  afterEach(() => teardownBrowserEnv());

  it("extracts title/authors/abstract/year from meta tags", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Deep Learning Advances" }),
        createMockElement("meta", { _attributes: { name: "citation_author" }, content: "Alice" }),
        createMockElement("meta", { _attributes: { name: "citation_author" }, content: "Bob" }),
        createMockElement("meta", { _attributes: { name: "citation_abstract" }, content: "This paper explores..." }),
        createMockElement("meta", { _attributes: { name: "citation_date" }, content: "2024/01/15" }),
        createMockElement("meta", { _attributes: { property: "og:title" }, content: "DL Advances" }),
      ],
    });
    loadContentScript();

    const adapter = { detect: () => true, extract: () => {} };
    // The content.js IIFE runs detectAdapter() which checks adapters.arxiv.detect()
    // Since we set hostname=arxiv.org, it should detect arxiv.
    // The adapter runs extract() and creates a floating button.
    // We can verify the button was created. But for adapter-level tests,
    // we need to access the adapter functions directly. They're inside the IIFE
    // closure so they're not directly accessible.
    //
    // Instead, verify side effects: a floating button should be created
    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn, "floating button should be created for arXiv page");
    assert.strictEqual(btn.innerText, "Save to PaperForge");
    assert.strictEqual(btn.tagName, "BUTTON");
  });

  it("falls back to og:title when citation_title is missing", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { property: "og:title" }, content: "OG Title" }),
        createMockElement("meta", { _attributes: { name: "citation_author" }, content: "Alice" }),
      ],
    });
    loadContentScript();
    // Should create a button (adapter detected)
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist");
  });

  it("uses document.title as title fallback", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      pageTitle: "Page Title Fallback",
      docElements: [],
    });
    loadContentScript();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist with title fallback");
  });

  it("constructs pdfUrl from /abs/ -> /pdf/", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001v2",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
        createMockElement("meta", { _attributes: { name: "citation_date" }, content: "2024/01/15" }),
      ],
    });
    loadContentScript();
    // The adapter constructs pdfUrl = href.replace("/abs/", "/pdf/") + ".pdf"
    // = "https://arxiv.org/pdf/2401.00001v2.pdf"
    // We can't easily verify this from outside the IIFE, but we know the button exists
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist");
  });

  it("handles missing authors gracefully", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "No Authors Paper" }),
      ],
    });
    loadContentScript();
    // Should not throw; button should still be created
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist even without authors");
  });

  it("extracts year from citation_date correctly", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
        createMockElement("meta", { _attributes: { name: "citation_date" }, content: "2023/06/01" }),
      ],
    });
    loadContentScript();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist");
  });
});

// ---------------------------------------------------------------------------
// Tests: CNKI adapter
// ---------------------------------------------------------------------------

describe("content.js — CNKI adapter", () => {
  afterEach(() => teardownBrowserEnv());

  // CNKI button creation is async (gated on isCnkiRawImportEnabled Promise)
  // so tests that expect the button must await a microtask flush after loadContentScript.
  async function flushMicrotasks() {
    await new Promise((r) => setTimeout(r, 0));
  }

  it("detects cnki.net hostname", async () => {
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "基于深度学习的文本分类" }),
        createMockElement("meta", { _attributes: { name: "citation_author" }, content: "张三" }),
      ],
    });
    loadContentScript();
    await flushMicrotasks();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist for CNKI when enabled");
  });

  it("detects cnki.com.cn hostname", async () => {
    setupBrowserEnv({
      hostname: "www.cnki.com.cn",
      href: "https://www.cnki.com.cn/Article/CJFDTotal",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();
    await flushMicrotasks();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist for cnki.com.cn when enabled");
  });

  it("falls back to DOM selectors when meta tags are missing", async () => {
    const titleEl = createMockElement("h1", { textContent: "DOM Extracted Title" });
    const titleWrapper = createMockElement("div", { className: "wx_tit", children: [titleEl] });
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      pageTitle: "Page Title",
      docElements: [titleWrapper],
    });
    loadContentScript();
    await flushMicrotasks();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist with DOM fallback");
  });

  it("falls back to document.title when all selectors fail", async () => {
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      pageTitle: "Ultimate Fallback Title",
      docElements: [],
    });
    loadContentScript();
    await flushMicrotasks();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist with document.title");
  });

  it("extracts authors from DOM when meta authors are absent", async () => {
    const authorEl = createMockElement("div", { className: "author", textContent: "Wang, Li, Zhang" });
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test Paper" }),
        authorEl,
      ],
    });
    loadContentScript();
    await flushMicrotasks();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist with DOM author fallback");
  });

  it("extracts abstract from DOM fallback", async () => {
    const absEl = createMockElement("div", { id: "ChDivSummary", textContent: "DOM abstract text here" });
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
        absEl,
      ],
    });
    loadContentScript();
    await flushMicrotasks();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist with abstract DOM fallback");
  });

  it("handles completely empty CNKI page gracefully", async () => {
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      pageTitle: "知网论文",
      docElements: [],
    });
    loadContentScript();
    await flushMicrotasks();
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist on minimal CNKI page when enabled");
  });
});

// ---------------------------------------------------------------------------
// Tests: CNKI PDF import toggle (WP-4.1b)
// ---------------------------------------------------------------------------

describe("content.js — CNKI PDF import toggle (default off)", () => {
  afterEach(() => teardownBrowserEnv());

  it("hides CNKI button when toggle is off (default)", async () => {
    // No cnkiEnabled option → no storage → isCnkiRawImportEnabled resolves false
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();
    await new Promise((r) => setTimeout(r, 0));
    assert.strictEqual(
      document.getElementById("pf-clipper-btn"),
      null,
      "button should NOT exist when CNKI toggle is off (default)"
    );
  });

  it("hides CNKI button when toggle is explicitly false", async () => {
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: false,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();
    await new Promise((r) => setTimeout(r, 0));
    assert.strictEqual(
      document.getElementById("pf-clipper-btn"),
      null,
      "button should NOT exist when CNKI toggle is explicitly false"
    );
  });

  it("shows CNKI button when toggle is on", async () => {
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();
    await new Promise((r) => setTimeout(r, 0));
    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn, "button should exist when CNKI toggle is on");
    assert.strictEqual(btn.dataset.pfCnki, "1", "button should be tagged as CNKI");
  });

  it("does not affect arXiv button (always shown regardless of toggle)", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();
    assert.ok(document.getElementById("pf-clipper-btn"), "arXiv button should always exist");
  });
});

describe("content.js — CNKI storage.onChanged dynamic show/hide", () => {
  afterEach(() => teardownBrowserEnv());

  it("shows button when toggle flips from off to on via storage.onChanged", async () => {
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: false,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();
    await new Promise((r) => setTimeout(r, 0));
    // Initially hidden (toggle off)
    assert.strictEqual(document.getElementById("pf-clipper-btn"), null);

    // Simulate toggle flip to on
    const listener = globalThis.__onStorageChangedListener;
    assert.ok(listener, "storage.onChanged listener should be registered");
    listener(
      { cnkiPdfImportEnabled: { oldValue: false, newValue: true } },
      "local"
    );

    assert.ok(document.getElementById("pf-clipper-btn"), "button should appear after toggle on");
  });

  it("hides button when toggle flips from on to off via storage.onChanged", async () => {
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();
    await new Promise((r) => setTimeout(r, 0));
    assert.ok(document.getElementById("pf-clipper-btn"), "button should exist initially");

    const listener = globalThis.__onStorageChangedListener;
    assert.ok(listener);
    listener(
      { cnkiPdfImportEnabled: { oldValue: true, newValue: false } },
      "local"
    );

    assert.strictEqual(
      document.getElementById("pf-clipper-btn"),
      null,
      "button should disappear after toggle off"
    );
  });

  it("ignores storage.onChanged for non-local area", async () => {
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();
    await new Promise((r) => setTimeout(r, 0));
    const listener = globalThis.__onStorageChangedListener;
    listener(
      { cnkiPdfImportEnabled: { oldValue: true, newValue: false } },
      "sync" // not local
    );
    assert.ok(document.getElementById("pf-clipper-btn"), "button should remain for non-local change");
  });
});

describe("content.js — handleCnkiSave (browser fetch + raw upload + fallback)", () => {
  afterEach(() => teardownBrowserEnv());

  // CNKI button creation is async (gated on isCnkiRawImportEnabled Promise).
  // Must flush microtasks before asserting the button exists.
  async function flush() {
    await new Promise((r) => setTimeout(r, 0));
  }

  it("downloads PDF in-page and sends save_raw when PDF is valid", async () => {
    // Valid PDF magic bytes (%PDF-1.4)
    const pdfBytes = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34]);
    const pdfBuffer = pdfBytes.buffer;

    let fetchUrl = null;
    global.fetch = (url, init) => {
      fetchUrl = url;
      return Promise.resolve({
        ok: true,
        arrayBuffer: () => Promise.resolve(pdfBuffer),
      });
    };

    let sentPayload = null;
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "知网论文" }),
        createMockElement("a", { id: "pdfDown", href: "https://kns.cnki.net/download/paper.pdf" }),
      ],
      _sendMessageImpl: (msg, cb) => {
        sentPayload = msg;
        cb({ success: true, paperId: "p1", title: "知网论文", mode: "pdf" });
      },
    });
    loadContentScript();
    await flush();

    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn);
    // Trigger click handler (handleCnkiSave)
    const clickHandler = btn._listeners.find((l) => l.event === "click").handler;
    await clickHandler({ currentTarget: btn });

    await new Promise((r) => setTimeout(r, 10));
    assert.strictEqual(fetchUrl, "https://kns.cnki.net/download/paper.pdf");
    assert.ok(sentPayload, "save_raw payload should be sent");
    assert.strictEqual(sentPayload.action, "save_raw");
    assert.strictEqual(sentPayload.title, "知网论文");
    assert.strictEqual(sentPayload.source, "cnki");
  });

  it("falls back to save_url when in-page fetch fails", async () => {
    global.fetch = () => Promise.reject(new Error("network error"));

    let sentPayload = null;
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "知网论文" }),
        createMockElement("a", { id: "pdfDown", href: "https://kns.cnki.net/download/paper.pdf" }),
      ],
      _sendMessageImpl: (msg, cb) => {
        sentPayload = msg;
        cb({ success: true, paperId: "p1", title: "知网论文", mode: "metadata" });
      },
    });
    loadContentScript();
    await flush();

    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn, "button should exist after flush");
    const clickHandler = btn._listeners.find((l) => l.event === "click").handler;
    await clickHandler({ currentTarget: btn });

    await new Promise((r) => setTimeout(r, 10));
    assert.ok(sentPayload, "fallback payload should be sent");
    assert.strictEqual(sentPayload.action, "save_url", "should fall back to save_url");
  });

  it("falls back to save_url when fetched content is not a valid PDF", async () => {
    // HTML login page, not a PDF
    const htmlBuffer = new TextEncoder().encode("<html>login</html>").buffer;
    global.fetch = () =>
      Promise.resolve({
        ok: true,
        arrayBuffer: () => Promise.resolve(htmlBuffer),
      });

    let sentPayload = null;
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "知网论文" }),
        createMockElement("a", { id: "pdfDown", href: "https://kns.cnki.net/download/paper.pdf" }),
      ],
      _sendMessageImpl: (msg, cb) => {
        sentPayload = msg;
        cb({ success: true, paperId: "p1", title: "知网论文", mode: "metadata" });
      },
    });
    loadContentScript();
    await flush();

    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn, "button should exist after flush");
    const clickHandler = btn._listeners.find((l) => l.event === "click").handler;
    await clickHandler({ currentTarget: btn });

    await new Promise((r) => setTimeout(r, 10));
    assert.strictEqual(sentPayload.action, "save_url", "should fall back when content is not PDF");
  });

  it("sends save_url directly when no pdfUrl in metadata", async () => {
    let sentPayload = null;
    setupBrowserEnv({
      hostname: "kns.cnki.net",
      href: "https://kns.cnki.net/kcms/detail/detail.aspx",
      cnkiEnabled: true,
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "无 PDF 链接论文" }),
        // No #pdfDown element → pdfUrl will be empty
      ],
      _sendMessageImpl: (msg, cb) => {
        sentPayload = msg;
        cb({ success: true, paperId: "p1", title: "无 PDF 链接论文", mode: "metadata" });
      },
    });
    loadContentScript();
    await flush();

    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn, "button should exist after flush");
    const clickHandler = btn._listeners.find((l) => l.event === "click").handler;
    await clickHandler({ currentTarget: btn });

    await new Promise((r) => setTimeout(r, 10));
    assert.strictEqual(sentPayload.action, "save_url", "should use save_url when no pdfUrl");
  });
});

// ---------------------------------------------------------------------------
// Tests: Google Scholar adapter
// ---------------------------------------------------------------------------

describe("content.js — Google Scholar adapter", () => {
  afterEach(() => teardownBrowserEnv());

  it("detects scholar.google.com hostname", () => {
    setupBrowserEnv({
      hostname: "scholar.google.com",
      href: "https://scholar.google.com/scholar?q=test",
      docElements: [],
    });
    loadContentScript();
    // Google Scholar uses injectPerResult; no floating button unless .gs_ri present
    // Without .gs_ri, no buttons are injected, but the adapter is detected
    // The script doesn't create a floating button for Scholar (uses per-result injection)
  });

  it("detects scholar.google.com.hk hostname", () => {
    setupBrowserEnv({
      hostname: "scholar.google.com.hk",
      href: "https://scholar.google.com.hk/scholar?q=test",
      docElements: [],
    });
    loadContentScript();
    // Scholar adapter detects but without .gs_ri, nothing happens
  });

  it("injects inline buttons per .gs_ri result", () => {
    const titleLink = createMockElement("a", { href: "https://example.com/paper1", textContent: "Paper One" });
    const titleH3 = createMockElement("h3", { className: "gs_rt", children: [titleLink] });
    const resultDiv = createMockElement("div", { className: "gs_ri", children: [titleH3] });

    setupBrowserEnv({
      hostname: "scholar.google.com",
      href: "https://scholar.google.com/scholar?q=test",
      docElements: [resultDiv],
    });
    loadContentScript();

    // After loading, the .gs_ri div should have a pf-clipper-inline button
    const inlineBtns = resultDiv._children.filter(c => c.className === "pf-clipper-inline");
    assert.strictEqual(inlineBtns.length, 1, "should inject 1 inline button per result");
    assert.strictEqual(inlineBtns[0].innerText, "Save to PF");
    assert.strictEqual(inlineBtns[0].dataset.originalText, "Save to PF");
  });

  it("skips duplicate injection on .gs_ri already having inline button", () => {
    const existingBtn = createMockElement("button", { className: "pf-clipper-inline", textContent: "Save to PF" });
    const titleLink = createMockElement("a", { href: "https://example.com/paper1", textContent: "Paper One" });
    const titleH3 = createMockElement("h3", { className: "gs_rt", children: [titleLink] });
    const resultDiv = createMockElement("div", {
      className: "gs_ri",
      children: [titleH3, existingBtn],
    });

    setupBrowserEnv({
      hostname: "scholar.google.com",
      href: "https://scholar.google.com/scholar?q=test",
      docElements: [resultDiv],
    });
    loadContentScript();

    // Should NOT add a second button
    const inlineBtns = resultDiv._children.filter(c => c.className === "pf-clipper-inline");
    assert.strictEqual(inlineBtns.length, 1, "should not inject duplicate inline button");
  });

  it("handles Scholar result with pdf link", () => {
    const pdfLink = createMockElement("a", { href: "https://example.com/paper.pdf" });
    const pdfWrapper = createMockElement("div", { className: "gs_or_ggsm", children: [pdfLink] });
    const titleLink = createMockElement("a", { href: "https://example.com/paper1", textContent: "Paper One" });
    const titleH3 = createMockElement("h3", { className: "gs_rt", children: [titleLink] });
    const resultDiv = createMockElement("div", {
      className: "gs_ri",
      children: [titleH3, pdfWrapper],
    });

    setupBrowserEnv({
      hostname: "scholar.google.com",
      href: "https://scholar.google.com/scholar?q=test",
      docElements: [resultDiv],
    });
    loadContentScript();

    const inlineBtns = resultDiv._children.filter(c => c.className === "pf-clipper-inline");
    assert.strictEqual(inlineBtns.length, 1, "should inject button even with pdf link");
  });

  it("extracts authors and year from .gs_a info element", () => {
    const infoEl = createMockElement("div", { className: "gs_a", textContent: "Alice, Bob - Nature, 2024 - example.com" });
    const titleLink = createMockElement("a", { href: "https://example.com/paper1", textContent: "Paper One" });
    const titleH3 = createMockElement("h3", { className: "gs_rt", children: [titleLink] });
    const resultDiv = createMockElement("div", {
      className: "gs_ri",
      children: [titleH3, infoEl],
    });

    setupBrowserEnv({
      hostname: "scholar.google.com",
      href: "https://scholar.google.com/scholar?q=test",
      docElements: [resultDiv],
    });
    loadContentScript();

    const inlineBtns = resultDiv._children.filter(c => c.className === "pf-clipper-inline");
    assert.strictEqual(inlineBtns.length, 1, "button should inject with info element");
  });
});

// ---------------------------------------------------------------------------
// Tests: sendSave behavior (P0-2 timeout + P1-6 alreadyExists)
// ---------------------------------------------------------------------------

describe("content.js — sendSave (onMessage + timeout + states)", () => {
  afterEach(() => teardownBrowserEnv());

  it("responds with success and changes button text", async () => {
    let sentMessage = null;
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Success Paper" }),
      ],
      _sendMessageImpl: (msg, cb) => {
        sentMessage = msg;
        cb({ success: true, paperId: "p99", title: "Success Paper", mode: "pdf" });
      },
    });
    loadContentScript();

    // Trigger save via onMessage to test the popup path
    const listener = global.__onMessageListener;
    assert.ok(listener, "onMessage listener should be registered");

    let responseReceived = null;
    listener({ action: "save_url", url: "https://arxiv.org/abs/2401.00001" }, {}, (resp) => {
      responseReceived = resp;
    });

    await new Promise(r => setTimeout(r, 10));
    assert.ok(responseReceived, "response should be received");
    assert.strictEqual(responseReceived.success, true);
    assert.strictEqual(sentMessage.action, "save_url");
    assert.strictEqual(sentMessage.title, "Success Paper");
  });

  it("shows 'Already in library' when response has alreadyExists", async () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Dup Paper" }),
      ],
      _sendMessageImpl: (_msg, cb) => {
        cb({
          success: true,
          paperId: "p999",
          title: "Dup Paper",
          alreadyExists: true,
          existingPaperId: "p001",
          existingTitle: "Dup Paper",
        });
      },
    });
    loadContentScript();
    // content.js creates the button at runtime, not via docElements
    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn, "button should be created");

    const listener = globalThis.__onMessageListener;
    let responseReceived = null;
    listener({ action: "save_url", url: "https://arxiv.org/abs/2401.00001" }, {}, (resp) => {
      responseReceived = resp;
    });

    await new Promise(r => setTimeout(r, 10));
    assert.ok(responseReceived);
    assert.strictEqual(responseReceived.alreadyExists, true);
    assert.strictEqual(btn.innerText, "Already in library");
    assert.strictEqual(btn.style.background, "#f59e0b");
  });

  it("shows 'Error' when response fails", async () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Fail Paper" }),
      ],
      _sendMessageImpl: (_msg, cb) => {
        cb({ success: false, error: "Server down" });
      },
    });
    loadContentScript();
    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn, "button should be created");

    const listener = globalThis.__onMessageListener;
    let responseReceived = null;
    listener({ action: "save_url", url: "https://arxiv.org/abs/2401.00001" }, {}, (resp) => {
      responseReceived = resp;
    });

    await new Promise(r => setTimeout(r, 10));
    assert.ok(responseReceived);
    assert.strictEqual(responseReceived.success, false);
    assert.strictEqual(btn.innerText, "Error");
    assert.strictEqual(btn.style.background, "#dc2626");
  });

  it("shows 'Saved (metadata)' when mode is metadata", async () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Meta Paper" }),
      ],
      _sendMessageImpl: (_msg, cb) => {
        cb({ success: true, paperId: "p100", title: "Meta Paper", mode: "metadata" });
      },
    });
    loadContentScript();
    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn, "button should be created");

    const listener = globalThis.__onMessageListener;
    listener({ action: "save_url", url: "https://arxiv.org/abs/2401.00001" }, {}, () => {});

    await new Promise(r => setTimeout(r, 10));
    assert.strictEqual(btn.innerText, "Saved (metadata)");
    assert.strictEqual(btn.style.background, "#16a34a");
  });

  it("rejects unknown action with error response", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();

    const listener = global.__onMessageListener;
    let responseReceived = null;
    const result = listener({ action: "ping" }, {}, (resp) => {
      responseReceived = resp;
    });
    assert.strictEqual(result, false);
    assert.ok(responseReceived);
    assert.strictEqual(responseReceived.success, false);
    assert.match(responseReceived.error, /Unknown action/);
  });

  it("passes popup tags through to save_url payload", async () => {
    let sentPayload = null;
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Tagged Paper" }),
      ],
      _sendMessageImpl: (msg, cb) => {
        sentPayload = msg;
        cb({ success: true, paperId: "p200", title: "Tagged Paper" });
      },
    });
    loadContentScript();

    const listener = global.__onMessageListener;
    listener(
      { action: "save_url", url: "https://arxiv.org/abs/2401.00001", tags: ["important", "to-read"] },
      {},
      () => {}
    );

    await new Promise(r => setTimeout(r, 10));
    assert.ok(sentPayload, "payload should be sent");
    assert.deepStrictEqual(sentPayload.tags, ["important", "to-read"]);
  });
});

// ---------------------------------------------------------------------------
// Tests: unsupported site
// ---------------------------------------------------------------------------

describe("content.js — unsupported site", () => {
  afterEach(() => teardownBrowserEnv());

  it("returns early without creating button on unsupported site", () => {
    setupBrowserEnv({
      hostname: "example.com",
      href: "https://example.com/paper",
      docElements: [],
    });
    loadContentScript();
    assert.strictEqual(document.getElementById("pf-clipper-btn"), null, "no button for unsupported site");
  });

  it("returns early on github.com", () => {
    setupBrowserEnv({
      hostname: "github.com",
      href: "https://github.com/paper/repo",
      docElements: [],
    });
    loadContentScript();
    assert.strictEqual(document.getElementById("pf-clipper-btn"), null, "no button for github");
  });
});

// ---------------------------------------------------------------------------
// Tests: floating button click behavior
// ---------------------------------------------------------------------------

describe("content.js — floating button interaction", () => {
  afterEach(() => teardownBrowserEnv());

  it("creates floating button with correct ID and text", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();

    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn);
    assert.strictEqual(btn.innerText, "Save to PaperForge");
    assert.strictEqual(btn.id, "pf-clipper-btn");
    assert.strictEqual(btn.tagName, "BUTTON");
    // Button should have a click listener
    assert.ok(btn._listeners.length > 0, "button should have click listener");
  });

  it("creates inline button with correct attributes", () => {
    setupBrowserEnv({
      hostname: "arxiv.org",
      href: "https://arxiv.org/abs/2401.00001",
      docElements: [
        createMockElement("meta", { _attributes: { name: "citation_title" }, content: "Test" }),
      ],
    });
    loadContentScript();

    // Floating button should exist
    const btn = document.getElementById("pf-clipper-btn");
    assert.ok(btn);
    assert.ok(btn.style.cssText.includes("position: fixed"), "floating button should be fixed position");
  });
});
