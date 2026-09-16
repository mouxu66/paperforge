import { Database, FileText, HardDrive, Radar, BadgeCheck } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { LibraryStats } from "@/api/types";
import { formatSize } from "@/utils/format";

interface Props {
  stats: LibraryStats | null;
  loading?: boolean;
  isReport?: boolean;
}

interface StripItem {
  key: string;
  icon: ReactNode;
  value: string;
  label: string;
}

/**
 * 研究库索引 —— 一行式条带。
 *
 * 此前这里是 3 张 min-height 116px 的独立卡片（3px 顶边 + 染色底 + 大号数字），
 * 连同标题行在首屏吃掉约 180px。但"库有多大"只是环境信息，不是首屏主动作。
 * 改为一行的紧凑条带后，首屏的视觉重心交还给搜索、上传与研究工作台，
 * 库规模仍在一眼可及的位置。
 */
export default function StatCards({ stats, loading, isReport }: Props) {
  const { t } = useTranslation();

  const items: StripItem[] = isReport
    ? [
        {
          key: "reportCount",
          icon: <FileText size={14} />,
          value: String(stats?.reportCount ?? 0),
          label: t("stats.reportCount"),
        },
        {
          key: "reportAvgScore",
          icon: <Radar size={14} />,
          value: (stats?.reportAvgScore ?? 0).toFixed(2),
          label: t("stats.reportAvgScore"),
        },
        {
          key: "reportAvgFidelity",
          icon: <BadgeCheck size={14} />,
          value: `${((stats?.reportAvgFidelity ?? 0) * 100).toFixed(1)}%`,
          label: t("stats.reportAvgFidelity"),
        },
      ]
    : [
        {
          key: "totalPapers",
          icon: <FileText size={14} />,
          value: (stats?.totalPapers ?? 0).toLocaleString(),
          label: t("stats.totalPapers"),
        },
        {
          key: "textChunks",
          icon: <Database size={14} />,
          value: (stats?.totalChunks ?? 0).toLocaleString(),
          label: t("stats.textChunks"),
        },
        {
          key: "indexSize",
          icon: <HardDrive size={14} />,
          value: stats ? formatSize(stats.totalSize) : "0 B",
          label: t("stats.indexSize"),
        },
      ];

  return (
    <div className="pf-stat-strip">
      {items.map((item) => (
        <div className="pf-stat-strip-item" key={item.key}>
          <span className="pf-stat-strip-icon" aria-hidden="true">
            {item.icon}
          </span>
          <span className="pf-stat-strip-value" data-loading={loading ? "true" : undefined}>
            {loading ? "—" : item.value}
          </span>
          <span className="pf-stat-strip-label">{item.label}</span>
        </div>
      ))}
    </div>
  );
}
