import { Copy, Trash2, Highlighter, History, BookOpen, Languages } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  ColorPicker,
  Empty,
  Input,
  List,
  App,
  Popover,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Tooltip,
} from "antd";

import { useTranslation } from "react-i18next";
import * as pdfjsLib from "pdfjs-dist";

/** pdfjs-dist 文本项的局部类型（避免依赖内部导出路径） */
interface PdfTextItem {
  str?: string;
  transform?: number[];
  width?: number;
  height?: number;
}
// Vite 通过 ?url 后缀将 worker 文件作为 URL 资源导入
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { Paper, TranslationHistoryItem } from "@/api/types";
import type { HighlightColor } from "@/api/types";
import {
  deleteTranslationHistory,
  fetchTranslationHistory,
  saveTranslationHistory,
  translatePaperText,
} from "@/api/papers";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

// ---------------------------------------------------------------------------
// 类型与常量
// ---------------------------------------------------------------------------
/** 单条高亮记录（localStorage 主存储，与后端 PdfAnnotation 结构对齐） */
interface HighlightRecord {
  id: string;
  paperId: string;
  page: number;
  /** 归一化矩形列表：[x, y, w, h, ...]，坐标为 0-1 相对页面尺寸 */
  rects: number[];
  /** 选中的原文（便于列表展示与跳转） */
  text: string;
  color: string;
  note: string;
  createdAt: string;
}

const COLOR_PRESETS: { labelKey: string; value: HighlightColor }[] = [
  { labelKey: "pdf.colorYellow", value: "#FFEB3B" },
  { labelKey: "pdf.colorBlue", value: "#4FC3F7" },
  { labelKey: "pdf.colorGreen", value: "#81C784" },
  { labelKey: "pdf.colorPink", value: "#F48FB1" },
];

const STORAGE_KEY_PREFIX = "pf_pdf_highlights_";
const TRANSLATION_HISTORY_KEY_PREFIX = "pf_translation_history_";
const RENDER_SCALE = 1.4;
/** 初始渲染页数上限，超出后滚动到底部时懒加载更多 */
const INITIAL_PAGE_BATCH = 3;

interface PDFViewerProps {
  paper: Paper;
  /** PDF 数据源 URL；若为空则使用 arXiv 默认 PDF 链接 */
  pdfUrl: string;
  /** 初始页码（从 URL ?page= 传入） */
  initialPage?: number;
  /** 要高亮的文本（从 URL ?highlight= 传入） */
  highlightText?: string;
}

/** 生成 UUID（避免依赖额外包） */
function uuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `h_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

/** 清洗 PDF 选中文本 */
function cleanSelectedText(raw: string): string {
  return raw.replace(/-\n/g, "").replace(/\n/g, " ").replace(/\s+/g, " ").trim();
}

interface TranslationPanelProps {
  original: string;
  targetLanguage: string;
  onTargetLanguageChange: (lang: string) => void;
  translating: boolean;
  translationResult: string;
  onTranslate: () => void;
  onCopy: () => void;
  history: TranslationHistoryItem[];
  onSelectHistory: (item: TranslationHistoryItem) => void;
  onDeleteHistory: (id: string) => void;
}

/** 翻译面板：源文/译文对照 + 目标语言选择 + 历史记录 */
function TranslationPanel({
  original,
  targetLanguage,
  onTargetLanguageChange,
  translating,
  translationResult,
  onTranslate,
  onCopy,
  history,
  onSelectHistory,
  onDeleteHistory,
}: TranslationPanelProps) {
  const { t } = useTranslation();
  const cleaned = original
    ? original.replace(/-\n/g, "").replace(/\n/g, " ").replace(/\s+/g, " ").trim()
    : "";
  return (
    <div style={{ padding: "4px 0" }}>
      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: "var(--pf-text-muted)", marginBottom: 6 }}>
          {t("pdf.targetLanguage", "目标语言")}
        </div>
        <Select
          value={targetLanguage}
          onChange={onTargetLanguageChange}
          size="small"
          style={{ width: "100%" }}
          options={[
            { value: "zh-CN", label: "简体中文" },
            { value: "zh-TW", label: "繁體中文" },
            { value: "en", label: "English" },
            { value: "ja", label: "日本語" },
            { value: "de", label: "Deutsch" },
            { value: "fr", label: "Français" },
          ]}
        />
      </div>

      <div style={{ marginBottom: 12 }}>
        <div style={{ fontSize: 12, color: "var(--pf-text-muted)", marginBottom: 6 }}>
          {t("pdf.originalText", "原文")}
        </div>
        <div
          style={{
            padding: 10,
            background: "var(--pf-bg-tertiary)",
            borderRadius: 6,
            fontSize: 13,
            color: "var(--pf-text-primary)",
            lineHeight: 1.6,
            maxHeight: 160,
            overflowY: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {cleaned || (
            <span style={{ color: "var(--pf-text-placeholder)" }}>
              {t("pdf.selectTextToTranslate", "请在 PDF 中选中文本")}
            </span>
          )}
        </div>
      </div>

      <Button
        type="primary"
        size="small"
        block
        loading={translating}
        disabled={!cleaned}
        onClick={onTranslate}
        style={{ marginBottom: 12 }}
      >
        {translating ? t("pdf.translating", "翻译中…") : t("pdf.translate", "翻译")}
      </Button>

      {translationResult && (
        <>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 6,
            }}
          >
            <div style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
              {t("pdf.translationResult", "翻译结果")}
            </div>
            <Button size="small" type="text" icon={<Copy />} onClick={onCopy}>
              {t("common.copy", "复制")}
            </Button>
          </div>
          <div
            style={{
              padding: 10,
              background: "var(--pf-primary-soft)",
              borderRadius: 6,
              fontSize: 13,
              color: "var(--pf-text-primary)",
              lineHeight: 1.6,
              maxHeight: 240,
              overflowY: "auto",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
            }}
          >
            {translationResult}
          </div>
        </>
      )}

      {history.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div
            style={{
              fontSize: 12,
              color: "var(--pf-text-muted)",
              marginBottom: 8,
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            <History />
            {t("pdf.translationHistory", "翻译历史")}
          </div>
          <List
            size="small"
            dataSource={history}
            renderItem={(item) => (
              <List.Item
                style={{ padding: "6px 4px", cursor: "pointer" }}
                onClick={() => onSelectHistory(item)}
                actions={[
                  <Button
                    key="delete"
                    size="small"
                    type="text"
                    danger
                    icon={<Trash2 />}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteHistory(item.id);
                    }}
                  />,
                ]}
              >
                <div style={{ width: "100%" }}>
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--pf-text-secondary)",
                      lineHeight: 1.4,
                      marginBottom: 2,
                    }}
                  >
                    {item.originalText.slice(0, 60)}
                    {item.originalText.length > 60 ? "…" : ""}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--pf-text-placeholder)" }}>
                    {item.targetLanguage} ·{" "}
                    {(() => {
                      try {
                        return new Date(item.createdAt).toLocaleString("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        });
                      } catch {
                        return item.createdAt;
                      }
                    })()}
                  </div>
                </div>
              </List.Item>
            )}
          />
        </div>
      )}
    </div>
  );
}

/** 按 paperId 加载高亮记录（localStorage） */
function loadHighlights(paperId: string): HighlightRecord[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + paperId);
    if (!raw) return [];
    const arr = JSON.parse(raw) as HighlightRecord[];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

/** 保存高亮记录到 localStorage */
function saveHighlights(paperId: string, records: HighlightRecord[]): void {
  try {
    localStorage.setItem(STORAGE_KEY_PREFIX + paperId, JSON.stringify(records));
  } catch {
    // 存储满或被禁用时静默降级
  }
}

/** 按 paperId 加载翻译历史（localStorage 兜底） */
function loadTranslationHistoryLocal(paperId: string): TranslationHistoryItem[] {
  try {
    const raw = localStorage.getItem(TRANSLATION_HISTORY_KEY_PREFIX + paperId);
    if (!raw) return [];
    const arr = JSON.parse(raw) as TranslationHistoryItem[];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

/** 保存翻译历史到 localStorage */
function saveTranslationHistoryLocal(paperId: string, items: TranslationHistoryItem[]): void {
  try {
    localStorage.setItem(TRANSLATION_HISTORY_KEY_PREFIX + paperId, JSON.stringify(items));
  } catch {
    // 存储满或被禁用时静默降级
  }
}

/**
 * 将 window.Selection 的若干 ClientRect 转换为相对页面容器的归一化矩形。
 * 返回扁平数组 [x, y, w, h, ...]，坐标范围 0-1，便于跨缩放比例复现高亮。
 */
function selectionToNormalizedRects(
  selection: Selection,
  pageEl: HTMLElement,
  pageWidth: number,
  pageHeight: number,
): number[] {
  const range = selection.getRangeAt(0);
  const rects = range.getClientRects();
  const containerRect = pageEl.getBoundingClientRect();
  const out: number[] = [];
  for (let i = 0; i < rects.length; i++) {
    const r = rects[i];
    const x = (r.left - containerRect.left) / pageWidth;
    const y = (r.top - containerRect.top) / pageHeight;
    const w = r.width / pageWidth;
    const h = r.height / pageHeight;
    // 过滤掉退化的零尺寸矩形
    if (w > 0.001 && h > 0.001) {
      out.push(x, y, w, h);
    }
  }
  return out;
}

export default function PDFViewer({ paper, pdfUrl, initialPage, highlightText }: PDFViewerProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const containerRef = useRef<HTMLDivElement>(null);
  // 页面渲染容器映射：pageNo -> { wrapper, canvas, textLayerDiv, width, height }
  const pageRefs = useRef<Map<number, HTMLDivElement>>(new Map());
  const abortRef = useRef<boolean>(false);
  // 共享的 PDF 文档代理（加载一次，渲染与翻页共用，避免重复 getDocument）
  const pdfDocRef = useRef<pdfjsLib.PDFDocumentProxy | null>(null);
  // 待渲染页队列（totalPages 就绪后渲染前 N 页）
  const [pendingRender, setPendingRender] = useState(0);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [totalPages, setTotalPages] = useState(0);
  const [renderedPages, setRenderedPages] = useState(INITIAL_PAGE_BATCH);
  const [highlights, setHighlights] = useState<HighlightRecord[]>(() => loadHighlights(paper.id));
  const [currentColor, setCurrentColor] = useState<string>("#FFEB3B");
  const [activePage, setActivePage] = useState<number>(Math.max(1, initialPage || 1));
  // 临时高亮（来自 URL highlight 参数）
  const [tempHighlights, setTempHighlights] = useState<
    { page: number; rects: number[]; text: string }[]
  >([]);
  // 待确认的高亮（选中文本后弹出的 Popover 数据）
  const [pending, setPending] = useState<{
    page: number;
    rects: number[];
    text: string;
    note: string;
  } | null>(null);
  // WP-2.7: 浮动菜单当前视图（menu / highlight / translation）
  const [popoverView, setPopoverView] = useState<"menu" | "highlight" | "translation">("menu");

  // 每次选中文本时重置浮动菜单视图
  const pendingText = pending?.text;
  useEffect(() => {
    if (pendingText) setPopoverView("menu");
  }, [pendingText]);
  // WP-2.7: 翻译状态
  const [translating, setTranslating] = useState(false);
  const [translationResult, setTranslationResult] = useState<string>("");
  const [translationOriginal, setTranslationOriginal] = useState<string>("");
  const [translationHistory, setTranslationHistory] = useState<TranslationHistoryItem[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarTab, setSidebarTab] = useState<"highlights" | "translation">("highlights");
  const [targetLanguage, setTargetLanguage] = useState<string>(() => {
    try {
      return localStorage.getItem("pf_pdf_target_language") || "zh-CN";
    } catch {
      return "zh-CN";
    }
  });
  const isSelectingHistoryRef = useRef(false);
  const translationAbortRef = useRef(0);
  const initialNavDone = useRef(false);

  // 切换论文时重置深链导航标记
  useEffect(() => {
    initialNavDone.current = false;
  }, [pdfUrl]);

  // 同步高亮到 localStorage
  useEffect(() => {
    saveHighlights(paper.id, highlights);
  }, [highlights, paper.id]);

  // WP-2.7: 加载翻译历史（后端优先，失败时回退 localStorage）
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const items = await fetchTranslationHistory(paper.id);
        if (!cancelled) setTranslationHistory(items);
      } catch {
        if (!cancelled) setTranslationHistory(loadTranslationHistoryLocal(paper.id));
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [paper.id]);

  // WP-2.7: 持久化目标语言偏好
  useEffect(() => {
    try {
      localStorage.setItem("pf_pdf_target_language", targetLanguage);
    } catch {
      // ignore
    }
  }, [targetLanguage]);

  // 加载 PDF（单次 getDocument，结果存入 pdfDocRef 供渲染复用）
  useEffect(() => {
    abortRef.current = false;
    setLoading(true);
    setError("");
    pageRefs.current.clear();
    setRenderedPages(INITIAL_PAGE_BATCH);
    setPendingRender(0);

    let task: pdfjsLib.PDFDocumentLoadingTask | null = null;

    const load = async () => {
      try {
        task = pdfjsLib.getDocument({
          url: pdfUrl,
          // 关闭 CMap 警告：使用标准字体路径
          cMapUrl: "https://unpkg.com/pdfjs-dist@4.7.76/cmaps/",
          cMapPacked: true,
        });
        const pdfDoc = await task.promise;
        if (abortRef.current) {
          pdfDoc.destroy().catch(() => {});
          return;
        }
        pdfDocRef.current = pdfDoc;
        setTotalPages(pdfDoc.numPages);
        setActivePage(1);
        // 触发首次渲染
        setPendingRender((n) => n + 1);
      } catch (e) {
        if (abortRef.current) return;
        const msg = e instanceof Error ? e.message : String(e);
        setError(t("pdf.loadFailed", { msg }));
      } finally {
        if (!abortRef.current) setLoading(false);
      }
    };
    load();

    return () => {
      abortRef.current = true;
      task?.destroy().catch(() => {});
      pdfDocRef.current?.destroy().catch(() => {});
      pdfDocRef.current = null;
    };
  }, [pdfUrl, t]);

  // 渲染指定页（canvas + text layer）
  const renderPage = useCallback(async (pageNo: number) => {
    const pdfDoc = pdfDocRef.current;
    if (!pdfDoc) return;
    const wrapper = pageRefs.current.get(pageNo);
    if (!wrapper) return;
    // 已渲染则跳过
    if (wrapper.dataset.rendered === "1") return;
    wrapper.dataset.rendered = "1";

    try {
      const page = await pdfDoc.getPage(pageNo);
      const viewport = page.getViewport({ scale: RENDER_SCALE });
      const canvas = wrapper.querySelector("canvas");
      const textDiv = wrapper.querySelector<HTMLElement>(".pf-pdf-text-layer");
      if (!canvas || !textDiv) return;

      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      // 高 DPI 渲染
      const outputScale = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * outputScale);
      canvas.height = Math.floor(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      wrapper.style.width = `${viewport.width}px`;
      wrapper.style.height = `${viewport.height}px`;
      wrapper.dataset.pageWidth = String(viewport.width);
      wrapper.dataset.pageHeight = String(viewport.height);

      const transform = outputScale !== 1 ? [outputScale, 0, 0, outputScale, 0, 0] : undefined;
      await page.render({
        canvasContext: ctx,
        viewport,
        transform: transform as unknown as number[],
      }).promise;

      // 文本层（透明覆盖在 canvas 上，使文字可选）
      textDiv.style.width = `${viewport.width}px`;
      textDiv.style.height = `${viewport.height}px`;
      textDiv.innerHTML = "";
      const textContent = await page.getTextContent();
      const textLayer = new pdfjsLib.TextLayer({
        textContentSource: textContent,
        container: textDiv,
        viewport,
      });
      await textLayer.render();
    } catch (e) {
      // 单页失败不阻塞其他页
      console.warn(`渲染第 ${pageNo} 页失败`, e);
    }
  }, []);

  // 当 totalPages/renderedPages/pendingRender 变化时，渲染前 N 页
  useEffect(() => {
    if (totalPages === 0 || loading || !pdfDocRef.current) return;
    let cancelled = false;
    const run = async () => {
      const end = Math.min(renderedPages, totalPages);
      for (let i = 1; i <= end; i++) {
        if (cancelled) break;
        await renderPage(i);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [renderedPages, totalPages, loading, pendingRender, renderPage]);

  // 深链：确保 initialPage 在渲染范围内
  useEffect(() => {
    if (!initialPage || totalPages === 0) return;
    if (initialPage > 0 && initialPage <= totalPages && renderedPages < initialPage) {
      setRenderedPages(initialPage);
    }
  }, [initialPage, totalPages, renderedPages]);

  // 深链：滚动到 initialPage 并高亮 highlightText
  useEffect(() => {
    if (loading || totalPages === 0 || initialNavDone.current) return;
    const target = initialPage && initialPage > 0 && initialPage <= totalPages ? initialPage : 1;
    const wrapper = pageRefs.current.get(target);
    if (wrapper) {
      wrapper.scrollIntoView({ behavior: "smooth", block: "start" });
      setActivePage(target);
      initialNavDone.current = true;
    }
  }, [loading, totalPages, initialPage]);

  // 深链：搜索并高亮 highlightText
  useEffect(() => {
    if (!highlightText || totalPages === 0 || loading) return;
    const searchAndHighlight = async () => {
      const pdfDoc = pdfDocRef.current;
      if (!pdfDoc) return;
      const matches: { page: number; rects: number[]; text: string }[] = [];
      for (let pageNo = 1; pageNo <= totalPages; pageNo++) {
        if (pageNo > renderedPages) break;
        const page = await pdfDoc.getPage(pageNo).catch(() => null);
        if (!page) continue;
        const textContent = await page.getTextContent();
        const textItems = textContent.items.filter(
          (item) => "str" in item,
        ) as unknown as PdfTextItem[];
        const fullText = textItems.map((item) => item.str || "").join("");
        if (!fullText.includes(highlightText)) continue;
        // 计算高亮矩形：基于 textContent 中每个字符的位置
        const viewport = page.getViewport({ scale: RENDER_SCALE });
        const pageWidth = viewport.width;
        const pageHeight = viewport.height;
        const startIndex = fullText.indexOf(highlightText);
        if (startIndex < 0) continue;
        // 简单处理：只高亮第一个匹配片段
        const endIndex = startIndex + highlightText.length;
        let charIndex = 0;
        const rects: number[] = [];
        for (const item of textItems) {
          const str: string = item.str || "";
          const itemStart = charIndex;
          const itemEnd = charIndex + str.length;
          charIndex = itemEnd;
          if (itemEnd <= startIndex) continue;
          if (itemStart >= endIndex) break;
          const transform = item.transform;
          if (!transform) continue;
          const x = transform[4] / pageWidth;
          const y = 1 - (transform[5] + (item.height || 12)) / pageHeight;
          const w = (item.width || 10) / pageWidth;
          const h = (item.height || 12) / pageHeight;
          rects.push(
            Math.max(0, Math.min(1, x)),
            Math.max(0, Math.min(1, y)),
            Math.max(0, Math.min(1, w)),
            Math.max(0, Math.min(1, h)),
          );
        }
        if (rects.length > 0) {
          matches.push({ page: pageNo, rects, text: highlightText });
          break;
        }
      }
      if (matches.length > 0) {
        setTempHighlights(matches);
        const first = matches[0];
        const wrapper = pageRefs.current.get(first.page);
        if (wrapper) {
          wrapper.scrollIntoView({ behavior: "smooth", block: "start" });
          setActivePage(first.page);
        }
      }
    };
    searchAndHighlight();
  }, [highlightText, totalPages, loading, renderedPages]);

  // 选中文本 → 弹出添加高亮 Popover
  const handleMouseUp = useCallback((pageNo: number, e: React.MouseEvent<HTMLDivElement>) => {
    const sel = window.getSelection();
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
      return;
    }
    const text = sel.toString().trim();
    if (!text) return;
    const wrapper = e.currentTarget;
    const pageWidth = Number(wrapper.dataset.pageWidth || 0);
    const pageHeight = Number(wrapper.dataset.pageHeight || 0);
    if (!pageWidth || !pageHeight) return;
    const rects = selectionToNormalizedRects(sel, wrapper, pageWidth, pageHeight);
    if (rects.length === 0) return;
    setPending({ page: pageNo, rects, text, note: "" });
  }, []);

  const confirmHighlight = () => {
    if (!pending) return;
    const rec: HighlightRecord = {
      id: uuid(),
      paperId: paper.id,
      page: pending.page,
      rects: pending.rects,
      text: pending.text,
      color: currentColor,
      note: pending.note,
      createdAt: new Date().toISOString(),
    };
    setHighlights((prev) => [rec, ...prev]);
    setPending(null);
    window.getSelection()?.removeAllRanges();
    message.success(t("pdf.highlightAdded"));
  };

  const cancelHighlight = () => {
    setPending(null);
    setTranslationResult("");
    setPopoverView("menu");
    window.getSelection()?.removeAllRanges();
  };

  // WP-2.7: 持久化翻译记录到后端（失败时回退 localStorage)
  const persistTranslation = useCallback(
    async (original: string, translated: string, lang: string) => {
      let backendId: string | undefined;
      try {
        const saved = await saveTranslationHistory(paper.id, {
          original_text: original,
          translated_text: translated,
          target_language: lang,
        });
        backendId = saved.id;
      } catch {
        // 后端保存失败时继续用 localStorage 兜底，保证离线/后端异常时仍可查看历史
      }
      const newItem: TranslationHistoryItem = {
        id: backendId || `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        paperId: paper.id,
        originalText: original,
        translatedText: translated,
        targetLanguage: lang,
        createdAt: new Date().toISOString(),
      };
      setTranslationHistory((prev) => {
        const next = [newItem, ...prev].slice(0, 50);
        saveTranslationHistoryLocal(paper.id, next);
        return next;
      });
    },
    [paper.id],
  );

  // WP-2.7: 翻译选中文本
  const handleTranslate = useCallback(
    async (textToTranslate?: string, options?: { skipHistory?: boolean }) => {
      const sourceText = textToTranslate || pending?.text;
      if (!sourceText) return;
      const cleaned = cleanSelectedText(sourceText);
      if (!cleaned) return;

      const requestId = ++translationAbortRef.current;
      setTranslating(true);
      setTranslationResult("");
      setTranslationOriginal(cleaned);
      setPopoverView("translation");
      try {
        const text = cleaned.substring(0, 2000);
        const res = await translatePaperText(paper.id, text, targetLanguage);
        if (requestId !== translationAbortRef.current) return;
        setTranslationResult(res.translation);
        if (!options?.skipHistory) {
          await persistTranslation(cleaned, res.translation, targetLanguage);
        }
      } catch (err0: unknown) {
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        if (requestId !== translationAbortRef.current) return;
        message.error(e?.message || t("pdf.translationError", "翻译失败"));
      } finally {
        if (requestId === translationAbortRef.current) {
          setTranslating(false);
        }
      }
    },
    [paper.id, pending?.text, targetLanguage, t, persistTranslation, message],
  );

  // WP-2.7: 切换目标语言时，若存在当前原文则自动重译
  // handleTranslate 已用 useCallback 稳定；此处仅监听语言变化，避免依赖变化导致循环重译
  const handleTranslateRef = useRef(handleTranslate);
  useEffect(() => {
    handleTranslateRef.current = handleTranslate;
  }, [handleTranslate]);
  useEffect(() => {
    if (!translationOriginal) return;
    if (isSelectingHistoryRef.current) {
      isSelectingHistoryRef.current = false;
      return;
    }
    handleTranslateRef.current(translationOriginal);
  }, [targetLanguage, translationOriginal]);

  // WP-2.7: 从历史记录恢复翻译
  const handleSelectHistory = (item: TranslationHistoryItem) => {
    isSelectingHistoryRef.current = true;
    setTranslationOriginal(item.originalText);
    setTranslationResult(item.translatedText);
    setTargetLanguage(item.targetLanguage);
    setSidebarTab("translation");
    setSidebarOpen(true);
  };

  // WP-2.7: 删除历史记录
  const handleDeleteHistory = async (id: string) => {
    setTranslationHistory((prev) => {
      const next = prev.filter((item) => item.id !== id);
      saveTranslationHistoryLocal(paper.id, next);
      return next;
    });
    try {
      await deleteTranslationHistory(paper.id, id);
    } catch {
      // 后端删除失败时 localStorage 已同步
    }
  };

  const removeHighlight = (id: string) => {
    setHighlights((prev) => prev.filter((h) => h.id !== id));
  };

  const updateNote = (id: string, note: string) => {
    setHighlights((prev) => prev.map((h) => (h.id === id ? { ...h, note } : h)));
  };

  const jumpToHighlight = (rec: HighlightRecord) => {
    const wrapper = pageRefs.current.get(rec.page);
    if (wrapper) {
      wrapper.scrollIntoView({ behavior: "smooth", block: "center" });
      setActivePage(rec.page);
    }
  };

  const loadMorePages = () => {
    setRenderedPages((n) => Math.min(n + INITIAL_PAGE_BATCH, totalPages));
  };

  // 按页码分组高亮（便于渲染覆盖层）
  const highlightsByPage = useMemo(() => {
    const map = new Map<number, HighlightRecord[]>();
    for (const h of highlights) {
      if (!map.has(h.page)) map.set(h.page, []);
      map.get(h.page)!.push(h);
    }
    return map;
  }, [highlights]);

  const pendingStyle = useMemo(
    () => ({ background: currentColor }) as React.CSSProperties,
    [currentColor],
  );

  return (
    <div className="pf-pdf-viewer" style={{ display: "flex", gap: 12 }}>
      {/* 左侧：PDF 渲染区 */}
      <div
        ref={containerRef}
        className="pf-pdf-canvas-wrap"
        style={{
          flex: 1,
          minWidth: 0,
          maxHeight: 720,
          overflow: "auto",
          background: "var(--pf-bg-tertiary)",
          padding: 16,
          borderRadius: 8,
        }}
      >
        {/* 工具栏 */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            marginBottom: 12,
            position: "sticky",
            top: 0,
            background: "#ffffffee",
            padding: "6px 10px",
            borderRadius: 6,
            backdropFilter: "blur(6px)",
            zIndex: 5,
          }}
        >
          <Space size="small">
            <Highlighter style={{ color: currentColor }} />
            <span style={{ fontSize: 12, color: "var(--pf-text-secondary)" }}>
              {t("pdf.highlightColor")}
            </span>
            <ColorPicker
              value={currentColor}
              onChange={(c) => setCurrentColor(c.toHexString())}
              size="small"
              showText
              format="hex"
              presets={[
                {
                  label: t("pdf.presetLabel"),
                  colors: COLOR_PRESETS.map((p) => p.value),
                },
              ]}
            />
          </Space>
          <Space size="small">
            <Tag color="blue">
              {activePage}/{totalPages || "?"}
            </Tag>
            <Tag color="purple">
              {highlights.length} {t("pdf.highlightCount")}
            </Tag>
            <Button
              size="small"
              type={sidebarOpen ? "primary" : "default"}
              icon={<BookOpen />}
              onClick={() => setSidebarOpen((v) => !v)}
            >
              {t("pdf.sidebar", "高亮/翻译")}
            </Button>
          </Space>
        </div>

        {loading && (
          <div style={{ textAlign: "center", padding: "60px 0" }}>
            <Spin tip={t("pdf.loadingPdf")}>
              <div style={{ padding: 24 }} />
            </Spin>
          </div>
        )}
        {error && (
          <div style={{ color: "var(--pf-error)", padding: 24, textAlign: "center" }}>{error}</div>
        )}

        {!loading && !error && (
          <>
            {Array.from({ length: Math.min(renderedPages, totalPages) }).map((_, idx) => {
              const pageNo = idx + 1;
              return (
                <div
                  key={pageNo}
                  data-page={pageNo}
                  ref={(el) => {
                    if (el) pageRefs.current.set(pageNo, el);
                    else pageRefs.current.delete(pageNo);
                  }}
                  onMouseUp={(e) => handleMouseUp(pageNo, e)}
                  style={{
                    position: "relative",
                    margin: "0 auto 16px",
                    background: "var(--pf-bg-primary)",
                    boxShadow: "0 1px 4px rgba(0,0,0,0.1)",
                  }}
                >
                  {/* 页码角标 */}
                  <div
                    style={{
                      position: "absolute",
                      top: 4,
                      right: 8,
                      fontSize: 11,
                      color: "var(--pf-text-placeholder)",
                      zIndex: 4,
                    }}
                  >
                    {pageNo}
                  </div>
                  <canvas />
                  {/* 高亮覆盖层（位于 canvas 与 text layer 之间） */}
                  {(highlightsByPage.get(pageNo) || []).map((h) =>
                    Array.from({ length: h.rects.length / 4 }).map((__, ri) => {
                      const base = ri * 4;
                      const x = h.rects[base];
                      const y = h.rects[base + 1];
                      const w = h.rects[base + 2];
                      const hh = h.rects[base + 3];
                      return (
                        <Tooltip
                          key={`${h.id}-${ri}`}
                          title={h.note || h.text.slice(0, 80)}
                          placement="top"
                        >
                          <div
                            style={{
                              position: "absolute",
                              left: `${x * 100}%`,
                              top: `${y * 100}%`,
                              width: `${w * 100}%`,
                              height: `${hh * 100}%`,
                              background: h.color,
                              opacity: 0.4,
                              pointerEvents: "auto",
                              cursor: "pointer",
                              zIndex: 2,
                              mixBlendMode: "multiply",
                            }}
                            onClick={() => jumpToHighlight(h)}
                          />
                        </Tooltip>
                      );
                    }),
                  )}
                  {/* 临时深链高亮 */}
                  {tempHighlights
                    .filter((h) => h.page === pageNo)
                    .map((h, hi) =>
                      Array.from({ length: h.rects.length / 4 }).map((__, ri) => {
                        const base = ri * 4;
                        const x = h.rects[base];
                        const y = h.rects[base + 1];
                        const w = h.rects[base + 2];
                        const hh = h.rects[base + 3];
                        return (
                          <Tooltip key={`temp-${hi}-${ri}`} title={h.text} placement="top">
                            <div
                              style={{
                                position: "absolute",
                                left: `${x * 100}%`,
                                top: `${y * 100}%`,
                                width: `${w * 100}%`,
                                height: `${hh * 100}%`,
                                background: "#facc15",
                                opacity: 0.5,
                                pointerEvents: "none",
                                zIndex: 2,
                                mixBlendMode: "multiply",
                              }}
                            />
                          </Tooltip>
                        );
                      }),
                    )}
                  {/* 待确认高亮预览 */}
                  {pending && pending.page === pageNo && (
                    <Popover
                      title={
                        popoverView === "menu"
                          ? t("pdf.selectionActions", "选中文本")
                          : popoverView === "highlight"
                            ? t("pdf.addHighlight")
                            : t("pdf.translationResult", "翻译结果")
                      }
                      trigger="click"
                      open
                      onOpenChange={(o) => {
                        if (!o) cancelHighlight();
                      }}
                      content={
                        <div style={{ width: 280 }}>
                          {popoverView === "menu" && (
                            <>
                              <div
                                style={{
                                  padding: 8,
                                  background: "var(--pf-bg-tertiary)",
                                  borderRadius: 4,
                                  fontSize: 13,
                                  lineHeight: 1.5,
                                  color: "var(--pf-text-primary)",
                                  maxHeight: 120,
                                  overflowY: "auto",
                                  whiteSpace: "pre-wrap",
                                  wordBreak: "break-word",
                                  marginBottom: 10,
                                }}
                              >
                                {pending.text}
                              </div>
                              <Space wrap style={{ width: "100%" }}>
                                <Button
                                  size="small"
                                  icon={<Highlighter />}
                                  onClick={() => setPopoverView("highlight")}
                                >
                                  {t("pdf.highlight", "高亮")}
                                </Button>
                                <Button
                                  size="small"
                                  icon={<Languages />}
                                  onClick={() => handleTranslate()}
                                  loading={translating}
                                >
                                  {t("pdf.translate", "翻译")}
                                </Button>
                                <Button
                                  size="small"
                                  icon={<Copy />}
                                  onClick={() => {
                                    navigator.clipboard
                                      .writeText(pending.text)
                                      .then(() => message.success(t("common.copied")))
                                      .catch(() => message.error(t("common.copyFailed")));
                                  }}
                                >
                                  {t("common.copy", "复制")}
                                </Button>
                              </Space>
                            </>
                          )}

                          {popoverView === "highlight" && (
                            <>
                              <Input.TextArea
                                rows={3}
                                placeholder={t("pdf.annotationPlaceholder")}
                                value={pending.note}
                                onChange={(e) =>
                                  setPending((p) => (p ? { ...p, note: e.target.value } : p))
                                }
                              />
                              <div
                                style={{
                                  marginTop: 8,
                                  display: "flex",
                                  gap: 8,
                                  justifyContent: "flex-end",
                                  alignItems: "center",
                                }}
                              >
                                <Button size="small" onClick={() => setPopoverView("menu")}>
                                  {t("common.back")}
                                </Button>
                                <Button size="small" type="primary" onClick={confirmHighlight}>
                                  {t("pdf.confirmHighlight")}
                                </Button>
                              </div>
                              <div
                                style={{
                                  marginTop: 6,
                                  fontSize: 11,
                                  color: "var(--pf-text-placeholder)",
                                }}
                              >
                                {t("pdf.color")}
                                {currentColor}
                              </div>
                            </>
                          )}

                          {popoverView === "translation" && (
                            <>
                              <div
                                style={{
                                  padding: 8,
                                  background: "var(--pf-bg-tertiary)",
                                  borderRadius: 4,
                                  fontSize: 12,
                                  lineHeight: 1.5,
                                  color: "var(--pf-text-secondary)",
                                  maxHeight: 100,
                                  overflowY: "auto",
                                  whiteSpace: "pre-wrap",
                                  wordBreak: "break-word",
                                  marginBottom: 8,
                                }}
                              >
                                {translationOriginal}
                              </div>
                              <div
                                style={{
                                  padding: 8,
                                  background: "var(--pf-primary-soft)",
                                  borderRadius: 4,
                                  maxHeight: 160,
                                  overflowY: "auto",
                                  fontSize: 13,
                                  lineHeight: 1.5,
                                  color: "var(--pf-text-primary)",
                                  whiteSpace: "pre-wrap",
                                  wordBreak: "break-word",
                                }}
                              >
                                {translating ? (
                                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                    <Spin size="small" />
                                    <span>{t("pdf.translating")}</span>
                                  </div>
                                ) : (
                                  translationResult || (
                                    <span style={{ color: "var(--pf-text-placeholder)" }}>
                                      {t("pdf.translationResult")}
                                    </span>
                                  )
                                )}
                              </div>
                              <div
                                style={{
                                  marginTop: 8,
                                  display: "flex",
                                  gap: 8,
                                  justifyContent: "flex-end",
                                }}
                              >
                                <Button
                                  size="small"
                                  icon={<Copy />}
                                  disabled={!translationResult}
                                  onClick={() => {
                                    navigator.clipboard
                                      .writeText(translationResult)
                                      .then(() => message.success(t("common.copied")))
                                      .catch(() => message.error(t("common.copyFailed")));
                                  }}
                                >
                                  {t("common.copy")}
                                </Button>
                                <Button size="small" onClick={() => setPopoverView("menu")}>
                                  {t("common.back")}
                                </Button>
                              </div>
                            </>
                          )}
                        </div>
                      }
                    >
                      <div
                        style={{
                          ...pendingStyle,
                          position: "absolute",
                          left: `${(pending.rects[0] || 0) * 100}%`,
                          top: `${(pending.rects[1] || 0) * 100}%`,
                          width: `${(pending.rects[2] || 0) * 100}%`,
                          height: `${(pending.rects[3] || 0) * 100}%`,
                          opacity: 0.5,
                          zIndex: 3,
                        }}
                      />
                    </Popover>
                  )}
                  {/* 文本层（透明，置于最上层以支持选中） */}
                  <div
                    className="pf-pdf-text-layer"
                    style={{
                      position: "absolute",
                      left: 0,
                      top: 0,
                      overflow: "hidden",
                      opacity: 1,
                      zIndex: 1,
                      // pdfjs TextLayer 需要文字透明但仍可选中
                      color: "transparent",
                    }}
                  />
                </div>
              );
            })}
            {/* 懒加载更多页 */}
            {renderedPages < totalPages && (
              <div style={{ textAlign: "center", padding: 16 }}>
                <Button onClick={loadMorePages} type="dashed">
                  {t("pdf.loadMore", { count: totalPages - renderedPages })}
                </Button>
              </div>
            )}
          </>
        )}
      </div>

      {/* 右侧：高亮/翻译侧边栏 */}
      {sidebarOpen && (
        <div
          className="pf-pdf-sidebar"
          style={{
            width: 320,
            flexShrink: 0,
            background: "var(--pf-bg-primary)",
            borderRadius: 8,
            padding: 12,
            maxHeight: 720,
            overflow: "auto",
            boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
          }}
        >
          <Tabs
            activeKey={sidebarTab}
            onChange={(k) => setSidebarTab(k as "highlights" | "translation")}
            items={[
              {
                key: "highlights",
                label: t("pdf.highlightTab", "高亮"),
                children: <></>,
              },
              {
                key: "translation",
                label: t("pdf.translationTab", "翻译"),
                children: <></>,
              },
            ]}
          />
          {sidebarTab === "highlights" && (
            <div
              style={{
                fontWeight: 600,
                fontSize: 13,
                marginBottom: 8,
                color: "var(--pf-text-primary)",
              }}
            >
              {t("pdf.highlightsLabel", { count: highlights.length })}
            </div>
          )}
          {highlights.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("pdf.highlightHint")}
              style={{ margin: "24px 0" }}
            />
          ) : (
            <List
              size="small"
              dataSource={highlights}
              renderItem={(h) => (
                <List.Item style={{ padding: "8px 4px", alignItems: "flex-start" }}>
                  <div style={{ width: "100%" }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        marginBottom: 4,
                      }}
                    >
                      <span
                        style={{
                          display: "inline-block",
                          width: 10,
                          height: 10,
                          borderRadius: 2,
                          background: h.color,
                          flexShrink: 0,
                        }}
                      />
                      <Tag style={{ marginRight: 0 }}>P{h.page}</Tag>
                      <span style={{ fontSize: 11, color: "var(--pf-text-placeholder)" }}>
                        {new Date(h.createdAt).toLocaleString("zh-CN", {
                          month: "2-digit",
                          day: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<Trash2 />}
                        onClick={() => removeHighlight(h.id)}
                        style={{ marginLeft: "auto", padding: "0 4px" }}
                      />
                    </div>
                    <div
                      onClick={() => jumpToHighlight(h)}
                      style={{
                        fontSize: 12,
                        color: "var(--pf-text-secondary)",
                        cursor: "pointer",
                        lineHeight: 1.5,
                        marginBottom: 4,
                        display: "-webkit-box",
                        WebkitLineClamp: 2,
                        WebkitBoxOrient: "vertical",
                        overflow: "hidden",
                      }}
                    >
                      {h.text}
                    </div>
                    <Input.TextArea
                      rows={1}
                      size="small"
                      placeholder={t("pdf.annotationEditor")}
                      value={h.note}
                      onChange={(e) => updateNote(h.id, e.target.value)}
                      style={{ fontSize: 11 }}
                    />
                  </div>
                </List.Item>
              )}
            />
          )}
          {sidebarTab === "translation" && (
            <TranslationPanel
              original={translationOriginal}
              targetLanguage={targetLanguage}
              onTargetLanguageChange={setTargetLanguage}
              translating={translating}
              translationResult={translationResult}
              onTranslate={() => handleTranslate(translationOriginal)}
              onCopy={() => {
                if (!translationResult) return;
                navigator.clipboard
                  .writeText(translationResult)
                  .then(() => message.success(t("common.copied")))
                  .catch(() => message.error(t("common.copyFailed")));
              }}
              history={translationHistory}
              onSelectHistory={handleSelectHistory}
              onDeleteHistory={handleDeleteHistory}
            />
          )}
        </div>
      )}

      {/* pdfjs TextLayer 样式（透明文字、可选中） */}
      <style>{`
        .pf-pdf-text-layer span {
          color: transparent;
          -webkit-user-select: text;
          user-select: text;
        }
        .pf-pdf-text-layer ::selection {
          background: rgba(96, 165, 250, 0.35);
        }
      `}</style>
    </div>
  );
}
