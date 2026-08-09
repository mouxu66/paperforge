import { Copy, Download, FileText, Link, RefreshCw, Search } from "lucide-react";
import { useEffect, useMemo, useCallback, useState } from "react";
import { Alert, Button, Card, Descriptions, Input, Select, Space, Spin, Tabs, App } from "antd";

import { useTranslation } from "react-i18next";
import type { CslItem } from "@/api/types";
import {
  renderCitations,
  BUILT_IN_CSL_STYLES,
  buildRemoteCslUrl,
  convertToBibTeX,
  generateLaTeXSnippet,
  downloadJson,
  fetchCslXml,
  renderBibliography,
  copyRichText,
} from "@/utils/csl";

interface CslExportPanelProps {
  projectId: number;
  projectTitle: string;
  items: CslItem[];
  loading?: boolean;
  onRefresh?: () => void;
}

interface StyleOption {
  value: string;
  label: string;
  isRemote?: boolean;
}

const LOCAL_CSL_BASE = "/csl/";

export default function CslExportPanel({
  projectId,
  projectTitle,
  items,
  loading: loadingItems,
  onRefresh,
}: CslExportPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [selectedStyle, setSelectedStyle] = useState<string>(BUILT_IN_CSL_STYLES[0].id);
  const [customUrl, setCustomUrl] = useState<string>("");
  const [remoteName, setRemoteName] = useState<string>("");
  const [cslXml, setCslXml] = useState<string>("");
  const [loadingStyle, setLoadingStyle] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const [bibliography, setBibliography] = useState<string>("");
  const [entries, setEntries] = useState<string[]>([]);
  const [inlineCitations, setInlineCitations] = useState<string[]>([]);

  const styleOptions: StyleOption[] = useMemo(() => {
    const builtIn = BUILT_IN_CSL_STYLES.map((s) => ({ value: s.id, label: s.name }));
    return [...builtIn, { value: "custom", label: t("csl.customStyle"), isRemote: true }];
  }, [t]);

  const currentStyleName = useMemo(() => {
    const found = BUILT_IN_CSL_STYLES.find((s) => s.id === selectedStyle);
    return found?.name ?? selectedStyle;
  }, [selectedStyle]);

  const loadStyle = useCallback(
    async (styleId: string) => {
      setError("");
      if (styleId === "custom") {
        if (!customUrl.trim()) {
          setCslXml("");
          setBibliography("");
          setEntries([]);
          return;
        }
        setLoadingStyle(true);
        try {
          const xml = await fetchCslXml(customUrl.trim());
          setCslXml(xml);
        } catch (err0: unknown) {
          const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
          setError(e?.message || t("csl.loadError"));
          setCslXml("");
          setBibliography("");
          setEntries([]);
        } finally {
          setLoadingStyle(false);
        }
        return;
      }

      setLoadingStyle(true);
      try {
        const xml = await fetchCslXml(`${LOCAL_CSL_BASE}${styleId}.csl`);
        setCslXml(xml);
      } catch (err0: unknown) {
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        setError(e?.message || t("csl.loadError"));
        setCslXml("");
        setBibliography("");
        setEntries([]);
      } finally {
        setLoadingStyle(false);
      }
    },
    [t, customUrl],
  );

  useEffect(() => {
    loadStyle(selectedStyle);
  }, [selectedStyle, customUrl, loadStyle]);

  useEffect(() => {
    if (!cslXml || items.length === 0) {
      setBibliography("");
      setEntries([]);
      setInlineCitations([]);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const bibResult = await renderBibliography(items, cslXml);
        const citations = await renderCitations(
          items,
          cslXml,
          items.slice(0, 5).map((item) => ({ id: item.id })),
        );
        if (cancelled) return;
        setBibliography(bibResult.bibliography);
        setEntries(bibResult.entries);
        setInlineCitations(citations);
      } catch (err0: unknown) {
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        if (cancelled) return;
        setError(e?.message || t("csl.renderError"));
        setBibliography("");
        setEntries([]);
        setInlineCitations([]);
      }
    })();
    return () => {
      cancelled = true;
    };

  }, [cslXml, items, t]);

  const handleDownloadCslJson = () => {
    const filename = `${projectTitle || `project-${projectId}`}_references.json`;
    downloadJson(items, filename);
    message.success(t("csl.downloadCslJsonSuccess"));
  };

  const handleDownloadBibTeX = () => {
    const bibtex = convertToBibTeX(items);
    const blob = new Blob([bibtex], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${projectTitle || `project-${projectId}`}_references.bib`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    message.success(t("csl.downloadBibTeXSuccess"));
  };

  const handleCopyBibliography = async () => {
    try {
      await copyRichText(bibliography);
      message.success(t("csl.copySuccess"));
    } catch {
      message.error(t("csl.copyFailed"));
    }
  };

  const handleCopyPlainText = async () => {
    try {
      const plain = entries
        .map((entry, i) => `${i + 1}. ${entry.replace(/<[^>]+>/g, "")}`)
        .join("\n");
      await navigator.clipboard.writeText(plain);
      message.success(t("csl.copySuccess"));
    } catch {
      message.error(t("csl.copyFailed"));
    }
  };

  const handleDownloadLaTeXBib = () => {
    const bibtex = convertToBibTeX(items);
    const blob = new Blob([bibtex], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${projectTitle || `project-${projectId}`}_references.bib`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    message.success(t("csl.downloadBibTeXSuccess"));
  };

  const handleCopyLaTeXSnippet = async () => {
    try {
      const snippet = generateLaTeXSnippet(items);
      await navigator.clipboard.writeText(snippet);
      message.success(t("csl.copySuccess"));
    } catch {
      message.error(t("csl.copyFailed"));
    }
  };

  const handleLoadRemoteStyle = () => {
    const name = remoteName.trim();
    if (!name) return;
    const url = buildRemoteCslUrl(`${name}.csl`);
    setCustomUrl(url);
    setSelectedStyle("custom");
  };

  const pluginItems = [
    {
      key: "zotero",
      label: t("csl.pluginZotero"),
      children: (
        <div style={{ fontSize: 13, color: "var(--pf-text-muted)", lineHeight: 1.8 }}>
          <p>{t("csl.pluginZoteroDesc")}</p>
          <ol style={{ paddingLeft: 20 }}>
            <li>{t("csl.pluginZoteroStep1")}</li>
            <li>{t("csl.pluginZoteroStep2")}</li>
            <li>{t("csl.pluginZoteroStep3")}</li>
          </ol>
        </div>
      ),
    },
    {
      key: "word",
      label: t("csl.pluginWord"),
      children: (
        <div style={{ fontSize: 13, color: "var(--pf-text-muted)", lineHeight: 1.8 }}>
          <p>{t("csl.pluginWordDesc")}</p>
          <ol style={{ paddingLeft: 20 }}>
            <li>{t("csl.pluginWordStep1")}</li>
            <li>{t("csl.pluginWordStep2")}</li>
            <li>{t("csl.pluginWordStep3")}</li>
          </ol>
          <Button
            size="small"
            icon={<Copy />}
            onClick={handleCopyBibliography}
            disabled={!bibliography}
            style={{ marginTop: 8 }}
          >
            {t("csl.copyHtml")}
          </Button>
        </div>
      ),
    },
    {
      key: "docs",
      label: t("csl.pluginDocs"),
      children: (
        <div style={{ fontSize: 13, color: "var(--pf-text-muted)", lineHeight: 1.8 }}>
          <p>{t("csl.pluginDocsDesc")}</p>
          <ol style={{ paddingLeft: 20 }}>
            <li>{t("csl.pluginDocsStep1")}</li>
            <li>{t("csl.pluginDocsStep2")}</li>
            <li>{t("csl.pluginDocsStep3")}</li>
          </ol>
          <Button
            size="small"
            icon={<Copy />}
            onClick={handleCopyBibliography}
            disabled={!bibliography}
            style={{ marginTop: 8 }}
          >
            {t("csl.copyHtml")}
          </Button>
        </div>
      ),
    },
    {
      key: "latex",
      label: t("csl.pluginLaTeX"),
      children: (
        <div style={{ fontSize: 13, color: "var(--pf-text-muted)", lineHeight: 1.8 }}>
          <p>{t("csl.pluginLaTeXDesc")}</p>
          <ol style={{ paddingLeft: 20 }}>
            <li>{t("csl.pluginLaTeXStep1")}</li>
            <li>{t("csl.pluginLaTeXStep2")}</li>
            <li>{t("csl.pluginLaTeXStep3")}</li>
          </ol>
          <Space style={{ marginTop: 8 }}>
            <Button
              size="small"
              icon={<Download />}
              onClick={handleDownloadLaTeXBib}
              disabled={items.length === 0}
            >
              {t("csl.downloadLaTeXBib")}
            </Button>
            <Button
              size="small"
              icon={<Copy />}
              onClick={handleCopyLaTeXSnippet}
              disabled={items.length === 0}
            >
              {t("csl.copyLaTeXSnippet")}
            </Button>
          </Space>
        </div>
      ),
    },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <Space wrap>
          <Select
            value={selectedStyle}
            onChange={setSelectedStyle}
            options={styleOptions}
            style={{ minWidth: 220 }}
            disabled={loadingStyle}
          />
          {selectedStyle === "custom" && (
            <Input
              value={customUrl}
              onChange={(e) => setCustomUrl(e.target.value)}
              placeholder={t("csl.customUrlPlaceholder")}
              style={{ width: 320 }}
              prefix={<Link />}
            />
          )}
          <Button
            icon={<RefreshCw />}
            onClick={() => loadStyle(selectedStyle)}
            loading={loadingStyle}
          >
            {t("csl.reloadStyle")}
          </Button>
        </Space>
        <Space wrap>
          <Button icon={<Download />} onClick={handleDownloadCslJson}>
            {t("csl.downloadCslJson")}
          </Button>
          <Button
            icon={<Download />}
            onClick={handleDownloadBibTeX}
            disabled={items.length === 0}
          >
            {t("csl.downloadBibTeX")}
          </Button>
          <Button
            icon={<Download />}
            onClick={handleDownloadLaTeXBib}
            disabled={items.length === 0}
          >
            {t("csl.downloadLaTeXBib")}
          </Button>
          <Button
            icon={<Copy />}
            onClick={handleCopyLaTeXSnippet}
            disabled={items.length === 0}
          >
            {t("csl.copyLaTeXSnippet")}
          </Button>
          <Button icon={<Copy />} onClick={handleCopyBibliography} disabled={!bibliography}>
            {t("csl.copyHtml")}
          </Button>
          <Button
            icon={<FileText />}
            onClick={handleCopyPlainText}
            disabled={entries.length === 0}
          >
            {t("csl.copyPlain")}
          </Button>
          {onRefresh && (
            <Button icon={<RefreshCw />} onClick={onRefresh} loading={loadingItems}>
              {t("csl.refresh")}
            </Button>
          )}
        </Space>
      </div>

      <Card
        className="pf-glass-card"
        variant="borderless"
        size="small"
        title={t("csl.remoteStyleSearch")}
      >
        <Space wrap>
          <Input
            value={remoteName}
            onChange={(e) => setRemoteName(e.target.value)}
            placeholder={t("csl.remoteStylePlaceholder")}
            style={{ width: 280 }}
            prefix={<Search />}
          />
          <Button type="primary" icon={<Search />} onClick={handleLoadRemoteStyle}>
            {t("csl.loadRemoteStyle")}
          </Button>
          <a
            href="https://www.zotero.org/styles"
            target="_blank"
            rel="noreferrer"
            style={{ fontSize: 13 }}
          >
            {t("csl.browseZoteroStyles")}
          </a>
        </Space>
      </Card>

      {error && (
        <Alert title={t("csl.errorTitle")} description={error} type="error" showIcon closable />
      )}

      <Card
        className="pf-glass-card"
        variant="borderless"
        title={
          <Space>
            <FileText style={{ color: "var(--pf-primary)" }} />
            <span className="pf-serif">{t("csl.previewTitle", { style: currentStyleName })}</span>
          </Space>
        }
      >
        {loadingItems || loadingStyle ? (
          <div style={{ textAlign: "center", padding: 40 }}>
            <Spin description={t("csl.loading")} />
          </div>
        ) : items.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "var(--pf-text-placeholder)" }}>
            {t("csl.noReferences")}
          </div>
        ) : (
          <div
            className="pf-csl-bibliography"
            style={{ fontSize: 14, lineHeight: 1.8 }}

            dangerouslySetInnerHTML={{ __html: bibliography }}
          />
        )}
      </Card>

      <Card
        className="pf-glass-card"
        variant="borderless"
        title={
          <Space>
            <FileText style={{ color: "var(--pf-primary)" }} />
            <span className="pf-serif">{t("csl.inlineCitationsTitle")}</span>
          </Space>
        }
      >
        {inlineCitations.length === 0 ? (
          <div style={{ textAlign: "center", padding: 24, color: "var(--pf-text-placeholder)" }}>
            {t("csl.noInlineCitations")}
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {inlineCitations.map((citation, index) => (
              <div
                key={index}
                style={{
                  padding: "8px 12px",
                  background: "var(--pf-bg-tertiary)",
                  borderRadius: 6,
                  fontSize: 13,
                }}

                dangerouslySetInnerHTML={{ __html: citation }}
              />
            ))}
          </div>
        )}
      </Card>

      <Card
        className="pf-glass-card"
        variant="borderless"
        title={
          <Space>
            <Link style={{ color: "var(--pf-primary)" }} />
            <span className="pf-serif">{t("csl.pluginIntegrationTitle")}</span>
          </Space>
        }
      >
        <Tabs items={pluginItems} />
      </Card>

      <Descriptions size="small" bordered column={2}>
        <Descriptions.Item label={t("csl.itemCount")}>{items.length}</Descriptions.Item>
        <Descriptions.Item label={t("csl.currentStyle")}>{currentStyleName}</Descriptions.Item>
      </Descriptions>
    </div>
  );
}
