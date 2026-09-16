import { Inbox, CheckCircle, XCircle, Loader, Clock, FileArchive, AlertCircle } from "lucide-react";
import { useState, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { Upload, Progress, Tag, Tooltip, Button, App } from "antd";
import type { UploadFile } from "antd";

import http from "@/api/client";
import type { AxiosProgressEvent } from "axios";
import { invalidatePaperQueryCache, usePaperStore } from "@/store/usePaperStore";
import type { ZipUploadResponse, ZipFailedFile } from "@/api/types";
import OcrBadge from "./OcrBadge";

const { Dragger } = Upload;

/** 单文件上传状态 */
type FileStatus = "pending" | "uploading" | "success" | "failed";

interface UploadItem {
  uid: string;
  name: string;
  status: FileStatus;
  percent: number;
  title?: string;
  error?: string;
  ocrStatus?: "pending" | "done" | "failed" | null;
  isScanned?: boolean | null;
}

/** ZIP 批量处理结果（覆写显示） */
interface ZipResult {
  total: number;
  succeeded: number;
  failed: number;
  failedFiles: ZipFailedFile[];
  /** 是否正在处理中 */
  processing: boolean;
}

const STATUS_ICON: Record<FileStatus, ReactElement> = {
  pending: <Clock style={{ color: "var(--pf-text-placeholder)" }} />,
  uploading: <Loader style={{ color: "var(--pf-primary)" }} />,
  success: <CheckCircle style={{ color: "var(--pf-success)" }} />,
  failed: <XCircle style={{ color: "var(--pf-error)" }} />,
};

const genUid = () => `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

/**
 * 文件拖拽上传组件（PDF 单文件/批量 + ZIP 压缩包）。
 */
export default function UploadPaper() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [items, setItems] = useState<UploadItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [zipResult, setZipResult] = useState<ZipResult | null>(null);
  const loadPapers = usePaperStore((s) => s.loadPapers);

  const STATUS_TEXT: Record<FileStatus, string> = {
    pending: t("upload.statusWaiting"),
    uploading: t("upload.statusUploading"),
    success: t("upload.statusImported"),
    failed: t("upload.statusFailed"),
  };

  const updateItem = (uid: string, patch: Partial<UploadItem>) => {
    setItems((prev) => prev.map((it) => (it.uid === uid ? { ...it, ...patch } : it)));
  };

  /** 单文件 PDF 上传 */
  const uploadOne = async (file: File, uid: string): Promise<boolean> => {
    const formData = new FormData();
    formData.append("file", file);
    updateItem(uid, { status: "uploading", percent: 10 });
    try {
      const { data } = await http.post("/upload-paper", formData, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 120000,
        onUploadProgress: (e: AxiosProgressEvent) => {
          if (e.total) {
            const percent = Math.round((e.loaded / e.total) * 100);
            updateItem(uid, { percent: Math.min(percent, 90) });
          }
        },
      });
      if (data?.success) {
        updateItem(uid, {
          status: "success",
          percent: 100,
          title: data.title || file.name,
          ocrStatus: data.ocr_status,
          isScanned: data.is_scanned,
        });
        return true;
      }
      updateItem(uid, {
        status: "failed",
        percent: 100,
        error: data?.error || t("upload.parseFailed"),
      });
      return false;
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      const detail = e?.response?.data?.detail || e?.message || t("upload.uploadFailed");
      updateItem(uid, { status: "failed", percent: 100, error: detail });
      return false;
    }
  };

  /** ZIP 压缩包上传 */
  const uploadZip = async (file: File) => {
    setZipResult({ total: 0, succeeded: 0, failed: 0, failedFiles: [], processing: true });
    const formData = new FormData();
    formData.append("file", file);
    try {
      const { data } = await http.post<ZipUploadResponse>("/upload-zip", formData, {
        headers: { "Content-Type": "multipart/form-data" },
        timeout: 180000,
      });
      setZipResult({
        total: data.total,
        succeeded: data.succeeded,
        failed: data.failed,
        failedFiles: data.failed_files || [],
        processing: false,
      });
      if (data.succeeded > 0) {
        message.success(t("upload.zipSuccess", { count: data.succeeded }));
        invalidatePaperQueryCache();
        await loadPapers();
      }
      if (data.failed > 0) {
        message.warning(t("upload.zipPartialFail", { count: data.failed }));
      }
      if (data.total === 0) {
        message.info(t("upload.zipNoPdf"));
      }
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      const detail = e?.response?.data?.detail || e?.message || t("upload.uploadFailed");
      setZipResult({
        total: 0,
        succeeded: 0,
        failed: 1,
        failedFiles: [{ filename: file.name, error: detail }],
        processing: false,
      });
      message.error(detail);
    }
  };

  const handleUpload = async (files: File[]) => {
    if (!files.length) return;
    setUploading(true);
    setZipResult(null);

    // 分离 ZIP 和文档文件（PDF/DOCX）
    const zipFiles = files.filter((f) => f.name.toLowerCase().endsWith(".zip"));
    const docFiles = files.filter((f) => /\.(pdf|docx)$/i.test(f.name));

    // ZIP 文件处理（取第一个 ZIP）
    if (zipFiles.length > 0) {
      await uploadZip(zipFiles[0]);
      if (zipFiles.length > 1) {
        message.info(t("upload.zipMultipleHint", { count: zipFiles.length }));
      }
    }

    // 文档逐个上传（PDF / DOCX）
    if (docFiles.length > 0) {
      const newItems: UploadItem[] = docFiles.map((f) => ({
        uid: genUid(),
        name: f.name,
        status: "pending" as FileStatus,
        percent: 0,
      }));
      setItems((prev) => [...prev, ...newItems]);

      let successCount = 0;
      let failCount = 0;
      for (let i = 0; i < docFiles.length; i++) {
        const ok = await uploadOne(docFiles[i], newItems[i].uid);
        if (ok) successCount++;
        else failCount++;
      }
      if (successCount > 0) {
        message.success(t("upload.importSuccess", { count: successCount }));
        invalidatePaperQueryCache();
        await loadPapers();
      }
      if (failCount > 0) {
        message.error(t("upload.importFail", { count: failCount }));
      }
    }

    setUploading(false);
  };

  const clearList = () => {
    if (!uploading) setItems([]);
  };

  const clearZipResult = () => {
    setZipResult(null);
  };

  const draggerProps = {
    name: "files",
    multiple: true,
    accept: ".pdf,.docx,.zip",
    fileList: [] as UploadFile[],
    beforeUpload: (_file: File, fileList: File[]) => {
      handleUpload(fileList);
      return false;
    },
    onDrop(e: React.DragEvent) {
      const files = Array.from(e.dataTransfer.files).filter((f) =>
        /\.(pdf|docx|zip)$/i.test(f.name),
      );
      if (files.length) handleUpload(files);
    },
  };

  const totalCount = items.length;
  const doneCount = items.filter((it) => it.status === "success" || it.status === "failed").length;
  const overallPercent = totalCount === 0 ? 0 : Math.round((doneCount / totalCount) * 100);
  const hasFailed = items.some((it) => it.status === "failed");

  return (
    <div id="upload-paper-anchor" data-tour="upload" style={{ marginBottom: 16 }}>
      <Dragger {...draggerProps} style={{ padding: "12px 8px" }}>
        <p className="ant-upload-drag-icon">
          <Inbox style={{ color: "var(--pf-primary)", fontSize: 36 }} />
        </p>
        <p className="ant-upload-text" style={{ fontSize: 14, color: "var(--pf-text-primary)" }}>
          {t("upload.dragHint")}
        </p>
        <p
          className="ant-upload-hint"
          style={{ color: "var(--pf-text-placeholder)", fontSize: 12 }}
        >
          {t("upload.dragDescZip")}
        </p>
      </Dragger>

      {/* ZIP 批量处理结果 */}
      {zipResult && (
        <div
          style={{
            marginTop: 10,
            padding: "12px 16px",
            background: zipResult.processing
              ? "var(--pf-primary-soft)"
              : zipResult.failed > 0
                ? "#fef2f2"
                : "#f0fdf4",
            borderRadius: 10,
            border: `1px solid ${
              zipResult.processing ? "#bfdbfe" : zipResult.failed > 0 ? "#fecaca" : "#bbf7d0"
            }`,
          }}
        >
          {/* 标题行 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 8,
            }}
          >
            <span style={{ fontSize: 14, fontWeight: 600 }}>
              <FileArchive style={{ marginRight: 8, color: "var(--pf-primary)" }} />
              {t("upload.zipResult")}
            </span>
            {!zipResult.processing && (
              <Button type="text" size="small" onClick={clearZipResult}>
                {t("common.close")}
              </Button>
            )}
          </div>

          {/* 进度 */}
          {zipResult.processing ? (
            <div style={{ textAlign: "center", padding: "8px 0" }}>
              <Loader style={{ fontSize: 20, color: "var(--pf-primary)" }} />
              <span style={{ marginLeft: 8, color: "var(--pf-text-secondary)", fontSize: 13 }}>
                {t("upload.zipProcessing")}
              </span>
            </div>
          ) : (
            <>
              <div
                style={{
                  display: "flex",
                  gap: 24,
                  marginBottom: zipResult.failedFiles.length > 0 ? 8 : 0,
                }}
              >
                <div>
                  <Tag color="default" style={{ fontSize: 13, padding: "4px 12px" }}>
                    {t("upload.zipTotal")}: {zipResult.total}
                  </Tag>
                </div>
                <div>
                  <Tag
                    color="green"
                    icon={<CheckCircle />}
                    style={{ fontSize: 13, padding: "4px 12px" }}
                  >
                    {t("upload.zipSucceeded")}: {zipResult.succeeded}
                  </Tag>
                </div>
                {zipResult.failed > 0 && (
                  <div>
                    <Tag
                      color="red"
                      icon={<XCircle />}
                      style={{ fontSize: 13, padding: "4px 12px" }}
                    >
                      {t("upload.zipFailed")}: {zipResult.failed}
                    </Tag>
                  </div>
                )}
              </div>

              {/* 失败文件列表（可折叠） */}
              {zipResult.failedFiles.length > 0 && (
                <details style={{ marginTop: 8 }}>
                  <summary
                    style={{
                      cursor: "pointer",
                      color: "var(--pf-error)",
                      fontSize: 12,
                      fontWeight: 500,
                    }}
                  >
                    <AlertCircle style={{ marginRight: 4 }} />
                    {t("upload.zipFailedDetail")} ({zipResult.failedFiles.length})
                  </summary>
                  <div style={{ marginTop: 6, paddingLeft: 4 }}>
                    {zipResult.failedFiles.map((f, i) => (
                      <div
                        key={i}
                        style={{ fontSize: 12, color: "var(--pf-text-muted)", lineHeight: 1.8 }}
                      >
                        <Tooltip title={f.error}>
                          <span>
                            <XCircle
                              style={{ color: "var(--pf-error)", marginRight: 4, fontSize: 11 }}
                            />
                            {f.filename}
                          </span>
                        </Tooltip>
                        <span style={{ marginLeft: 8, color: "var(--pf-text-placeholder)" }}>
                          — {f.error.slice(0, 60)}
                          {f.error.length > 60 ? "…" : ""}
                        </span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      )}

      {/* PDF 批量上传进度 */}
      {totalCount > 0 && (
        <>
          <div
            style={{
              marginTop: 10,
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span style={{ fontSize: 13, color: "var(--pf-text-muted)" }}>
              {t("upload.totalProgress", { done: doneCount, total: totalCount })}
              {uploading && t("upload.uploading")}
            </span>
            {!uploading && (
              <a
                onClick={clearList}
                style={{ fontSize: 12, color: "var(--pf-text-muted)" }}
                className="pf-link"
              >
                {t("upload.clearList")}
              </a>
            )}
          </div>
          <Progress
            percent={overallPercent}
            status={uploading ? "active" : hasFailed ? "exception" : "success"}
            style={{ marginTop: 4 }}
          />

          <div style={{ marginTop: 8 }}>
            {items.map((it) => (
              <div key={it.uid} className={`pf-upload-item pf-upload-${it.status}`}>
                {STATUS_ICON[it.status]}
                <Tooltip title={it.name} mouseEnterDelay={0.5}>
                  <span className="pf-upload-name">
                    {it.status === "success" && it.title ? it.title : it.name}
                  </span>
                </Tooltip>
                {it.status === "uploading" && (
                  <span className="pf-upload-status">{it.percent}%</span>
                )}
                {it.status === "failed" && it.error && (
                  <Tooltip title={it.error}>
                    <Tag color="red" style={{ marginInlineStart: 0 }}>
                      {STATUS_TEXT[it.status]}
                    </Tag>
                  </Tooltip>
                )}
                {it.status !== "uploading" && it.status !== "failed" && (
                  <span className="pf-upload-status">{STATUS_TEXT[it.status]}</span>
                )}
                {it.status === "success" && (it.isScanned || it.ocrStatus) && (
                  <OcrBadge ocrStatus={it.ocrStatus} isScanned={it.isScanned} showLabel />
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
