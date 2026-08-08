import { Import, Link, Merge } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Card, Input, List, Progress, Space, Tag, Typography, App } from "antd";

import { importFromZotero } from "@/api/zotero";
import type { ZoteroImportResult } from "@/api/types";
import { useNavigate } from "react-router-dom";
import VramEventPanel from "@/components/VramEventPanel";
import ResearchPageAccent from "@/components/ResearchPageAccent";
import PageHeader from "@/components/PageHeader";
import DepthSettingsPanel from "@/components/DepthSettingsPanel";

const { Text, Link: AntLink } = Typography;

/**
 * 设置页 —— B3：Zotero 文献库导入入口。
 *
 * 支持从 Zotero 用户库批量导入已有文献到 PaperForge：
 * - 公开库：仅需 userID
 * - 私有库：需 userID + API Key
 * - 单次最多导入 100 篇
 * - 失败条目记录在结果列表中，不阻塞整体导入
 */
export default function SettingsPage() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [userId, setUserId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);
  const [results, setResults] = useState<ZoteroImportResult[]>([]);
  const [summary, setSummary] = useState<{
    success: number;
    fail: number;
    totalFetched: number;
  } | null>(null);

  const handleImport = async () => {
    if (loading) return;
    const trimmedId = userId.trim();
    if (!trimmedId) {
      message.warning(t("settings.userIdRequired"));
      return;
    }

    setLoading(true);
    setProgress(null);
    setResults([]);
    setSummary(null);

    try {
      const resp = await importFromZotero(trimmedId, apiKey.trim());
      setProgress({ done: resp.results.length, total: resp.totalFetched });
      setResults(resp.results);
      setSummary({
        success: resp.successCount,
        fail: resp.failCount,
        totalFetched: resp.totalFetched,
      });
      if (resp.successCount > 0) {
        message.success(t("settings.importSuccess", { count: resp.successCount }));
      } else if (resp.totalFetched === 0) {
        message.info(t("settings.importEmpty"));
      } else {
        message.warning(t("settings.importPartialFail", { count: resp.failCount }));
      }
    } catch {
      // client.ts 拦截器已提示错误
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <PageHeader title={t("settings.title")} />
      <ResearchPageAccent
        icon={<Import />}
        label={t("settings.title")}
        detail={t("settings.zoteroImport")}
        tone="blue"
        index="WORKSPACE SETTINGS 09"
      />

      <Card
        className="pf-glass-card"
        style={{ marginBottom: 24 }}
        title={
          <Space>
            <Merge />
            <span>{t("settings.duplicatePapers")}</span>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 20 }}
          message={t("settings.duplicatePapersHelp")}
        />
        <Button
          type="primary"
          icon={<Merge />}
          onClick={() => navigate("/duplicates")}
        >
          {t("settings.goToDuplicatePapers")}
        </Button>
      </Card>

      <Card
        className="pf-glass-card"
        title={
          <Space>
            <Import />
            <span>{t("settings.zoteroImport")}</span>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 20 }}
          message={t("settings.zoteroHelp")}
          description={
            <Text style={{ fontSize: 13 }}>
              {t("settings.zoteroHelpStep1")}{" "}
              <AntLink href="https://www.zotero.org/settings/keys" target="_blank" rel="noreferrer">
                {t("settings.zoteroHelpLink")} <Link />
              </AntLink>{" "}
              {t("settings.zoteroHelpStep2")}
              {t("settings.zoteroHelpStep3")}
            </Text>
          }
        />

        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleImport();
          }}
        >
          <div style={{ marginBottom: 16 }}>
            <label
              htmlFor="zotero-user-id"
              style={{ display: "block", marginBottom: 6, fontSize: 13 }}
            >
              {t("settings.userIdLabel")} <Text type="danger">*</Text>
            </label>
            <Input
              id="zotero-user-id"
              placeholder={t("settings.userIdPlaceholder")}
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
            />
          </div>

          <div style={{ marginBottom: 20 }}>
            <label
              htmlFor="zotero-api-key"
              style={{ display: "block", marginBottom: 6, fontSize: 13 }}
            >
              API Key{" "}
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t("settings.apiKeyOptional")}
              </Text>
            </label>
            <Input.Password
              id="zotero-api-key"
              placeholder={t("settings.apiKeyPlaceholder")}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </div>

          <Button type="primary" icon={<Import />} loading={loading} htmlType="submit">
            {t("settings.importButton")}
          </Button>
        </form>

        {progress && (
          <div style={{ marginTop: 24 }}>
            <Progress
              percent={
                progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 100
              }
              status={loading ? "active" : "normal"}
            />
            <Text type="secondary" style={{ fontSize: 13 }}>
              {t("settings.progressText", { done: progress.done, total: progress.total })}
            </Text>
          </div>
        )}

        {summary && (
          <div style={{ marginTop: 16 }}>
            <Space size={16}>
              <Tag color="green">{t("settings.tagSuccess", { count: summary.success })}</Tag>
              {summary.fail > 0 && (
                <Tag color="red">{t("settings.tagFail", { count: summary.fail })}</Tag>
              )}
              <Tag>{t("settings.tagFetched", { count: summary.totalFetched })}</Tag>
            </Space>
          </div>
        )}

        {results.length > 0 && (
          <List
            style={{ marginTop: 20 }}
            size="small"
            dataSource={results}
            renderItem={(item) => (
              <List.Item>
                <Space style={{ width: "100%" }} align="start">
                  {item.success ? (
                    <Tag color="green" style={{ marginTop: 2 }}>
                      {t("settings.resultSuccess")}
                    </Tag>
                  ) : (
                    <Tag color="red" style={{ marginTop: 2 }}>
                      {t("settings.resultFail")}
                    </Tag>
                  )}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Text style={{ fontSize: 13, fontWeight: 500 }} ellipsis>
                      {item.title}
                    </Text>
                    {item.error && (
                      <Text type="danger" style={{ fontSize: 12, display: "block" }}>
                        {item.error}
                      </Text>
                    )}
                  </div>
                </Space>
              </List.Item>
            )}
          />
        )}
      </Card>

      <DepthSettingsPanel />

      <VramEventPanel />
    </div>
  );
}
