import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Upload } from "lucide-react";
import { Button } from "antd";
import PageHeader from "@/components/PageHeader";
import ReflectionList from "@/components/ReflectionList";
import ReflectionUpload from "@/components/ReflectionUpload";

/**
 * 感悟报告分析（独立入口 /reflection）。
 *
 * 与论文深度审稿（/depth-v4）物理隔离：本页只承载 reflection 报告评审记录，
 * 含 6 维雷达评分、有据性/覆盖度、重评 / 删除 / 诚信报告 / 全班批量导出。
 * 顶部「提交感悟」入口把「上传 → 评审 → 列表追踪」闭环收拢在本页，形成完整工作流。
 */
export default function ReflectionReportsPage() {
  const { t } = useTranslation();
  const [uploadOpen, setUploadOpen] = useState(false);
  // 上传成功后将列表 remount 以拉取最新评审记录（含刚提交仍在 running 的报告，
  // 列表自身会每 5 秒轮询活跃行）
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div style={{ maxWidth: 1200, margin: "0 auto", padding: "24px 16px" }}>
      <PageHeader
        title={t("reflection.page.title", "感悟报告分析")}
        description={t(
          "reflection.page.subtitle",
          "对每份感悟实验报告做 6 维评审（理解/分析/创新/证据/有据性/覆盖度），定位证据缺口与编造风险。",
        )}
        actions={
          <Button
            type="primary"
            icon={<Upload size={16} />}
            onClick={() => setUploadOpen(true)}
          >
            {t("reflection.upload.quickAction", "提交感悟")}
          </Button>
        }
      />
      <ReflectionList key={refreshKey} />
      <ReflectionUpload
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onSuccess={() => setRefreshKey((k) => k + 1)}
      />
    </div>
  );
}
