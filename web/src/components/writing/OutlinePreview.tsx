import { FolderOpen, FileText, Import, RefreshCw, Expand, Shrink } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Space, Tree, Typography } from "antd";

import type { DataNode } from "antd/es/tree";
import type { TreeProps } from "antd";
import type { OutlineNode } from "@/api/types";
import { createChapter } from "@/api/writing";

interface OutlinePreviewProps {
  /** LLM 生成的大纲结构 */
  outline: OutlineNode[];
  /** 目标项目 ID（用于批量导入章节） */
  projectId: number;
  /** 导入完成回调（父组件据此关闭 Modal 并跳转编辑器） */
  onImported: () => void;
  /** 重新生成回调（父组件重新调用 LLM） */
  onRegenerate: () => void;
}

/**
 * 大纲预览组件（P1 自动生成大纲）。
 *
 * - 使用 Ant Design Tree 展示 LLM 生成的大纲层级结构
 * - 每个节点显示章节标题，层级缩进清晰
 * - 底部按钮：「导入此大纲」（递归调用 createChapter 批量创建）
 *            和「重新生成」（触发父组件重新调用 LLM）
 */
export default function OutlinePreview({
  outline,
  projectId,
  onImported,
  onRegenerate,
}: OutlinePreviewProps) {
  const { t } = useTranslation();
  const [importing, setImporting] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() => collectAllKeys(outline));

  /** 统计节点数量与最大深度 */
  const stats = useMemo(() => countNodes(outline), [outline]);

  /** 收集所有含子节点的 key（用于全部展开） */
  function collectAllKeys(nodes: OutlineNode[], prefix = "p"): React.Key[] {
    const keys: React.Key[] = [];
    nodes.forEach((n, i) => {
      const key = `${prefix}-${i}`;
      const children = n.children ?? [];
      if (children.length > 0) {
        keys.push(key);
        keys.push(...collectAllKeys(children, key));
      }
    });
    return keys;
  }

  /** 递归统计：总节点数、叶节点数、最大深度 */
  function countNodes(nodes: OutlineNode[]): {
    total: number;
    leaves: number;
    maxDepth: number;
  } {
    let total = 0;
    let leaves = 0;
    let maxDepth = 0;
    const walk = (list: OutlineNode[], depth: number) => {
      list.forEach((n) => {
        total += 1;
        maxDepth = Math.max(maxDepth, depth);
        const children = n.children ?? [];
        if (children.length === 0) {
          leaves += 1;
        } else {
          walk(children, depth + 1);
        }
      });
    };
    walk(nodes, 1);
    return { total, leaves, maxDepth };
  }

  const toTreeData = (nodes: OutlineNode[], prefix = "p"): DataNode[] =>
    nodes.map((n, i) => {
      const key = `${prefix}-${i}`;
      const children = n.children ?? [];
      return {
        key,
        title: n.title,
        children: children.length > 0 ? toTreeData(children, key) : [],
      };
    });

  const handleExpand: TreeProps["onExpand"] = (keys) => {
    setExpandedKeys(keys);
  };

  const handleExpandAll = () => {
    setExpandedKeys(collectAllKeys(outline));
  };

  const handleCollapseAll = () => {
    setExpandedKeys([]);
  };

  /**
   * 递归创建章节：先创建父章节，拿到返回的 id，
   * 再以该 id 作为 parentId 创建其子章节，保证层级关系正确。
   */
  const importRecursive = async (nodes: OutlineNode[], parentId: number | null): Promise<void> => {
    for (let i = 0; i < nodes.length; i++) {
      const node = nodes[i];
      const created = await createChapter(projectId, {
        title: node.title,
        parentId,
        order: i,
      });
      const children = node.children ?? [];
      if (children.length > 0) {
        await importRecursive(children, created.id);
      }
    }
  };

  const handleImport = async () => {
    setImporting(true);
    try {
      await importRecursive(outline, null);
      onImported();
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setImporting(false);
    }
  };

  const titleRender = (node: DataNode) => {
    const hasChildren = (node.children?.length ?? 0) > 0;
    return (
      <Space size={4}>
        {hasChildren ? (
          <FolderOpen style={{ color: "var(--pf-primary)", fontSize: 13 }} />
        ) : (
          <FileText style={{ color: "var(--pf-text-placeholder)", fontSize: 13 }} />
        )}
        <span className="pf-serif" style={{ fontSize: 13, color: "var(--pf-text-primary)" }}>
          {String(node.title)}
        </span>
      </Space>
    );
  };

  if (outline.length === 0) {
    return (
      <div
        style={{
          textAlign: "center",
          padding: 24,
          color: "var(--pf-text-placeholder)",
          fontSize: 13,
        }}
      >
        {t("outlinePreview.emptyOutline")}
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {/* 统计信息 */}
      <div
        style={{
          marginBottom: 12,
          display: "flex",
          gap: 16,
          fontSize: 12,
          color: "var(--pf-text-muted)",
        }}
      >
        <span>{t("outline.totalStats", { total: stats.total })}</span>
        <span>{t("outline.leafNodes", { count: stats.leaves })}</span>
        <span>{t("outline.maxDepth", { depth: stats.maxDepth })}</span>
      </div>

      {/* 工具栏 */}
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 4 }}>
        <Space size={2}>
          <Button size="small" type="text" icon={<Expand />} onClick={handleExpandAll}>
            {t("outlinePreview.expand")}
          </Button>
          <Button size="small" type="text" icon={<Shrink />} onClick={handleCollapseAll}>
            {t("outlinePreview.collapse")}
          </Button>
        </Space>
      </div>

      {/* 大纲树 */}
      <div
        style={{
          maxHeight: 360,
          overflow: "auto",
          border: "1px solid var(--pf-border)",
          borderRadius: 6,
          padding: "8px 4px",
          background: "rgba(255,255,255,0.6)",
        }}
      >
        <Tree
          treeData={toTreeData(outline)}
          titleRender={titleRender}
          expandedKeys={expandedKeys}
          onExpand={handleExpand}
          blockNode
          showLine
          className="pf-outline-tree"
        />
      </div>

      {/* 提示文案 */}
      <Typography.Text type="secondary" style={{ fontSize: 12, marginTop: 8 }}>
        {t("outlinePreview.importHint")}
      </Typography.Text>

      {/* 底部操作 */}
      <div
        style={{
          marginTop: 16,
          display: "flex",
          justifyContent: "flex-end",
          gap: 8,
        }}
      >
        <Button icon={<RefreshCw />} onClick={onRegenerate} disabled={importing}>
          {t("outlinePreview.regenerate")}
        </Button>
        <Button type="primary" icon={<Import />} onClick={handleImport} loading={importing}>
          {t("outlinePreview.importOutline")}
        </Button>
      </div>
    </div>
  );
}
