import ReferencePreview from "./ReferencePreview";

/**
 * 工具函数：在文本节点中检测 [@paper_id] 并替换为 ReferencePreview 组件。
 *
 * 用于 react-markdown 的 components 覆盖：将段落、列表项等元素的字符串子节点
 * 按 [@xxx] 模式拆分，匹配部分渲染为可悬浮预览的 <ReferencePreview>。
 */
const CITE_PATTERN = /\[@([^\]\s]+)\]/g;

export function renderTextWithCitations(node: React.ReactNode): React.ReactNode {
  if (typeof node === "string") {
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;
    // 重置正则的 lastIndex（全局正则需要）
    CITE_PATTERN.lastIndex = 0;
    let key = 0;
    while ((match = CITE_PATTERN.exec(node)) !== null) {
      if (match.index > lastIndex) {
        parts.push(node.slice(lastIndex, match.index));
      }
      parts.push(<ReferencePreview key={`cite-${key++}`} paperId={match[1]} />);
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < node.length) {
      parts.push(node.slice(lastIndex));
    }
    return parts.length > 0 ? parts : node;
  }
  if (Array.isArray(node)) {
    return node.map((child) => renderTextWithCitations(child));
  }
  return node;
}
