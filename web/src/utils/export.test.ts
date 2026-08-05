/**
 * 前端多格式导出（Word/LaTeX）参考文献作者渲染测试。
 *
 * 背景（2026-08-03）：后端全库作者乱码修复后，references 的 authors 已是
 * 正确人名列表。本测试验证前端 exportLatex / exportWord / exportMarkdown
 * 消费后端返回的 references 时，参考文献中作者名正确渲染（而非乱码）。
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { exportLatex, exportMarkdown, exportWord } from "./export";

// 模拟后端修复后的 references（authors 为正常人名列表）
const refs = [
  { id: "2607.20422", title: "DeepSeek-V3 技术报告", authors: ["Cosmin Pohoata"], year: 2026 },
  {
    id: "2106.09685",
    title: "LoRA: Low-Rank Adaptation",
    authors: ["Edward J. Hu", "Yelong Shen", "Phillip Wallis"],
    year: 2021,
  },
];

let captured: Blob | null;

beforeEach(() => {
  captured = null;
  // jsdom 未实现 Blob URL API，需 stub 并捕获生成的 Blob 以便断言内容
  URL.createObjectURL = vi.fn((blob: Blob) => {
    captured = blob;
    return "blob:mock";
  });
  URL.revokeObjectURL = vi.fn();
  // 阻止 download() 中的 a.click() 触发 jsdom 模拟导航（产生无意义报错噪音）
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

/** jsdom 的 Blob 可能缺少 text()，用 FileReader 兜底读取。 */
async function readBlob(blob: Blob): Promise<string> {
  if (typeof (blob as Blob & { text?: () => Promise<string> }).text === "function") {
    return (blob as Blob & { text: () => Promise<string> }).text();
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(blob);
  });
}

describe("exportLatex 参考文献作者渲染", () => {
  it("bibitem 中使用正确作者名（单作者与多作者）", async () => {
    exportLatex("# 标题", refs, "test");
    expect(captured).not.toBeNull();
    const text = await readBlob(captured!);

    expect(text).toContain(
      "\\bibitem{1} DeepSeek-V3 技术报告. Cosmin Pohoata. 2026. arXiv:2607.20422.",
    );
    expect(text).toContain(
      "\\bibitem{2} LoRA: Low-Rank Adaptation. Edward J. Hu, Yelong Shen, Phillip Wallis. 2021. arXiv:2106.09685.",
    );
    // 旧 bug 特征：逐字符拆分乱码不应出现
    expect(text).not.toContain("[, C");
  });

  it("作者超 3 人时截断为 et al.", async () => {
    const many = [
      {
        id: "p1",
        title: "Many Authors",
        authors: ["A. One", "B. Two", "C. Three", "D. Four", "E. Five"],
        year: 2020,
      },
    ];
    exportLatex("# T", many, "t");
    const text = await readBlob(captured!);
    expect(text).toContain("A. One, B. Two, C. Three et al.");
  });
});

describe("exportWord 参考文献作者渲染", () => {
  it("HTML 参考文献列表包含正确作者名", async () => {
    exportWord("<h1>标题</h1>", refs, "test");
    expect(captured).not.toBeNull();
    const text = await readBlob(captured!);

    expect(text).toContain("<h2>参考文献</h2>");
    expect(text).toContain("Cosmin Pohoata");
    expect(text).toContain("Edward J. Hu, Yelong Shen, Phillip Wallis");
    expect(text).not.toContain("[, C");
  });
});

describe("exportMarkdown 参考文献作者渲染", () => {
  it("Markdown 参考文献列表包含正确作者名", async () => {
    exportMarkdown("# 标题", refs, "test");
    expect(captured).not.toBeNull();
    const text = await readBlob(captured!);

    expect(text).toContain("## 参考文献");
    expect(text).toContain("Cosmin Pohoata");
    expect(text).toContain("Edward J. Hu, Yelong Shen, Phillip Wallis");
    expect(text).not.toContain("[, C");
  });
});
