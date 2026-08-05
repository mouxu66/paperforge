import { UploadCloud, Globe, FolderOpen, Cloud, Inbox, BookMarked, GraduationCap } from "lucide-react";
import { useMemo } from "react";
import type { ReactNode } from "react";
import { Segmented } from "antd";

import type { LibraryStats, PaperSource } from "@/api/types";

interface Props {
  stats: LibraryStats | null;
  currentSource: PaperSource;
  onSourceChange: (source: PaperSource) => void;
}

// 来源选项配置
const SOURCE_OPTIONS: { value: PaperSource; label: string; icon: ReactNode }[] = [
  { value: "all", label: "全部", icon: <Inbox /> },
  { value: "upload", label: "本地上传", icon: <UploadCloud /> },
  { value: "arxiv", label: "arXiv", icon: <Globe /> },
  { value: "cnki", label: "知网", icon: <BookMarked /> },
  { value: "google_scholar", label: "Scholar", icon: <GraduationCap /> },
  { value: "web_clipper", label: "网页剪藏", icon: <Globe /> },
  { value: "zotero", label: "Zotero", icon: <FolderOpen /> },
  { value: "pubmed", label: "PubMed", icon: <Cloud /> },
  { value: "ieee", label: "IEEE", icon: <Cloud /> },
];

export default function SourceFilter({ stats, currentSource, onSourceChange }: Props) {
  // 从 stats 中提取各来源数量
  const sourceCounts = useMemo(() => {
    const counts: Record<string, number> = { all: stats?.totalPapers ?? 0 };
    if (stats?.bySource) {
      for (const item of stats.bySource) {
        counts[item.source] = item.count;
      }
    }
    return counts;
  }, [stats]);

  return (
    <div
      style={{
        padding: "8px 0",
        display: "flex",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
      }}
    >
      {/* 使用 Segmented 组件实现来源筛选 */}
      <Segmented
        value={currentSource}
        onChange={(v) => onSourceChange(v as PaperSource)}
        options={SOURCE_OPTIONS.map((opt) => ({
          value: opt.value,
          label: (
            <span style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}>
              {opt.icon}
              {opt.label}
              {stats && (
                <span
                  style={{
                    fontSize: 11,
                    color:
                      currentSource === opt.value
                        ? "var(--pf-text-primary)"
                        : "var(--pf-text-placeholder)",
                    fontWeight: 500,
                  }}
                >
                  {sourceCounts[opt.value] ?? 0}
                </span>
              )}
            </span>
          ),
        }))}
      />
    </div>
  );
}
