import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";
import ReflectionList from "@/components/ReflectionList";

/**
 * 感悟报告分析（独立入口 /reflection）。
 *
 * 与论文深度审稿（/depth-v4）物理隔离：本页只承载 reflection 报告评审记录，
 * 含 6 维雷达评分、有据性/覆盖度、重评 / 删除 / 诚信报告 / 全班批量导出。
 */
export default function ReflectionReportsPage() {
  const { t } = useTranslation();
  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 16px" }}>
      <PageHeader
        title={t("reflection.page.title", "感悟报告分析")}
        description={t(
          "reflection.page.subtitle",
          "对每份感悟实验报告做 6 维评审（理解/分析/创新/证据/有据性/覆盖度），定位证据缺口与编造风险。",
        )}
      />
      <ReflectionList />
    </div>
  );
}
