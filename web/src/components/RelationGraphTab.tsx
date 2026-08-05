import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Button, Empty, Skeleton, Space, Switch, Tag } from "antd";
import ReactECharts from "echarts-for-react";
import type { EChartsOption, GraphSeriesOption } from "echarts";
import { extractCitationSentiment, fetchRelationGraph } from "@/api/papers";
import type { RelationGraph as RelationGraphData } from "@/api/types";

interface RelationGraphTabProps {
  paperId: string;
}

interface EChartsNode {
  id: string;
  name: string;
  value: number;
  symbolSize: number;
  itemStyle: { color: string };
  sentimentLabel: string | null;
  sentimentScore: number | null;
  category: string;
  year: number;
  citations: number;
}

interface EChartsLink {
  source: string;
  target: string;
  value: number;
  type: "similarity" | "citation";
  lineStyle: { width: number; curveness: number; color: string };
  sentiment?: "support" | "criticize" | "background";
  snippet?: string;
}

/**
 * 论文关系图面板：以当前论文为中心，展示相似论文节点与引用/相似边。
 * 节点颜色按情感倾向着色，大小按引用数缩放。
 */
export default function RelationGraphTab({ paperId }: RelationGraphTabProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [graph, setGraph] = useState<RelationGraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [extracting, setExtracting] = useState(false);
  // WP-2.2: background 档默认折叠（语义噪音大），保留数据但 UI 隐藏，可手动展开
  const [showBackground, setShowBackground] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(false);
    fetchRelationGraph(paperId)
      .then((data) => {
        if (!active) return;
        setGraph(data);
      })
      .catch(() => {
        if (!active) return;
        setError(true);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [paperId]);

  const nodes: EChartsNode[] = useMemo(() => {
    if (!graph) return [];
    return graph.nodes.map((node) => ({
      id: node.id,
      name: node.title,
      value: node.citations,
      symbolSize: Math.max(20, Math.min(60, 20 + node.citations / 5)),
      itemStyle: {
        color:
          node.sentimentLabel === "support"
            ? "#52c41a"
            : node.sentimentLabel === "criticize"
              ? "#ff4d4f"
              : "#faad14",
      },
      sentimentLabel: node.sentimentLabel,
      sentimentScore: node.sentimentScore,
      category: node.category,
      year: node.year,
      citations: node.citations,
    }));
  }, [graph]);

  const links: EChartsLink[] = useMemo(() => {
    if (!graph) return [];
    return graph.edges
      .filter((edge) => showBackground || edge.sentiment !== "background")
      .map((edge) => {
        const isCitation = edge.type === "citation";
        const sentiment = edge.sentiment;
        const color = isCitation
          ? sentiment === "support"
            ? "#52c41a"
            : sentiment === "criticize"
              ? "#ff4d4f"
              : "#faad14"
          : "var(--pf-text-placeholder)";
        return {
          source: edge.source,
          target: edge.target,
          value: edge.weight,
          type: edge.type,
          lineStyle: {
            width: isCitation ? Math.max(2, edge.weight * 4) : Math.max(1, edge.weight * 5),
            curveness: 0.2,
            color,
          },
          sentiment: edge.sentiment,
          snippet: edge.snippet,
        };
      });
  }, [graph, showBackground]);

  const option: EChartsOption = useMemo(() => {
    const formatNode = (node: EChartsNode) => {
      const sentimentText =
        node.sentimentLabel && node.sentimentScore !== null
          ? `<br/>${t("paper.sentiment")}: ${node.sentimentLabel} (${node.sentimentScore.toFixed(2)})`
          : "";
      return `${node.name}<br/>${t("paper.category")}: ${node.category}<br/>${t("paper.year")}: ${node.year}<br/>${t("paper.citations")}: ${node.citations}${sentimentText}`;
    };

    const formatEdge = (edge: EChartsLink & { sentiment?: string; snippet?: string }) => {
      const label = edge.type === "citation" ? t("paper.citationEdge") : t("paper.similarityEdge");
      const sentimentText = edge.sentiment ? ` [${edge.sentiment}]` : "";
      const snippetText = edge.snippet ? `<br/>${edge.snippet.slice(0, 200)}...` : "";
      return `${label}${sentimentText}: ${edge.value.toFixed(2)}${snippetText}`;
    };

    return {
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const p = params as { dataType?: string; data: EChartsNode | EChartsLink };
          if (p.dataType === "node") {
            return formatNode(p.data as EChartsNode);
          }
          return formatEdge(p.data as EChartsLink);
        },
      },
      series: [
        {
          type: "graph",
          layout: "force",
          data: nodes,
          links,
          roam: true,
          label: {
            show: true,
            position: "right",
            formatter: "{b}",
            fontSize: 12,
          },
          force: {
            repulsion: 300,
            gravity: 0.1,
            edgeLength: [80, 200],
          },
          emphasis: {
            focus: "adjacency",
            lineStyle: {
              width: 4,
            },
          },
        } as GraphSeriesOption,
      ],
    };
  }, [nodes, links, t]);

  const onEvents = useMemo(
    () => ({
      click: (params: unknown) => {
        const p = params as { dataType?: string; data?: { id?: string } };
        if (p.dataType === "node" && p.data?.id) {
          navigate(`/paper/${p.data.id}`);
        }
      },
    }),
    [navigate],
  );

  if (loading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }

  if (error || !graph) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t("paper.relationGraphError", "加载关系图失败")}
      />
    );
  }

  const handleExtract = async () => {
    setExtracting(true);
    try {
      await extractCitationSentiment(paperId);
      // 触发后台任务后刷新关系图
      const data = await fetchRelationGraph(paperId);
      setGraph(data);
    } catch {
      setError(true);
    } finally {
      setExtracting(false);
    }
  };

  if (graph.nodes.length === 0) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t("paper.noRelationGraphData", "暂无关系图数据")}
      />
    );
  }

  const hasCitationEdges = graph.edges.some((e) => e.type === "citation");

  return (
    <div
      style={{ position: "relative" }}
      aria-label={t("detail.tabRelationGraph", "关系图")}
      role="img"
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Tag color="green">{t("paper.supportSentiment", "支持")}</Tag>
        <Tag color="red">{t("paper.criticizeSentiment", "批评")}</Tag>
        <Tag color="default" style={{ opacity: showBackground ? 1 : 0.4 }}>
          {t("paper.backgroundSentiment", "背景引用")}
        </Tag>
        <Switch
          size="small"
          checked={showBackground}
          onChange={setShowBackground}
          checkedChildren={t("paper.backgroundSentiment", "背景引用")}
          unCheckedChildren={t("paper.showBackgroundSentiment", "显示背景")}
        />
        <Tag color={graph.mode === "citation" ? "blue" : "orange"}>
          {t(`paper.relationGraphMode.${graph.mode}`, graph.mode)}
        </Tag>
        <Button onClick={handleExtract} loading={extracting} size="small">
          {t("paper.extractCitationSentiment", "抽取被引情感")}
        </Button>
      </Space>
      {!hasCitationEdges && (
        <div style={{ marginBottom: 8, color: "var(--pf-text-placeholder)" }}>
          {t("paper.noCitationSentimentData", "暂无被引情感数据，点击上方按钮抽取")}
        </div>
      )}
      <ReactECharts option={option} style={{ height: 500, width: "100%" }} onEvents={onEvents} />
    </div>
  );
}
