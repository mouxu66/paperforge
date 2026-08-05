import { BookOpen, FileText, Link as LinkIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Empty, Spin, Switch, Tag, Typography } from "antd";

import { Link } from "react-router-dom";
import type { Paper } from "@/api/types";
import { fetchPaperById } from "@/api/papers";

interface ReferenceSidebarProps {
  /** 当前章节内容（Markdown），用于解析 [@paper_id] 引用 */
  content: string;
  /** 被标记为「不在参考文献中显示」的论文 ID 集合 */
  hiddenIds: Set<string>;
  /** 切换某篇论文的显示/隐藏状态 */
  onToggleHidden: (paperId: string) => void;
}

/** 引用解析正则：匹配 [@paper_id] 格式 */
const CITE_PATTERN = /\[@([^\]\s]+)\]/g;

/**
 * 引用列表侧边栏。
 *
 * - 自动解析当前章节内容中的所有 [@paper_id] 引用，去重后生成列表
 * - 每个列表项显示论文标题（可点击跳转详情页）、作者、年份
 * - 列表项右侧 Switch 标记「是否在参考文献中显示」（默认开启）
 * - 侧边栏底部显示引用总数
 * - 使用 useMemo 缓存解析结果，避免每次渲染都重新解析
 */
export default function ReferenceSidebar({
  content,
  hiddenIds,
  onToggleHidden,
}: ReferenceSidebarProps) {
  const { t } = useTranslation();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [loading, setLoading] = useState(false);

  /** 解析内容中的所有引用 ID（去重） */
  const citationIds = useMemo(() => {
    const ids: string[] = [];
    let match: RegExpExecArray | null;
    CITE_PATTERN.lastIndex = 0;
    while ((match = CITE_PATTERN.exec(content)) !== null) {
      ids.push(match[1]);
    }
    // 去重，保持首次出现顺序
    const seen = new Set<string>();
    return ids.filter((id) => {
      if (seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }, [content]);

  /** 批量加载论文详情（复用模块级缓存避免重复请求） */
  useEffect(() => {
    if (citationIds.length === 0) {
      setPapers([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    const loadAll = async () => {
      const results: Paper[] = [];
      for (const id of citationIds) {
        try {
          const p = await fetchPaperById(id);
          if (p && !cancelled) results.push(p);
        } catch {
          // 论文不存在或请求失败，跳过
        }
      }
      if (!cancelled) {
        setPapers(results);
        setLoading(false);
      }
    };
    void loadAll();
    return () => {
      cancelled = true;
    };
  }, [citationIds]);

  const visibleCount = papers.filter((p) => !hiddenIds.has(p.id)).length;
  const hiddenCount = papers.length - visibleCount;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* 标题栏 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid var(--pf-border)",
        }}
      >
        <Typography.Text className="pf-serif" strong style={{ fontSize: 14 }}>
          <BookOpen style={{ marginRight: 6, color: "var(--pf-primary)" }} />
          {t("reference.title")}
        </Typography.Text>
        <Badge
          count={papers.length}
          style={{ backgroundColor: "var(--pf-primary)" }}
          overflowCount={99}
        />
      </div>

      {/* 引用列表 */}
      <div style={{ flex: 1, overflow: "auto", padding: "8px 12px" }}>
        {loading ? (
          <div style={{ textAlign: "center", padding: 24 }}>
            <Spin size="small" />
          </div>
        ) : papers.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <span style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }}>
                {t("reference.empty")}
                <br />
                {t("reference.hint")}
              </span>
            }
          />
        ) : (
          papers.map((paper) => {
            const hidden = hiddenIds.has(paper.id);
            return (
              <div
                key={paper.id}
                style={{
                  padding: "10px 12px",
                  marginBottom: 6,
                  borderRadius: 6,
                  background: hidden ? "rgba(245, 247, 251, 0.5)" : "rgba(245, 247, 251, 0.7)",
                  border: hidden
                    ? "1px dashed var(--pf-border-light)"
                    : "1px solid var(--pf-border)",
                  opacity: hidden ? 0.6 : 1,
                  transition: "all 0.2s ease",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "flex-start",
                    gap: 8,
                  }}
                >
                  <Link to={`/paper/${paper.id}`} style={{ flex: 1, minWidth: 0 }}>
                    <Typography.Text
                      className="pf-serif"
                      ellipsis
                      style={{ fontSize: 12, fontWeight: 500, color: "var(--pf-text-primary)" }}
                    >
                      {paper.title}
                    </Typography.Text>
                  </Link>
                  <Switch
                    size="small"
                    checked={!hidden}
                    onChange={() => onToggleHidden(paper.id)}
                    title={hidden ? t("reference.showTooltip") : t("reference.hideTooltip")}
                  />
                </div>
                <div
                  style={{
                    marginTop: 4,
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    flexWrap: "wrap",
                  }}
                >
                  <FileText style={{ fontSize: 11, color: "var(--pf-text-placeholder)" }} />
                  <span style={{ fontSize: 11, color: "var(--pf-text-muted)" }}>
                    {paper.authors.slice(0, 2).join(", ")}
                    {paper.authors.length > 2 ? " et al." : ""}
                  </span>
                  <Tag style={{ fontSize: 10, margin: 0, lineHeight: "16px", padding: "0 4px" }}>
                    {paper.year}
                  </Tag>
                  {paper.pdfUrl && (
                    <a
                      href={paper.pdfUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ fontSize: 11, color: "var(--pf-text-placeholder)" }}
                    >
                      <LinkIcon />
                    </a>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* 底部统计 */}
      <div
        style={{
          padding: "10px 16px",
          borderTop: "1px solid var(--pf-border)",
          fontSize: 12,
          color: "var(--pf-text-muted)",
          display: "flex",
          justifyContent: "space-between",
        }}
      >
        <span>{t("reference.totalCount", { count: papers.length })}</span>
        {hiddenCount > 0 && (
          <span style={{ color: "#d97706" }}>
            {t("reference.hiddenCount", { count: hiddenCount })}
          </span>
        )}
      </div>
    </div>
  );
}
