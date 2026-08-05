const { describe, it, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert");
const path = require("path");

// Minimal in-memory fetch stub (avoid shadowing Node's global Response)
class MockResponse {
  constructor(body, init = {}) {
    this.body = body;
    this.status = init.status ?? 200;
    this.ok = init.ok ?? (this.status >= 200 && this.status < 300);
    this._json = typeof body === "string" ? JSON.parse(body) : body;
  }
  json() {
    return Promise.resolve(this._json);
  }
}

describe("background.js message handling", () => {
  let originalChrome;
  let originalFetch;
  let originalImportScripts;
  let listeners;
  let fetchCalls;
  let storageApiBase;

  beforeEach(() => {
    originalChrome = global.chrome;
    originalFetch = global.fetch;
    originalImportScripts = global.importScripts;

    listeners = [];
    fetchCalls = [];
    storageApiBase = "http://localhost:8770";

    global.chrome = {
      storage: {
        local: {
          get: (key, cb) => {
            if (cb) cb({ apiBase: storageApiBase });
            return Promise.resolve({ apiBase: storageApiBase });
          },
        },
      },
      runtime: {
        onMessage: {
          addListener: (listener) => listeners.push(listener),
        },
      },
    };

    global.fetch = (url, init) => {
      fetchCalls.push({ url, init });
      return Promise.resolve(new MockResponse(JSON.stringify({ paperId: "p1", title: "T" }), { ok: true }));
    };

    global.importScripts = () => {
      // utils.js is loaded as a classic script; expose its functions globally
      const utilsPath = path.join(__dirname, "utils.js");
      const utils = require(utilsPath);
      global.isValidClipperUrl = utils.isValidClipperUrl;
      global.detectSource = utils.detectSource;
    };

    // Load background.js fresh for each test
    delete require.cache[require.resolve("./background.js")];
    require("./background.js");
  });

  afterEach(() => {
    global.chrome = originalChrome;
    global.fetch = originalFetch;
    global.importScripts = originalImportScripts;
    delete require.cache[require.resolve("./background.js")];
  });

  it("ignores non-save_url actions", () => {
    const sendResponse = () => {};
    const result = listeners[0]({ action: "ping" }, {}, sendResponse);
    assert.strictEqual(result, false);
  });

  it("rejects missing URL", () => {
    const sendResponse = (response) => {
      assert.strictEqual(response.success, false);
      assert.match(response.error, /No URL provided/);
    };
    const result = listeners[0]({ action: "save_url" }, {}, sendResponse);
    assert.strictEqual(result, true);
  });

  it("rejects unsupported URLs", () => {
    const sendResponse = (response) => {
      assert.strictEqual(response.success, false);
      assert.match(response.error, /Only arXiv/);
    };
    const result = listeners[0]({ action: "save_url", url: "https://example.com" }, {}, sendResponse);
    assert.strictEqual(result, true);
  });

  it("sends ingest request for arXiv URL (with duplicate check)", async () => {
    let resolved = false;
    const sendResponse = (response) => {
      resolved = true;
      assert.strictEqual(response.success, true);
      assert.strictEqual(response.paperId, "p1");
    };
    const result = listeners[0](
      { action: "save_url", url: "https://arxiv.org/abs/2101.00001", title: "Test" },
      {},
      sendResponse,
    );
    assert.strictEqual(result, true);

    // Wait for the async IIFE inside the listener
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(resolved, true);
    // P1-6: 查重 (1st call) + ingest (2nd call)
    assert.strictEqual(fetchCalls.length, 2);
    assert.match(fetchCalls[0].url, /\/api\/papers\?keyword=/);
    assert.match(fetchCalls[1].url, /\/api\/ingest\/url$/);
    assert.strictEqual(fetchCalls[1].init.method, "POST");
    const body = JSON.parse(fetchCalls[1].init.body);
    assert.strictEqual(body.url, "https://arxiv.org/abs/2101.00001");
    assert.strictEqual(body.title, "Test");
  });

  it("uses custom apiBase from storage", async () => {
    storageApiBase = "http://custom:9999";
    // No title → P1-6 duplicate check is skipped → only 1 fetch (ingest)
    listeners[0]({ action: "save_url", url: "https://arxiv.org/abs/2101.00001" }, {}, () => {});
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(fetchCalls.length, 1);
    assert.match(fetchCalls[0].url, /http:\/\/custom:9999/);
  });

  it("returns error on fetch failure", async () => {
    global.fetch = () => Promise.reject(new Error("network down"));
    let resolved = false;
    const sendResponse = (response) => {
      resolved = true;
      assert.strictEqual(response.success, false);
      assert.match(response.error, /network down/);
    };
    listeners[0]({ action: "save_url", url: "https://arxiv.org/abs/2101.00001" }, {}, sendResponse);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(resolved, true);
  });

  it("returns error on non-ok backend response", async () => {
    global.fetch = () =>
      Promise.resolve(new MockResponse(JSON.stringify({ detail: "bad request" }), { ok: false, status: 400 }));
    let resolved = false;
    const sendResponse = (response) => {
      resolved = true;
      assert.strictEqual(response.success, false);
      assert.match(response.error, /bad request/);
    };
    listeners[0]({ action: "save_url", url: "https://arxiv.org/abs/2101.00001" }, {}, sendResponse);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(resolved, true);
  });
});

// ===========================================================================
// WP-4.1b: save_raw handler — PDF 字节直传到 /api/ingest/raw
// ===========================================================================
describe("background.js save_raw handling", () => {
  let originalChrome;
  let originalFetch;
  let originalImportScripts;
  let listeners;
  let fetchCalls;
  let storageApiBase;

  beforeEach(() => {
    originalChrome = global.chrome;
    originalFetch = global.fetch;
    originalImportScripts = global.importScripts;

    listeners = [];
    fetchCalls = [];
    storageApiBase = "http://localhost:8770";

    global.chrome = {
      storage: {
        local: {
          get: (key, cb) => {
            if (cb) cb({ apiBase: storageApiBase });
            return Promise.resolve({ apiBase: storageApiBase });
          },
        },
      },
      runtime: {
        onMessage: {
          addListener: (listener) => listeners.push(listener),
        },
      },
    };

    // FormData + Blob stubs (MV3 SW lacks DOM, but tests run in Node)
    global.FormData = class MockFormData {
      constructor() {
        this._fields = {};
      }
      append(name, value, filename) {
        this._fields[name] = { value, filename };
      }
      get(name) {
        return this._fields[name]?.value;
      }
    };
    global.Blob = class MockBlob {
      constructor(parts, opts) {
        this.parts = parts;
        this.type = opts?.type || "";
      }
    };

    global.fetch = (url, init) => {
      fetchCalls.push({ url, init });
      return Promise.resolve(
        new MockResponse(
          JSON.stringify({ paperId: "p2", title: "Raw Paper", mode: "pdf" }),
          { ok: true }
        )
      );
    };

    global.importScripts = () => {
      const utilsPath = path.join(__dirname, "utils.js");
      const utils = require(utilsPath);
      global.isValidClipperUrl = utils.isValidClipperUrl;
      global.detectSource = utils.detectSource;
    };

    delete require.cache[require.resolve("./background.js")];
    require("./background.js");
  });

  afterEach(() => {
    global.chrome = originalChrome;
    global.fetch = originalFetch;
    global.importScripts = originalImportScripts;
    delete global.FormData;
    delete global.Blob;
    delete require.cache[require.resolve("./background.js")];
  });

  it("sends save_raw as multipart POST to /api/ingest/raw", async () => {
    const pdfBuffer = new ArrayBuffer(8);
    let resolved = false;
    const sendResponse = (response) => {
      resolved = true;
      assert.strictEqual(response.success, true);
      assert.strictEqual(response.paperId, "p2");
      assert.strictEqual(response.mode, "pdf");
    };
    const result = listeners[0](
      {
        action: "save_raw",
        url: "https://kns.cnki.net/kcms/detail/detail.aspx",
        pdfBuffer,
        filename: "paper.pdf",
        title: "知网论文",
        authors: ["张三"],
        abstract: "摘要",
        year: 2024,
        journal: "计算机学报",
        source: "cnki",
        tags: ["cnki"],
      },
      {},
      sendResponse
    );
    assert.strictEqual(result, true);

    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(resolved, true);
    // P1-6 duplicate check (no title match → 1 search) + ingest/raw POST
    assert.strictEqual(fetchCalls.length, 2);
    assert.match(fetchCalls[1].url, /\/api\/ingest\/raw$/);
    assert.strictEqual(fetchCalls[1].init.method, "POST");
    assert.ok(fetchCalls[1].init.body instanceof FormData, "body should be FormData");
    const fd = fetchCalls[1].init.body;
    assert.strictEqual(fd.get("title"), "知网论文");
    assert.strictEqual(fd.get("source"), "cnki");
    assert.strictEqual(fd.get("year"), "2024");
    assert.strictEqual(fd.get("authors"), JSON.stringify(["张三"]));
    assert.strictEqual(fd.get("tags"), JSON.stringify(["cnki"]));
  });

  it("returns error when no pdfBuffer provided", async () => {
    let resolved = false;
    const sendResponse = (response) => {
      resolved = true;
      assert.strictEqual(response.success, false);
      assert.match(response.error, /No PDF buffer/);
    };
    listeners[0](
      { action: "save_raw", url: "https://kns.cnki.net/x", title: "Test" },
      {},
      sendResponse
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(resolved, true);
  });

  it("returns error on fetch failure", async () => {
    global.fetch = () => Promise.reject(new Error("upload failed"));
    let resolved = false;
    const sendResponse = (response) => {
      resolved = true;
      assert.strictEqual(response.success, false);
      assert.match(response.error, /upload failed/);
    };
    listeners[0](
      {
        action: "save_raw",
        url: "https://kns.cnki.net/x",
        pdfBuffer: new ArrayBuffer(4),
        title: "Test",
      },
      {},
      sendResponse
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(resolved, true);
  });

  it("returns error on non-ok backend response", async () => {
    global.fetch = () =>
      Promise.resolve(
        new MockResponse(JSON.stringify({ detail: "invalid pdf" }), { ok: false, status: 400 })
      );
    let resolved = false;
    const sendResponse = (response) => {
      resolved = true;
      assert.strictEqual(response.success, false);
      assert.match(response.error, /invalid pdf/);
    };
    listeners[0](
      {
        action: "save_raw",
        url: "https://kns.cnki.net/x",
        pdfBuffer: new ArrayBuffer(4),
        title: "Test",
      },
      {},
      sendResponse
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(resolved, true);
  });

  it("passes through alreadyExists from duplicate check", async () => {
    // First fetch = duplicate search returns a match
    let callCount = 0;
    global.fetch = (url) => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve(
          new MockResponse(
            JSON.stringify({
              items: [{ id: "existing_001", title: "知网论文" }],
            }),
            { ok: true }
          )
        );
      }
      return Promise.resolve(
        new MockResponse(
          JSON.stringify({ paperId: "p2", title: "Raw Paper", mode: "pdf" }),
          { ok: true }
        )
      );
    };
    let resolved = false;
    const sendResponse = (response) => {
      resolved = true;
      assert.strictEqual(response.success, true);
      assert.strictEqual(response.alreadyExists, true);
      assert.strictEqual(response.existingPaperId, "existing_001");
    };
    listeners[0](
      {
        action: "save_raw",
        url: "https://kns.cnki.net/x",
        pdfBuffer: new ArrayBuffer(4),
        title: "知网论文",
      },
      {},
      sendResponse
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.strictEqual(resolved, true);
  });
});
