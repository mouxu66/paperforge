import { Plus, Trash2, FileText, FolderOpen, Expand, Shrink } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Input, Modal, Space, Tooltip, Tree, Typography, message } from "antd";

import type { DataNode } from "antd/es/tree";
import type { TreeProps } from "antd";
import type { Chapter, ChapterTreeNode } from "@/api/types";
import WordCountBadge from "./WordCountBadge";

interface OutlineTreeProps {
  tree: ChapterTreeNode[];
  selectedId: number | null;
  loading: boolean;
  onSelect: (chapter: ChapterTreeNode) => void;
  /** 创建章节：parentId 为 null 表示根章节 */
  onCreate: (parentId: number | null, title: string) => Promise<void>;
  /** 删除章节（含子孙） */
  onDelete: (chapter: Chapter) => Promise<void>;
  /** 拖拽移动章节：挂到 parentId 下并插入到 index 位置 */
  onMove: (chapterId: number, parentId: number | null, index: number) => Promise<void>;
  /** 章节字数映射（id → 字数），可选 */
  wordCountMap?: Map<number, number>;
}

/**
 * 章节大纲树 —— 左侧栏。
 *
 * - 使用 Ant Design Tree 渲染无限层级大纲
 * - 缩进引导线（VS Code 风格垂直线）+ 16px 层级间距
 * - 拖拽预览：蓝色水平插入线 + 父节点浅蓝高亮
 * - 「全部展开 / 全部收起」按钮（受控 expandedKeys）
 * - 每个节点附带「新增子章节 / 删除」悬浮操作
 * - 新增章节标题通过内部 Modal 输入
 */
export default function OutlineTree({
  tree,
  selectedId,
  loading,
  onSelect,
  onCreate,
  onDelete,
  onMove,
  wordCountMap,
}: OutlineTreeProps) {
  const { t } = useTranslation();
  // 新增章节 Modal 状态：addParent 为 null 表示关闭，'root' 表示根章节，否则为父节点
  const [addParent, setAddParent] = useState<ChapterTreeNode | "root" | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // 受控展开键：用于「全部展开 / 全部收起」
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() => collectAllKeys(tree));

  // id → ChapterTreeNode 扁平映射，便于 onSelect 回查节点
  const nodeMap = useMemo(() => {
    const map = new Map<number, ChapterTreeNode>();
    const walk = (nodes: ChapterTreeNode[]) => {
      nodes.forEach((n) => {
        map.set(n.id, n);
        walk(n.children);
      });
    };
    walk(tree);
    return map;
  }, [tree]);

  /** 收集树中所有节点的 key（用于全部展开） */
  function collectAllKeys(nodes: ChapterTreeNode[]): React.Key[] {
    const keys: React.Key[] = [];
    const walk = (list: ChapterTreeNode[]) => {
      list.forEach((n) => {
        if (n.children.length > 0) {
          keys.push(String(n.id));
          walk(n.children);
        }
      });
    };
    walk(nodes);
    return keys;
  }

  // P2-1: 统计总节点数，超过阈值时启用虚拟滚动
  const VIRTUAL_THRESHOLD = 50;
  const VIRTUAL_HEIGHT = 500;
  const totalNodes = useMemo(() => {
    let count = 0;
    const walk = (list: ChapterTreeNode[]) => {
      list.forEach((n) => {
        count += 1;
        walk(n.children);
      });
    };
    walk(tree);
    return count;
  }, [tree]);
  const virtual = totalNodes >= VIRTUAL_THRESHOLD;

  const toTreeData = (nodes: ChapterTreeNode[]): DataNode[] =>
    nodes.map((n) => ({
      key: String(n.id),
      title: n.title,
      children: toTreeData(n.children),
    }));

  const handleSelect = (keys: React.Key[]) => {
    const key = keys[0];
    if (key == null) return;
    const node = nodeMap.get(Number(key));
    if (node) onSelect(node);
  };

  /** 全部展开：展开所有含子节点的节点 */
  const handleExpandAll = () => {
    setExpandedKeys(collectAllKeys(tree));
  };

  /** 全部收起：折叠所有节点 */
  const handleCollapseAll = () => {
    setExpandedKeys([]);
  };

  /** 受控展开/收起回调 */
  const handleExpand: TreeProps["onExpand"] = (keys) => {
    setExpandedKeys(keys);
  };

  const startAdd = (parent: ChapterTreeNode | "root") => {
    setNewTitle("");
    setAddParent(parent);
  };

  const handleCreate = async () => {
    const title = newTitle.trim();
    if (!title) {
      message.warning(t("outline.titleRequired"));
      return;
    }
    setSubmitting(true);
    try {
      const parentId = addParent === "root" || addParent === null ? null : addParent.id;
      await onCreate(parentId, title);
      setAddParent(null);
      // 新增后确保父节点展开
      if (parentId !== null) {
        const key = String(parentId);
        if (!expandedKeys.includes(key)) {
          setExpandedKeys((prev) => [...prev, key]);
        }
      }
    } catch {
      // 错误已由 http 拦截器提示
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (chapter: Chapter) => {
    Modal.confirm({
      title: t("outline.deleteChapterTitle"),
      content: t("outline.deleteChapterConfirm", { title: chapter.title }),
      okText: t("common.delete"),
      okType: "danger",
      cancelText: t("common.cancel"),
      onOk: async () => {
        await onDelete(chapter);
      },
    });
  };

  /**
   * 拖拽落点处理：依据 Ant Design Tree 的 info 计算 (parentId, index)。
   * - dropToGap=false：拖入节点内部，作为其子节点（追加到末尾）
   * - dropToGap=true：拖到节点间隙，依据 dropPosition 判断在 dropNode 之前或之后
   * 计算 index 时先排除 dragNode（若同父则其原位置会让位），再定位 dropNode 的新序号
   */
  const handleDrop: TreeProps["onDrop"] = async (info) => {
    const dragId = Number(info.dragNode.key);
    const dropId = Number(info.node.key);
    const dragNode = nodeMap.get(dragId);
    const dropNode = nodeMap.get(dropId);
    if (!dragNode || !dropNode || dragId === dropId) return;

    let parentId: number | null;
    let index: number;

    if (!info.dropToGap) {
      // 拖入节点内部：作为其子节点，追加到末尾
      parentId = dropId;
      index = dropNode.children.length;
    } else {
      // 拖到节点之间的间隙
      parentId = dropNode.parentId;
      // 取 dropNode 当前的兄弟列表（含 dragNode 若同父），排除 dragNode 后定位 dropNode 序号
      const siblings =
        dropNode.parentId === null ? tree : (nodeMap.get(dropNode.parentId)?.children ?? []);
      const filtered = siblings.filter((s) => s.id !== dragId);
      const pos = filtered.findIndex((s) => s.id === dropId);
      if (pos < 0) return;
      // dropPosition < 0 表示落在 dropNode 上方间隙，否则落在下方间隙
      index = info.dropPosition < 0 ? pos : pos + 1;
    }

    try {
      await onMove(dragId, parentId, index);
      // 拖拽后展开目标父节点，让用户看到结果
      if (parentId !== null) {
        const key = String(parentId);
        if (!expandedKeys.includes(key)) {
          setExpandedKeys((prev) => [...prev, key]);
        }
      }
    } catch {
      // 错误已由 http 拦截器提示
    }
  };

  const titleRender = (node: DataNode) => {
    const id = Number(node.key);
    const chapter = nodeMap.get(id);
    if (!chapter) return <span>{String(node.title)}</span>;
    const hasChildren = chapter.children.length > 0;
    return (
      <div
        className="pf-outline-node"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          width: "100%",
          paddingRight: 4,
        }}
      >
        <Space size={4}>
          {hasChildren ? (
            <FolderOpen style={{ color: "var(--pf-primary)", fontSize: 13 }} />
          ) : (
            <FileText style={{ color: "var(--pf-text-placeholder)", fontSize: 13 }} />
          )}
          <span
            className="pf-serif"
            style={{
              fontSize: 13,
              color: selectedId === id ? "var(--pf-primary)" : "var(--pf-text-primary)",
              fontWeight: selectedId === id ? 600 : 400,
            }}
          >
            {chapter.title}
          </span>
          {wordCountMap && <WordCountBadge count={wordCountMap.get(id) ?? 0} />}
        </Space>
        <Space size={0} className="pf-outline-actions">
          <Button
            size="small"
            type="text"
            icon={<Plus />}
            onClick={(e) => {
              e.stopPropagation();
              startAdd(chapter);
            }}
            title={t("outline.addChildChapter")}
          />
          <Button
            size="small"
            type="text"
            danger
            icon={<Trash2 />}
            onClick={(e) => {
              e.stopPropagation();
              void handleDelete(chapter);
            }}
            title={t("outline.deleteChapter")}
          />
        </Space>
      </div>
    );
  };

  const addModalTitle =
    addParent === "root"
      ? t("outline.addRootHint")
      : addParent === null
        ? ""
        : t("outline.addChildHint", { title: addParent.title });

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
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
          {t("outline.title")}
        </Typography.Text>
        <Space size={2}>
          <Tooltip title={t("outline.expandAll")}>
            <Button
              size="small"
              type="text"
              icon={<Expand />}
              onClick={handleExpandAll}
              disabled={tree.length === 0}
            />
          </Tooltip>
          <Tooltip title={t("outline.collapseAll")}>
            <Button
              size="small"
              type="text"
              icon={<Shrink />}
              onClick={handleCollapseAll}
              disabled={tree.length === 0}
            />
          </Tooltip>
          <Button size="small" type="text" icon={<Plus />} onClick={() => startAdd("root")}>
            {t("outline.addRootChapter")}
          </Button>
        </Space>
      </div>

      <div
        className="pf-outline-body"
        style={{
          flex: 1,
          overflow: virtual ? "hidden" : "auto",
          padding: "8px 4px",
        }}
      >
        {tree.length === 0 && !loading ? (
          <div
            style={{
              textAlign: "center",
              color: "var(--pf-text-placeholder)",
              fontSize: 13,
              padding: 24,
            }}
          >
            {t("outline.noChapters")}
          </div>
        ) : (
          <Tree
            treeData={toTreeData(tree)}
            titleRender={titleRender}
            selectedKeys={selectedId != null ? [String(selectedId)] : []}
            expandedKeys={expandedKeys}
            onExpand={handleExpand}
            onSelect={handleSelect}
            blockNode
            showLine
            draggable
            onDrop={handleDrop}
            className="pf-outline-tree"
            // P2-1: 节点数 ≥ 50 时启用 antd Tree 虚拟滚动，仅渲染可见区域
            height={virtual ? VIRTUAL_HEIGHT : undefined}
          />
        )}
      </div>

      <Modal
        title={addModalTitle}
        open={addParent !== null}
        onOk={handleCreate}
        onCancel={() => setAddParent(null)}
        confirmLoading={submitting}
        okText={t("common.create")}
        cancelText={t("common.cancel")}
        destroyOnHidden
      >
        <Input
          autoFocus
          placeholder={t("outline.chapterTitlePlaceholder")}
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
          onPressEnter={handleCreate}
          style={{ marginTop: 16 }}
        />
      </Modal>
    </div>
  );
}
