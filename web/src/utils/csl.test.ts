import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  parseDefaultLocale,
  buildRemoteCslUrl,
  BUILT_IN_CSL_STYLES,
  CSL_GITHUB_BASE,
  convertToBibTeX,
  generateLaTeXSnippet,
} from "./csl";

// Mock citeproc-js engine
const mockMakeBibliography = vi.fn();
const mockUpdateItems = vi.fn();
const mockProcessCitationCluster = vi.fn();

vi.mock("citeproc", () => ({
  default: {
    Engine: vi.fn().mockImplementation(() => ({
      updateItems: mockUpdateItems,
      makeBibliography: mockMakeBibliography,
      processCitationCluster: mockProcessCitationCluster,
    })),
  },
}));

describe("parseDefaultLocale", () => {
  it("extracts default-locale regardless of attribute order", () => {
    const xml = `<?xml version="1.0"?>\n<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0" default-locale="zh-CN">\n</style>`;
    expect(parseDefaultLocale(xml)).toBe("zh-CN");
  });

  it("falls back to en-US when default-locale is missing", () => {
    const xml = `<?xml version="1.0"?>\n<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0">`;
    expect(parseDefaultLocale(xml)).toBe("en-US");
  });

  it("falls back to en-US on invalid XML", () => {
    expect(parseDefaultLocale("not xml")).toBe("en-US");
  });
});

describe("fetchCslLocale", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.fetch = vi.fn();
  });

  it("returns locale XML on success", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      text: async () => "<locale></locale>",
    });
    const { fetchCslLocale } = await import("./csl");
    const result = await fetchCslLocale("zh-CN");
    expect(result).toBe("<locale></locale>");
  });

  it("returns null on non-ok response", async () => {
    (global.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 404,
    });
    const { fetchCslLocale } = await import("./csl");
    const result = await fetchCslLocale("zh-CN");
    expect(result).toBeNull();
  });

  it("returns null on network error and logs warning", async () => {
    const consoleSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    (global.fetch as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("network"));
    const { fetchCslLocale } = await import("./csl");
    const result = await fetchCslLocale("zh-CN");
    expect(result).toBeNull();
    expect(consoleSpy).toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe("buildRemoteCslUrl", () => {
  it("builds a remote CSL URL from a file name", () => {
    expect(buildRemoteCslUrl("nature.csl")).toBe(`${CSL_GITHUB_BASE}/nature.csl`);
  });
});

describe("BUILT_IN_CSL_STYLES", () => {
  it("includes GB/T 7714 as the first built-in style", () => {
    expect(BUILT_IN_CSL_STYLES[0].id).toBe("china-national-standard-gb-t-7714-2015-numeric");
  });

  it("marks local styles correctly", () => {
    const localStyles = BUILT_IN_CSL_STYLES.filter((s) => s.local);
    expect(localStyles.length).toBeGreaterThanOrEqual(6);
  });
});

describe("convertToBibTeX", () => {
  it("converts a journal article to BibTeX", () => {
    const items = [
      {
        id: "paper-1",
        type: "article-journal",
        title: "Attention Is All You Need",
        author: [
          { family: "Vaswani", given: "Ashish" },
          { family: "Shazeer", given: "Noam" },
        ],
        issued: { "date-parts": [[2017]] },
        "container-title": "arXiv preprint",
        URL: "https://arxiv.org/abs/1706.03762",
      },
    ];
    const bibtex = convertToBibTeX(items as never);
    expect(bibtex).toContain("@article{paper-1,");
    expect(bibtex).toContain("title={Attention Is All You Need}");
    expect(bibtex).toContain("author={Vaswani, Ashish and Shazeer, Noam}");
    expect(bibtex).toContain("year=2017");
    expect(bibtex).toContain("journal={arXiv preprint}");
    expect(bibtex).toContain("url={https://arxiv.org/abs/1706.03762}");
  });

  it("converts an arXiv preprint to article type", () => {
    const items = [
      {
        id: "1706.03762",
        type: "article",
        title: "Attention Is All You Need",
        author: [{ family: "Vaswani", given: "Ashish" }],
        issued: { "date-parts": [[2017]] },
      },
    ];
    const bibtex = convertToBibTeX(items as never);
    expect(bibtex).toContain("@article{1706_03762,");
  });

  it("returns empty string for empty items", () => {
    expect(convertToBibTeX([])).toBe("");
  });

  it("sanitizes id for BibTeX key", () => {
    const items = [{ id: "10.1000/182", type: "article-journal", title: "DOI Paper", author: [] }];
    const bibtex = convertToBibTeX(items as never);
    expect(bibtex).toContain("@article{10_1000_182,");
  });
});

describe("generateLaTeXSnippet", () => {
  it("generates a minimal LaTeX document with cite commands", () => {
    const items = [{ id: "paper-1" }, { id: "paper-2" }] as never;
    const snippet = generateLaTeXSnippet(items);
    expect(snippet).toContain("\\bibliographystyle{plain}");
    expect(snippet).toContain("\\bibliography{references}");
    expect(snippet).toContain("\\cite{paper-1}");
    expect(snippet).toContain("\\cite{paper-2}");
  });

  it("sanitizes cite keys", () => {
    const items = [{ id: "10.1000/182" }] as never;
    const snippet = generateLaTeXSnippet(items);
    expect(snippet).toContain("\\cite{10_1000_182}");
  });

  it("uses custom bib filename and style", () => {
    const items = [{ id: "p1" }] as never;
    const snippet = generateLaTeXSnippet(items, { bibFilename: "refs", style: "ieee" });
    expect(snippet).toContain("\\bibliographystyle{ieee}");
    expect(snippet).toContain("\\bibliography{refs}");
  });
});
