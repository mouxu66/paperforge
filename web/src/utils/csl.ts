import type { CslItem } from "@/api/types";

// citeproc-js is a UMD module without default export in its types.
// We import the factory function and cast it.
import CSL from "citeproc";

export interface CslStyleInfo {
  id: string;
  name: string;
  local?: boolean;
  url?: string;
}

/** Curated CSL styles shipped locally under /csl/ */
export const BUILT_IN_CSL_STYLES: CslStyleInfo[] = [
  {
    id: "china-national-standard-gb-t-7714-2015-numeric",
    name: "GB/T 7714-2015 (numeric)",
    local: true,
  },
  { id: "apa", name: "APA 7th edition", local: true },
  { id: "ieee", name: "IEEE", local: true },
  { id: "nature", name: "Nature", local: true },
  { id: "chicago-author-date", name: "Chicago (author-date)", local: true },
  { id: "modern-language-association", name: "MLA 9th edition", local: true },
  { id: "acm-sigchi", name: "ACM SIGCHI", local: false },
  { id: "acm-siggraph", name: "ACM SIGGRAPH", local: false },
  { id: "american-chemical-society", name: "ACS", local: false },
  { id: "american-medical-association", name: "AMA", local: false },
  { id: "cell", name: "Cell", local: false },
  { id: "elsevier-harvard", name: "Elsevier Harvard", local: false },
  { id: "frontiers", name: "Frontiers", local: false },
  { id: "plos-one", name: "PLOS ONE", local: false },
  { id: "science", name: "Science", local: false },
  { id: "springer-basic-author-date", name: "Springer (author-date)", local: false },
  { id: "springer-lecture-notes-in-computer-science", name: "Springer LNCS", local: false },
];

/** Remote CSL repository base URL */
export const CSL_GITHUB_BASE =
  "https://raw.githubusercontent.com/citation-style-language/styles/master";

/** Remote CSL locale repository base URL */
export const CSL_LOCALE_BASE =
  "https://raw.githubusercontent.com/citation-style-language/locales/master";

/** Build a remote CSL URL from a style file name (e.g. "apa.csl") */
export function buildRemoteCslUrl(fileName: string): string {
  return `${CSL_GITHUB_BASE}/${fileName}`;
}

/** Fetch a CSL XML string from a URL or local path */
export async function fetchCslXml(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`Failed to fetch CSL style: ${res.status} ${res.statusText}`);
  }
  return res.text();
}

/** Fetch a CSL locale XML string */
export async function fetchCslLocale(lang: string): Promise<string | null> {
  try {
    const res = await fetch(`${CSL_LOCALE_BASE}/locales-${lang}.xml`);
    if (!res.ok) return null;
    return res.text();
  } catch (err) {

    console.warn(`Failed to fetch CSL locale ${lang}:`, err);
    return null;
  }
}

/** Parse the default locale from a CSL style XML */
export function parseDefaultLocale(cslXml: string): string {
  try {
    const parser = new DOMParser();
    const doc = parser.parseFromString(cslXml, "application/xml");
    const styleNode = doc.getElementsByTagName("style")[0];
    return styleNode?.getAttribute("default-locale") ?? "en-US";
  } catch {
    return "en-US";
  }
}

/** 转义 HTML 特殊字符（与 utils/export.ts 的 escapeHtml 一致，另补引号）。 */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 递归转义 CslItem 中的全部字符串字段（title/authors/容器名等不可信元数据）。 */
function escapeCslItem(value: unknown): unknown {
  if (typeof value === "string") return escapeHtml(value);
  if (Array.isArray(value)) return value.map(escapeCslItem);
  if (value !== null && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = escapeCslItem(v);
    }
    return out;
  }
  return value;
}

/** Build a citeproc-js sys object for the given items and locale cache */
function buildSys(items: CslItem[], localeCache: Map<string, string>) {
  // 论文元数据（title/authors 等）来自用户上传 / arXiv 自动导入，不可信。
  // citeproc-js 不做 HTML 转义，变量值会原样进入渲染结果，再被
  // dangerouslySetInnerHTML 注入 → 存储型 XSS。在喂给引擎前递归转义全部
  // 字符串字段；citeproc 自身的排版标签（<span>/<i> 等）不受影响。
  const escapedMap = new Map<string, CslItem>(
    items.map((item) => [item.id, escapeCslItem(item) as CslItem]),
  );
  return {
    retrieveItem: (id: string) => escapedMap.get(id) ?? { id, title: "" },
    retrieveLocale: (lang: string) => localeCache.get(lang) ?? null,
  };
}

export interface RenderedBibliography {
  entries: string[];
  bibliography: string;
}

/** Render a bibliography using citeproc-js */
export async function renderBibliography(
  items: CslItem[],
  cslXml: string,
): Promise<RenderedBibliography> {
  if (items.length === 0) {
    return { entries: [], bibliography: "" };
  }

  const locale = parseDefaultLocale(cslXml);
  const localeCache = new Map<string, string>();
  const localeXml = await fetchCslLocale(locale);
  if (localeXml) {
    localeCache.set(locale, localeXml);
  }

  const sys = buildSys(items, localeCache);
  const citeproc = new CSL.Engine(sys as never, cslXml, locale);

  const itemIds = items.map((item) => item.id);
  citeproc.updateItems(itemIds);

  const result = citeproc.makeBibliography();
  const entries: string[] = result?.[1] ?? [];
  const bibliography = entries.map((entry) => `<p>${entry}</p>`).join("\n");

  return { entries, bibliography };
}

/** Render inline citations for a list of item IDs */
export async function renderCitations(
  items: CslItem[],
  cslXml: string,
  citations: { id: string; prefix?: string; suffix?: string }[],
): Promise<string[]> {
  if (items.length === 0 || citations.length === 0) {
    return [];
  }

  const locale = parseDefaultLocale(cslXml);
  const localeCache = new Map<string, string>();
  const localeXml = await fetchCslLocale(locale);
  if (localeXml) {
    localeCache.set(locale, localeXml);
  }

  const sys = buildSys(items, localeCache);
  const citeproc = new CSL.Engine(sys as never, cslXml, locale);

  const outputs: string[] = [];
  for (const citation of citations) {
    try {
      const result = citeproc.processCitationCluster(
        {
          citationID:
            typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
              ? crypto.randomUUID()
              : `cite-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
          citationItems: [{ id: citation.id, prefix: citation.prefix, suffix: citation.suffix }],
          properties: { noteIndex: 0 },
        },
        [],
        [],
      );
      const text = (result?.[1]?.[0]?.[1] as string) ?? "";
      outputs.push(text);
    } catch {
      outputs.push(`[${citation.id}]`);
    }
  }
  return outputs;
}

/** Escape a BibTeX value by wrapping braces around the whole string */
function escapeBibtexValue(value: string): string {
  // Strip braces to avoid breaking BibTeX parsing; other special characters
  // are protected by the outer braces.
  return `{${value.replace(/[{}]/g, "")}}`;
}

/** Generate a minimal LaTeX snippet that uses the given BibTeX entries */
export function generateLaTeXSnippet(
  items: CslItem[],
  options: { bibFilename?: string; style?: string } = {},
): string {
  const { bibFilename = "references", style = "plain" } = options;
  const citeKeys = items.map((item) => item.id.replace(/[^a-zA-Z0-9_-]/g, "_"));
  const citeCommands = citeKeys.map((key) => `\\cite{${key}}`).join("\n");
  return `\\documentclass[11pt]{article}
\\usepackage[utf8]{inputenc}
\\usepackage[T1]{fontenc}
\\usepackage{hyperref}

% CSL-generated bibliography
\\bibliographystyle{${style}}

\\begin{document}

% Insert citations in your text:
${citeCommands || "% \\cite{key}"}

\\bibliography{${bibFilename}}

\\end{document}
`;
}

/** Convert CSL-JSON items to a BibTeX string */
export function convertToBibTeX(items: CslItem[]): string {
  if (items.length === 0) return "";

  const lines: string[] = [];
  for (const item of items) {
    const type = item.type === "article-journal" ? "article" : (item.type ?? "misc");
    const key = item.id.replace(/[^a-zA-Z0-9_-]/g, "_");
    const fields: string[] = [];

    if (item.title) fields.push(`  title=${escapeBibtexValue(item.title)}`);
    if (item.author && item.author.length > 0) {
      const authorStr = item.author
        .map((a) => {
          if (typeof a === "string") return a;
          const family = a.family ?? "";
          const given = a.given ?? "";
          return given ? `${family}, ${given}` : family;
        })
        .join(" and ");
      fields.push(`  author=${escapeBibtexValue(authorStr)}`);
    }
    if (item.issued?.["date-parts"]?.[0]?.[0]) {
      fields.push(`  year=${item.issued["date-parts"][0][0]}`);
    }
    if (item["container-title"]) {
      fields.push(`  journal=${escapeBibtexValue(item["container-title"])}`);
    }
    if (item.URL) fields.push(`  url=${escapeBibtexValue(item.URL)}`);
    if (item.DOI) fields.push(`  doi=${escapeBibtexValue(item.DOI)}`);

    lines.push(`@${type}{${key},`);
    lines.push(...fields);
    lines.push("}");
    lines.push("");
  }
  return lines.join("\n");
}

/** Download a JSON blob as a file */
export function downloadJson(data: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** Copy rich text (HTML) to the clipboard with plain-text fallback */
export async function copyRichText(html: string): Promise<void> {
  const plain = html.replace(/<[^>]+>/g, "");
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
    const blob = new Blob([html], { type: "text/html" });
    const item = new ClipboardItem({
      "text/html": blob,
      "text/plain": new Blob([plain], { type: "text/plain" }),
    });
    await navigator.clipboard.write([item]);
  } else if (navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(plain);
  } else {
    throw new Error("Clipboard API not supported");
  }
}
