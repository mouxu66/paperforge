import { DownloadCloud } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Tooltip, App } from "antd";

import http from "@/api/client";

/**
 * 开发者工具栏。
 *
 * 仅在开发模式（import.meta.env.DEV）下渲染。
 * 包含“导出教师版”按钮，点击后触发后端 PyInstaller 打包，
 * 成功后自动下载 .exe 文件。
 */
export default function DevTools() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [packaging, setPackaging] = useState(false);

  if (!import.meta.env.DEV) return null;

  const handlePackage = async () => {
    setPackaging(true);
    try {
      const { data } = await http.post(
        "/admin/package",
        {},
        { timeout: 360000 }, // 打包可能需要 1-2 分钟
      );
      if (data.success && data.downloadUrl) {
        message.success(t("devtools.packDone"));
        // 触发下载
        window.open(data.downloadUrl, "_blank");
      } else {
        message.error(data.message || t("devtools.packFailed"));
      }
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      const detail = e?.response?.data?.detail || e?.message || "打包失败";
      message.error(t("devtools.dockerCheck", { detail }));
    } finally {
      setPackaging(false);
    }
  };

  return (
    <span className="pf-nav-devtools">
      <Tooltip title={t("devtools.packTooltip")}>
        <Button
        type="text"
        icon={<DownloadCloud style={{ color: "var(--pf-text-muted)" }} />}
        loading={packaging}
        onClick={handlePackage}
        style={{ fontSize: 12 }}
      >
        {packaging ? t("devtools.packing") : t("devtools.packButton")}
        </Button>
      </Tooltip>
    </span>
  );
}
