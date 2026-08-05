// 排序选项
export const SORT_OPTIONS = [
  { label: "最新发表", value: "year_desc" },
  { label: "最早发表", value: "year_asc" },
  { label: "引用最多", value: "citations_desc" },
  { label: "文本块最多", value: "chunks_desc" },
] as const;

export const PAGE_SIZE = 20;

// WP-2.4: 大库性能 UX — 分页选项（桌面端默认 20，支持 50/100 快速浏览）
export const PAGE_SIZE_OPTIONS = [20, 50, 100] as const;

// 论文来源 → antd Tag color（PaperCard / DetailPage 共用）
export const SOURCE_COLOR: Record<string, string> = {
  arxiv: "blue",
  pubmed: "green",
  ieee: "orange",
  springer: "purple",
  upload: "cyan",
  cnki: "volcano",
  google_scholar: "purple",
  web_clipper: "cyan",
};

// 分类 → 学科色彩（PaperCard 圆点指示器 / SideBar 侧边栏指示器共用）
export const CATEGORY_COLOR: Record<string, string> = {
  all: "#94a3b8",
  llm: "#1e40af",
  lora: "#3b82f6",
  rl: "#8b5cf6",
  cv: "#ec4899",
  comm: "#10b981",
  quant: "#f59e0b",
};
