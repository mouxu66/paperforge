import { Button } from "antd";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { CheckSquare, Microscope, Tags, Upload } from "lucide-react";

interface HomeToolbarProps {
  selectMode: boolean;
  onToggleSelectMode: () => void;
  onReflectionUpload: () => void;
  onTagManager: () => void;
  /** 当前是否为感悟报告视图，用于调整文案 */
  isReportView?: boolean;
}

export default function HomeToolbar({
  selectMode,
  onToggleSelectMode,
  onReflectionUpload,
  onTagManager,
  isReportView,
}: HomeToolbarProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <div
      style={{
        marginBottom: 20,
        padding: "12px 16px",
        background: "var(--pf-bg-tertiary)",
        borderRadius: 10,
        border: "1px solid var(--pf-border-light)",
        display: "flex",
        alignItems: "center",
        gap: 12,
        flexWrap: "wrap",
      }}
    >
      <Button
        size="small"
        icon={<Microscope size={16} style={{ color: "var(--pf-primary)" }} />}
        onClick={() => navigate("/depth")}
      >
        {t("nav.depth")}
      </Button>
      <Button
        size="small"
        icon={<Upload size={16} style={{ color: "var(--pf-report-accent)" }} />}
        onClick={onReflectionUpload}
      >
        {t("reflection.upload.quickAction", "提交感悟")}
      </Button>
      <div style={{ flex: 1 }} />
      <Button
        size="small"
        icon={<Tags size={16} style={{ color: "var(--pf-success)" }} />}
        onClick={onTagManager}
      >
        {t("tag.manager.button", "标签管理")}
      </Button>
      <Button
        type={selectMode ? "primary" : "default"}
        size="small"
        icon={<CheckSquare size={16} />}
        onClick={onToggleSelectMode}
      >
        {selectMode
          ? t("home.toolbar.exitSelection", "退出选择")
          : isReportView
            ? t("home.toolbar.selectReports", "选择报告")
            : t("home.toolbar.selectPapers", "选择论文")}
      </Button>
    </div>
  );
}
