import { Database, FileText, HardDrive, Radar, BadgeCheck } from "lucide-react";
import { Card, Statistic } from "antd";

import { useTranslation } from "react-i18next";
import type { LibraryStats } from "@/api/types";
import { formatSize } from "@/utils/format";

interface Props {
  stats: LibraryStats | null;
  loading?: boolean;
  isReport?: boolean;
}

export default function StatCards({ stats, loading, isReport }: Props) {
  const { t } = useTranslation();

  if (isReport) {
    return (
      <div className="pf-stat-grid">
        <Card loading={loading} className="pf-card-hover pf-glass-card pf-stat-card" variant="borderless">
          <Statistic
            title={<span style={{ color: "var(--pf-text-muted)" }}>{t("stats.reportCount")}</span>}
            value={stats?.reportCount ?? 0}
            prefix={<FileText style={{ color: "var(--pf-primary)" }} />}
            styles={{ content: { color: "var(--pf-text-primary)", fontWeight: 600 } }}
          />
        </Card>
        <Card loading={loading} className="pf-card-hover pf-glass-card pf-stat-card" variant="borderless">
          <Statistic
            title={
              <span style={{ color: "var(--pf-text-muted)" }}>{t("stats.reportAvgScore")}</span>
            }
            value={stats?.reportAvgScore ?? 0}
            precision={2}
            prefix={<Radar style={{ color: "var(--pf-primary)" }} />}
            styles={{ content: { color: "var(--pf-text-primary)", fontWeight: 600 } }}
          />
        </Card>
        <Card loading={loading} className="pf-card-hover pf-glass-card pf-stat-card" variant="borderless">
          <Statistic
            title={
              <span style={{ color: "var(--pf-text-muted)" }}>{t("stats.reportAvgFidelity")}</span>
            }
            value={(stats?.reportAvgFidelity ?? 0) * 100}
            precision={1}
            suffix="%"
            prefix={<BadgeCheck style={{ color: "var(--pf-primary)" }} />}
            styles={{ content: { color: "var(--pf-text-primary)", fontWeight: 600 } }}
          />
        </Card>
      </div>
    );
  }

  return (
    <div className="pf-stat-grid">
      <Card loading={loading} className="pf-card-hover pf-glass-card pf-stat-card" variant="borderless">
        <Statistic
          title={<span style={{ color: "var(--pf-text-muted)" }}>{t("stats.totalPapers")}</span>}
          value={stats?.totalPapers ?? 0}
          prefix={<FileText style={{ color: "var(--pf-primary)" }} />}
          styles={{ content: { color: "var(--pf-text-primary)", fontWeight: 600 } }}
        />
      </Card>
      <Card loading={loading} className="pf-card-hover pf-glass-card pf-stat-card" variant="borderless">
        <Statistic
          title={<span style={{ color: "var(--pf-text-muted)" }}>{t("stats.textChunks")}</span>}
          value={stats?.totalChunks ?? 0}
          prefix={<Database style={{ color: "var(--pf-primary)" }} />}
          styles={{ content: { color: "var(--pf-text-primary)", fontWeight: 600 } }}
        />
      </Card>
      <Card loading={loading} className="pf-card-hover pf-glass-card pf-stat-card" variant="borderless">
        <Statistic
          title={<span style={{ color: "var(--pf-text-muted)" }}>{t("stats.indexSize")}</span>}
          value={stats ? formatSize(stats.totalSize) : "0 B"}
          prefix={<HardDrive style={{ color: "var(--pf-primary)" }} />}
          styles={{ content: { color: "var(--pf-text-primary)", fontWeight: 600 } }}
        />
      </Card>
    </div>
  );
}
