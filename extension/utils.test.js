const { describe, it } = require("node:test");
const assert = require("node:assert");
const { isValidClipperUrl, detectSource } = require("./utils");

describe("isValidClipperUrl", () => {
  it("accepts arxiv abs URLs", () => {
    assert.strictEqual(isValidClipperUrl("https://arxiv.org/abs/2401.00001"), true);
  });

  it("accepts arxiv pdf URLs", () => {
    assert.strictEqual(isValidClipperUrl("https://arxiv.org/pdf/2401.00001.pdf"), true);
  });

  it("accepts CNKI URLs", () => {
    assert.strictEqual(isValidClipperUrl("https://kns.cnki.net/kcms/detail/detail.aspx"), true);
    assert.strictEqual(isValidClipperUrl("https://www.cnki.com.cn/Article/CJFDTotal"), true);
  });

  it("accepts Google Scholar URLs", () => {
    assert.strictEqual(isValidClipperUrl("https://scholar.google.com/scholar?q=test"), true);
    assert.strictEqual(isValidClipperUrl("https://scholar.google.com.hk/scholar?q=test"), true);
  });

  it("rejects unsupported URLs", () => {
    assert.strictEqual(isValidClipperUrl("https://example.com/paper"), false);
    assert.strictEqual(isValidClipperUrl("https://github.com/paper"), false);
  });

  it("rejects invalid URLs", () => {
    assert.strictEqual(isValidClipperUrl("not-a-url"), false);
    assert.strictEqual(isValidClipperUrl(""), false);
  });
});

describe("detectSource", () => {
  it("detects arxiv", () => {
    assert.strictEqual(detectSource("https://arxiv.org/abs/2401.00001"), "arxiv");
  });

  it("detects cnki", () => {
    assert.strictEqual(detectSource("https://kns.cnki.net/kcms/detail/detail.aspx"), "cnki");
    assert.strictEqual(detectSource("https://www.cnki.com.cn/Article/CJFDTotal"), "cnki");
  });

  it("detects google_scholar", () => {
    assert.strictEqual(detectSource("https://scholar.google.com/scholar?q=test"), "google_scholar");
    assert.strictEqual(detectSource("https://scholar.google.com.hk/scholar?q=test"), "google_scholar");
  });

  it("falls back to other", () => {
    assert.strictEqual(detectSource("https://example.com/paper"), "other");
  });

  it("handles invalid URLs", () => {
    assert.strictEqual(detectSource("not-a-url"), "other");
    assert.strictEqual(detectSource(""), "other");
  });
});
