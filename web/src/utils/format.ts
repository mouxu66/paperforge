// 格式化工具函数

/** 字节数 → 人类可读 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 作者列表截断显示 */
export function formatAuthors(authors: string[], max = 3): string {
  if (!authors || authors.length === 0) return "佚名";
  const head = authors.slice(0, max).join(", ");
  return authors.length > max ? `${head} et al.` : head;
}

/** arXiv ID → 链接 */
export function arxivUrl(paperId: string): string {
  return `https://arxiv.org/abs/${paperId}`;
}
