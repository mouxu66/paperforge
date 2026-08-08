import { Eye, History, Undo2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Button,
  Drawer,
  Empty,
  Modal,
  Popconfirm,
  Spin,
  Timeline,
  Typography,
  App,
} from "antd";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import dayjs from "dayjs";
import type { ChapterVersion, ChapterVersionDetail } from "@/api/types";
import { fetchChapterVersion, fetchChapterVersions, restoreChapterVersion } from "@/api/writing";

interface VersionDrawerProps {
  open: boolean;
  chapterId: number | null;
  onClose: () => void;
  /** 恢复版本后的回调，传入恢复的内容 */
  onRestore: (content: string) => void;
}

/**
 * 章节历史版本抽屉。
 *
 * 功能：
 * - 打开时拉取章节历史版本列表，以时间线展示
 * - 「预览」拉取版本完整内容并以 Markdown 只读展示
 * - 「恢复」调用后端恢复接口，成功后回调父组件并关闭抽屉
 */
export default function VersionDrawer({ open, chapterId, onClose, onRestore }: VersionDrawerProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [versions, setVersions] = useState<ChapterVersion[]>([]);
  const [loading, setLoading] = useState(false);

  // 预览弹窗状态
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewDetail, setPreviewDetail] = useState<ChapterVersionDetail | null>(null);

  // 恢复中的版本 id
  const [restoringId, setRestoringId] = useState<string | null>(null);

  // 打开时拉取版本列表
  useEffect(() => {
    if (!open || !chapterId) {
      setVersions([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetchChapterVersions(chapterId)
      .then((list) => {
        if (!cancelled) setVersions(list);
      })
      .catch(() => {
        // 错误已由 http 拦截器提示
        if (!cancelled) setVersions([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, chapterId]);

  // 关闭时重置预览状态
  useEffect(() => {
    if (!open) {
      setPreviewOpen(false);
      setPreviewDetail(null);
      setRestoringId(null);
    }
  }, [open]);

  /** 预览某个版本：拉取完整内容后弹窗展示 */
  const handlePreview = useCallback(
    async (versionId: string) => {
      if (!chapterId) return;
      setPreviewOpen(true);
      setPreviewLoading(true);
      setPreviewDetail(null);
      try {
        const detail = await fetchChapterVersion(chapterId, versionId);
        setPreviewDetail(detail);
      } catch {
        // 错误已由 http 拦截器提示
        setPreviewOpen(false);
      } finally {
        setPreviewLoading(false);
      }
    },
    [chapterId],
  );

  /** 恢复到指定版本 */
  const handleRestore = useCallback(
    async (versionId: string) => {
      if (!chapterId) return;
      setRestoringId(versionId);
      try {
        const result = await restoreChapterVersion(chapterId, versionId);
        onRestore(result.content);
        message.success(t("version.restoreDone"));
        onClose();
      } catch {
        // 错误已由 http 拦截器提示
      } finally {
        setRestoringId(null);
      }
    },
    [chapterId, onRestore, onClose, t, message],
  );

  return (
    <>
      <Drawer
        title={
          <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
            <History style={{ marginRight: 8, color: "var(--pf-primary)" }} />
            {t("version.title")}
          </span>
        }
        open={open}
        onClose={onClose}
        width={480}
        destroyOnHidden
      >
        {loading ? (
          <div style={{ textAlign: "center", padding: "48px 0" }}>
            <Spin />
          </div>
        ) : versions.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <div>
                <div style={{ color: "var(--pf-text-primary)" }}>{t("version.empty")}</div>
                <Typography.Text style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }}>
                  {t("version.emptyHint")}
                </Typography.Text>
              </div>
            }
          />
        ) : (
          <Timeline
            items={versions.map((v) => ({
              color: "var(--pf-primary)",
              children: (
                <div
                  style={{
                    paddingBottom: 8,
                    borderBottom: "1px solid var(--pf-border-light)",
                  }}
                >
                  <Typography.Text
                    className="pf-serif"
                    style={{ fontSize: 14, fontWeight: 600, color: "var(--pf-text-primary)" }}
                  >
                    {dayjs(v.createdAt).format("YYYY-MM-DD HH:mm")}
                  </Typography.Text>
                  <div style={{ marginTop: 4, marginBottom: 8 }}>
                    <Typography.Text style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
                      {t("version.wordCount", { count: v.wordCount.toLocaleString() })}
                    </Typography.Text>
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <Button size="small" icon={<Eye />} onClick={() => handlePreview(v.id)}>
                      {t("version.preview")}
                    </Button>
                    <Popconfirm
                      title={t("version.restoreTitle")}
                      description={t("version.restoreDesc")}
                      okText={t("version.restoreOk")}
                      cancelText={t("version.restoreCancel")}
                      onConfirm={() => handleRestore(v.id)}
                    >
                      <Button
                        size="small"
                        icon={<Undo2 />}
                        loading={restoringId === v.id}
                      >
                        {t("version.restoreOk")}
                      </Button>
                    </Popconfirm>
                  </div>
                </div>
              ),
            }))}
          />
        )}
      </Drawer>

      {/* 版本预览弹窗 */}
      <Modal
        title={t("version.previewTitle")}
        open={previewOpen}
        onCancel={() => setPreviewOpen(false)}
        footer={null}
        width={720}
        destroyOnHidden
      >
        {previewLoading ? (
          <div style={{ textAlign: "center", padding: "48px 0" }}>
            <Spin />
          </div>
        ) : previewDetail ? (
          <div>
            <div style={{ marginBottom: 12 }}>
              <Typography.Text style={{ fontSize: 12, color: "var(--pf-text-muted)" }}>
                {dayjs(previewDetail.createdAt).format("YYYY-MM-DD HH:mm")} ·{" "}
                {t("version.wordCount", { count: previewDetail.wordCount.toLocaleString() })}
              </Typography.Text>
            </div>
            <div
              className="pf-markdown"
              style={{
                padding: 16,
                background: "var(--pf-bg-tertiary)",
                borderRadius: 8,
                border: "1px solid var(--pf-border)",
                maxHeight: "60vh",
                overflow: "auto",
              }}
            >
              {previewDetail.content.trim() ? (
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{previewDetail.content}</ReactMarkdown>
              ) : (
                <div style={{ color: "var(--pf-text-placeholder)", fontSize: 13 }}>
                  {t("version.noContent")}
                </div>
              )}
            </div>
          </div>
        ) : null}
      </Modal>
    </>
  );
}
