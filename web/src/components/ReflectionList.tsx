import {
  RefreshCw,
  Eye,
  Zap,
  CheckCircle,
  XCircle,
  Clock,
  FileText,
  Trash2,
  SquareMinus,
  TriangleAlert,
  ShieldCheck,
  Download,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
/**
 * ReflectionList 组件：分页列出 reflection 报告评审记录。
 *
 * 列：论文标题、状态、4 维评分（带 Progress）、平均分、判决、提交时间、操作
 * - 后端 reflection_result 已通过 _legacy_data_compat 兜底，
 *   前端读取时只需用 Number(score ?? 0) 即可，无需 ?. 防护
 * - 表格内「重新审稿」按钮复用 reflection/run 接口 + dedup 自愈（429/409 友好提示）
 * - SSE 进度通过 useTaskStore.subscribeSSE 订阅；列表通过 setInterval 5s 静默轮询活跃行
 */
import { useState, useEffect, useCallback, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  Table,
  Tag,
  Button,
  Space,
  Tooltip,
  Typography,
  App,
  Empty,
  Progress,
  Spin,
  Popconfirm,
} from "antd";

import {
  listReflectionReviews,
  startReflectionReview,
  deleteReflectionReview,
  batchDeleteReflectionReviews,
  type ReflectionListItem,
} from "@/api/reflection";
import { downloadIntegrityBatchZip } from "@/api/reports";
import { useTaskStore } from "@/store/useTaskStore";
import IntegrityReportModal from "./IntegrityReportModal";
import ReflectionRadar from "./ReflectionRadar";

const { Title, Text } = Typography;

const ACTIVE_STATUSES = new Set(["pending", "running"]);

const VERDICT_COLORS: Record<string, string> = {
  well_done: "green",
  needs_evidence: "gold",
  needs_depth: "orange",
  rewrite_required: "red",
  llm_failed: "red",
  "n/a": "default",
};

const VERDICT_LABELS: Record<string, string> = {
  well_done: "写得好",
  needs_evidence: "需补证据",
  needs_depth: "需深化",
  rewrite_required: "需重写",
  llm_failed: "评审无效（LLM故障）",
  "n/a": "无评审",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "default",
  running: "blue",
  completed: "green",
  failed: "red",
  timed_out: "orange",
};

const STATUS_LABELS: Record<string, string> = {
  pending: "等待",
  running: "进行中",
  completed: "已完成",
  failed: "失败",
  timed_out: "已超时",
};

const STATUS_ICONS: Record<string, React.ReactNode> = {
  pending: <Clock />,
  running: <RefreshCw className="pf-spin" />,
  completed: <CheckCircle />,
  failed: <XCircle />,
  timed_out: <TriangleAlert />,
};

interface ReflectionListProps {
  /** 是否在 HomePage 内嵌展示（紧凑模式） */
  compact?: boolean;
  /** 显示行数限制（compact 模式默认 5） */
  pageSize?: number;
}

export default function ReflectionList({
  compact = false,
  pageSize: initialPageSize = 20,
}: ReflectionListProps) {
  const navigate = useNavigate();
  const { subscribeSSE } = useTaskStore();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [data, setData] = useState<ReflectionListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(compact ? Math.min(initialPageSize, 5) : initialPageSize);
  const [loading, setLoading] = useState(false);
  const [restartingIds, setRestartingIds] = useState<Set<string>>(new Set());
  // 批量选择：useState<string[]>（rowKey="id" → ReflectionListItem.id）
  // compact 模式禁用（嵌入式 HomePage 控件不暴露批量操作，避免误操作）
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [batchDeleting, setBatchDeleting] = useState(false);
  // 诚信报告一页纸预览（记录选中的 report paper_id）
  const [integrityPaperId, setIntegrityPaperId] = useState<string | null>(null);
  // 全班批量导出进行中（防止重复点击）
  const [batchExporting, setBatchExporting] = useState(false);
  const dataRef = useRef(data);
  // SSE 订阅 cleanup Set：每次重评时取消上一个订阅，防内存泄露
  const restartUnsubsRef = useRef<Set<() => void>>(new Set());

  useEffect(() => {
    dataRef.current = data;
  }, [data]);

  // 卸载时取消所有进行中的 SSE 订阅
  useEffect(() => {
    const unsubs = restartUnsubsRef.current;
    return () => {
      unsubs.forEach((unsub) => unsub());
      unsubs.clear();
    };
  }, []);

  const fetchList = useCallback(
    async (p: number, silent = false) => {
      if (!silent) setLoading(true);
      try {
        const res = await listReflectionReviews(pageSize, (p - 1) * pageSize);
        setData(res.items);
        setTotal(res.total);
      } catch {
        if (!silent) message.error("获取 reflection 列表失败");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [pageSize, message],
  );

  useEffect(() => {
    fetchList(page);
    // 进行中的 reflection 评审每 5 秒静默轮询
    const timer = setInterval(() => {
      if (dataRef.current.some((d) => ACTIVE_STATUSES.has(d.status))) {
        fetchList(page, true);
      }
    }, 5000);
    return () => clearInterval(timer);
  }, [page, fetchList]);

  const handleDelete = useCallback(
    async (reviewId: number) => {
      try {
        await deleteReflectionReview(String(reviewId));
        message.success("评审记录已删除");
        fetchList(page);
      } catch (err0: unknown) {
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        message.error(e?.response?.data?.detail || "删除失败");
      }
    },
    [fetchList, page, message],
  );

  // 批量删除：调用 POST /api/depth/reviews/batch-delete，参数为 review_ids 列表
  const handleBatchDelete = useCallback(async () => {
    if (selectedIds.length === 0) return;
    setBatchDeleting(true);
    try {
      const res = await batchDeleteReflectionReviews(selectedIds);
      if (res.failed_ids && res.failed_ids.length > 0) {
        message.warning(
          `部分删除失败：成功 ${res.deleted_count} 条，失败 ${res.failed_ids.length} 条`,
          4,
        );
      } else {
        message.success(`已批量删除 ${res.deleted_count} 条评审记录`);
      }
      setSelectedIds([]);
      fetchList(page, true);
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error(e?.response?.data?.detail || "批量删除失败");
    } finally {
      setBatchDeleting(false);
    }
  }, [selectedIds, fetchList, page, message]);

  const handleRestart = useCallback(
    async (paperId: string) => {
      setRestartingIds((prev) => new Set(prev).add(paperId));
      try {
        const res = await startReflectionReview(paperId);
        message.success(`重评已提交（task_id=${res.task_id}）`);

        // 订阅 SSE 进度（onUpdate 静默刷新列表以感知轮询回退时的进度）
        const unsub = subscribeSSE(
          res.task_id,
          () => {
            fetchList(page, true);
          },
          () => {
            fetchList(page, true);
            message.success("重评完成");
            unsub();
            restartUnsubsRef.current.delete(unsub);
          },
          (info) => {
            fetchList(page, true);
            message.error(info.error || "重评失败");
            unsub();
            restartUnsubsRef.current.delete(unsub);
          },
        );
        restartUnsubsRef.current.add(unsub);
      } catch (err0: unknown) {
        const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
        const status = e?.response?.status;
        const detail = e?.response?.data?.detail || e?.message || "重评启动失败";
        if (status === 409) {
          const firstLine = String(detail).split("\n")[0];
          message.warning(`已有进行中的任务。${firstLine}`, 4);
        } else if (status === 400) {
          // 例如：报告无全文
          message.error(detail);
        } else {
          message.error(detail);
        }
        fetchList(page, true);
      } finally {
        setRestartingIds((prev) => {
          const next = new Set(prev);
          next.delete(paperId);
          return next;
        });
      }
    },
    [fetchList, page, subscribeSSE, message],
  );

  // 全班诚信报告批量导出：启动任务 → 轮询（消息实时更新进度）→ 自动下载 zip
  const handleBatchExport = useCallback(async () => {
    if (batchExporting) return;
    setBatchExporting(true);
    const updateMsg = (text: string) =>
      message.open({ key: "integrity-batch", type: "loading", content: text, duration: 0 });
    updateMsg("正在启动全班诚信报告打包…");
    try {
      await downloadIntegrityBatchZip((percent) => {
        updateMsg(`正在打包全班诚信报告… ${percent}%`);
      });
      message.success({
        key: "integrity-batch",
        content: "全班诚信报告已打包下载（zip，每生一份 docx）",
        duration: 3,
      });
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.error({
        key: "integrity-batch",
        content: e?.response?.data?.detail || e?.message || "批量导出失败",
        duration: 4,
      });
    } finally {
      setBatchExporting(false);
    }
  }, [batchExporting, message]);

  const columns = [
    {
      title: "报告",
      dataIndex: "paper_title",
      key: "paper_title",
      width: 280,
      fixed: "left" as const,
      ellipsis: true,
      render: (t: string, record: ReflectionListItem) => (
        <Space size={4} orientation="vertical">
          <Tooltip title={t || "(未命名)"}>
            <span className="pf-reflection-title">{t || "(未命名)"}</span>
          </Tooltip>
          <Text type="secondary" style={{ fontSize: 11 }} copyable={{ text: record.paper_id }}>
            {record.paper_id}
          </Text>
        </Space>
      ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 96,
      render: (s: string) => (
        <Tag icon={STATUS_ICONS[s]} color={STATUS_COLORS[s]}>
          {STATUS_LABELS[s] || s}
        </Tag>
      ),
    },
    {
      title: t("reflection.list.scoresColumn"),
      key: "scores",
      width: 134,
      align: "center" as const,
      render: (_: unknown, record: ReflectionListItem) => (
        // 表格中隐藏雷达下方的均分 text：避免与右侧"平均分"列信息重复，
        // 同时让行高从 ~138 收紧到 ~88。
        <ReflectionRadar
          showDetail={false}
          showFooter={false}
          scores={record.llm_failed ? null : record.scores}
          size={80}
        />
      ),
    },
    {
      title: "平均分",
      dataIndex: ["scores", "average"],
      key: "average",
      width: 110,
      sorter: false,
      render: (avg: number | null, record: ReflectionListItem) => {
        if (record.llm_failed || record.verdict === "llm_failed") {
          return <Tag color="red">评审无效（LLM 故障）</Tag>;
        }
        if (avg == null) return <Text type="secondary">-</Text>;
        const pct = Math.round(avg * 100);
        const hasFidelity = !record.llm_failed && record.fidelity != null;
        const hasCoverage = !record.llm_failed && record.coverage != null;
        return (
          <div>
            <Tooltip
              title={
                hasFidelity && hasCoverage
                  ? `6 维加权 ${pct}% | 有据性 ${Math.round((record.fidelity ?? 0) * 100)}% | 覆盖度 ${Math.round((record.coverage ?? 0) * 100)}%`
                  : hasFidelity
                    ? `均分 ${pct}% | 有据性 ${Math.round((record.fidelity ?? 0) * 100)}%`
                    : `${pct}%`
              }
            >
              <Progress
                percent={pct}
                size="small"
                showInfo={false}
                strokeColor={pct >= 70 ? "#52c41a" : pct >= 50 ? "#faad14" : "#ff4d4f"}
                style={{ width: 60, marginBottom: 2 }}
              />
              <div>
                <Text style={{ fontSize: 12, fontWeight: 600 }}>{pct}%</Text>
                {hasFidelity && (
                  <Tooltip
                    title={`有据性 ${Math.round((record.fidelity ?? 0) * 100)}%（报告→论文，防编造）`}
                  >
                    <Tag
                      color={
                        (record.fidelity ?? 0) >= 0.6
                          ? "green"
                          : (record.fidelity ?? 0) >= 0.35
                            ? "gold"
                            : "red"
                      }
                      style={{ fontSize: 10, padding: "0 4px", marginLeft: 4, lineHeight: "18px" }}
                    >
                      F{Math.round((record.fidelity ?? 0) * 100)}
                    </Tag>
                  </Tooltip>
                )}
                {hasCoverage && (
                  <Tooltip
                    title={`覆盖度 ${Math.round((record.coverage ?? 0) * 100)}%（论文→报告，防遗漏核心，正确性核心指标）`}
                  >
                    <Tag
                      color={
                        (record.coverage ?? 0) >= 0.6
                          ? "green"
                          : (record.coverage ?? 0) >= 0.35
                            ? "gold"
                            : "red"
                      }
                      style={{ fontSize: 10, padding: "0 4px", marginLeft: 4, lineHeight: "18px" }}
                    >
                      C{Math.round((record.coverage ?? 0) * 100)}
                    </Tag>
                  </Tooltip>
                )}
              </div>
            </Tooltip>
          </div>
        );
      },
    },
    {
      title: "判决",
      dataIndex: "verdict",
      key: "verdict",
      width: 96,
      render: (v: string | null, record: ReflectionListItem) => {
        if (!v) return <Text type="secondary">-</Text>;
        return (
          <Tooltip title={record.verdict_reason || VERDICT_LABELS[v] || v}>
            <Tag color={VERDICT_COLORS[v] || "default"}>{VERDICT_LABELS[v] || v}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: "证据/观点",
      key: "evidence",
      width: 84,
      align: "center" as const,
      render: (_: unknown, record: ReflectionListItem) => (
        <Tooltip
          title={
            <div>
              <div>证据片段: {record.effective_evidence_count}</div>
              <div>核心观点: {record.claims_count}</div>
            </div>
          }
        >
          <Text style={{ fontSize: 12 }}>
            {record.effective_evidence_count}/{record.claims_count}
          </Text>
        </Tooltip>
      ),
    },
    {
      title: "完成时间",
      dataIndex: "completed_at",
      width: 132,
      render: (t: string | null) => (t ? new Date(t).toLocaleString() : "-"),
    },
    {
      title: "操作",
      key: "actions",
      width: compact ? 156 : 196,
      fixed: "right" as const,
      align: "center" as const,
      render: (_: unknown, record: ReflectionListItem) => {
        const isActive = ACTIVE_STATUSES.has(record.status);
        const isRestarting = restartingIds.has(record.paper_id);
        // 操作列改纯图标 + Tooltip（aria-label 兜底）以把列宽从 520 字节收到
        // ~196 px：这是解「拥挤」的主开关。Tooltip 同时承担原"按钮 + 文字"
        // 的提示职责（鼠标 hover / 触屏 tap 都能看到）。
        return (
          <Space size={6} className="pf-reflection-actions" onClick={(e) => e.stopPropagation()}>
            <Tooltip title="查看报告评审详情">
              <Button
                size="small"
                type="text"
                className="pf-reflection-action-view"
                icon={<Eye />}
                aria-label="查看"
                onClick={() => navigate(`/reflection/result/${record.paper_id}`)}
              />
            </Tooltip>
            {!compact && (
              <Tooltip title="查看 AI 使用声明 + 真实性报告（一页纸，可导出归档）">
                <Button
                  size="small"
                  type="text"
                  className="pf-reflection-action-integrity"
                  icon={<ShieldCheck />}
                  aria-label="诚信报告"
                  onClick={(e) => {
                    e.stopPropagation();
                    setIntegrityPaperId(record.paper_id);
                  }}
                />
              </Tooltip>
            )}
            <Tooltip
              title={
                isActive
                  ? `${record.paper_id} 正在评审中，请等待完成后再重评`
                  : "重新发起 reflection 评审"
              }
            >
              <Button
                size="small"
                type="text"
                className="pf-reflection-action-restart"
                icon={<Zap />}
                loading={isRestarting}
                disabled={isActive}
                aria-label="重评"
                onClick={() => handleRestart(record.paper_id)}
              />
            </Tooltip>
            <Popconfirm
              title="确定删除该评审记录？"
              description="此操作不可撤销"
              onConfirm={(e) => {
                e?.stopPropagation();
                handleDelete(record.id);
              }}
              onCancel={(e) => e?.stopPropagation()}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true }}
            >
              <Tooltip title="删除该条评审记录">
                <Button
                  size="small"
                  type="text"
                  className="pf-reflection-action-delete"
                  danger
                  icon={<Trash2 />}
                  aria-label="删除"
                  onClick={(e) => e.stopPropagation()}
                />
              </Tooltip>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  // compact 模式仅保留「标题 / 状态 / 平均分 / 判决 / 操作」5 列；按 column.key
  // 过滤而非按 i18n title，避免依赖杕抹中文（避免 i18n key 未来跨语言影响布局）
  const COMPACT_COLUMN_KEYS = new Set(["paper_title", "status", "average", "verdict", "actions"]);

  if (compact) {
    return (
      <div>
        <Spin spinning={loading}>
          {data.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("reflection.list.empty")}
              style={{ padding: "24px 0" }}
            />
          ) : (
            <Table
              dataSource={data}
              rowKey="id"
              size="small"
              pagination={false}
              columns={columns.filter((c) => COMPACT_COLUMN_KEYS.has(String(c.key)))}
            />
          )}
        </Spin>
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
          flexWrap: "wrap",
          gap: 12,
        }}
      >
        <Space>
          <Title level={4} style={{ margin: 0 }}>
            <FileText style={{ color: "var(--pf-primary)", marginRight: 8 }} />
            {t("reflection.list.title")}
          </Title>
          <Text type="secondary" style={{ fontSize: 13 }}>
            按平均分降序 · 有正在评审的报告每 5 秒自动刷新
          </Text>
        </Space>
        <Space size={8}>
          {" "}
          <Tooltip
            title={
              total === 0
                ? "当前没有感悟报告评审记录，无法导出"
                : "一键导出全班诚信报告（每生一份 docx，打包为 zip）"
            }
          >
            <Button
              icon={<Download />}
              loading={batchExporting}
              disabled={total === 0 && !loading}
              onClick={handleBatchExport}
            >
              导出全班诚信报告
            </Button>
          </Tooltip>
          <Text type="secondary" style={{ fontSize: 13 }}>
            已选 <strong>{selectedIds.length}</strong> 条
          </Text>
          <Popconfirm
            title={`确定批量删除 ${selectedIds.length} 条评审记录？`}
            description="此操作不可撤销"
            okText="批量删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            disabled={selectedIds.length === 0 || batchDeleting}
            onConfirm={(e) => {
              e?.stopPropagation();
              handleBatchDelete();
            }}
            onCancel={(e) => e?.stopPropagation()}
          >
            <Button
              danger
              icon={<Trash2 />}
              loading={batchDeleting}
              disabled={selectedIds.length === 0}
              onClick={(e) => e.stopPropagation()}
            >
              批量删除
            </Button>
          </Popconfirm>
          <Button
            size="small"
            icon={<SquareMinus />}
            disabled={selectedIds.length === 0}
            onClick={() => setSelectedIds([])}
          >
            清空选择
          </Button>
          <Button icon={<RefreshCw />} onClick={() => fetchList(page)}>
            刷新
          </Button>
        </Space>
      </div>
      <Table
        dataSource={data}
        rowKey="id"
        loading={loading}
        className="pf-reflection-table"
        tableLayout="fixed"
        size="middle"
        virtual
        scroll={{ x: 1240, y: 640 }}
        rowSelection={{
          selectedRowKeys: selectedIds,
          onChange: (keys: React.Key[]) => setSelectedIds(keys.map(String)),
          getCheckboxProps: (record: ReflectionListItem) => ({
            disabled: ACTIVE_STATUSES.has(record.status),
            title: ACTIVE_STATUSES.has(record.status)
              ? `${record.paper_id} 正在评审中，无法勾选`
              : "勾选后可批量删除该条评审记录",
          }),
        }}
        pagination={{
          current: page,
          total,
          pageSize,
          onChange: (p) => setPage(p),
          showTotal: (t) => `共 ${t} 条`,
        }}
        onRow={(record) => ({
          onClick: () => navigate(`/reflection/result/${record.paper_id}`),
          style: { cursor: "pointer" },
        })}
        columns={columns}
        expandable={{
          expandIcon: ({ expanded, expandable, onExpand, record }) =>
            expandable ? (
              <button
                type="button"
                className="pf-reflection-expand-button"
                aria-label={expanded ? "收起评审依据" : "展开评审依据"}
                onClick={(event) => {
                  event.stopPropagation();
                  onExpand(record, event);
                }}
              >
                {expanded ? <ChevronDown /> : <ChevronRight />}
              </button>
            ) : (
              <span className="pf-reflection-expand-placeholder" aria-hidden="true" />
            ),
          rowExpandable: (record) =>
            record.status === "failed" ||
            record.status === "timed_out" ||
            (record.verdict_reason ?? "").length > 0,
          expandedRowRender: (record) => (
            <div
              style={{ padding: "8px 16px", background: "var(--pf-bg-secondary)", borderRadius: 6 }}
            >
              <Text type="secondary" style={{ fontSize: 12 }}>
                判决依据：
              </Text>
              <div style={{ fontSize: 13, marginTop: 4 }}>
                {record.verdict_reason || "（无说明）"}
              </div>
              {record.error_message && (
                <Text type="danger" style={{ fontSize: 12, marginTop: 8, display: "block" }}>
                  ⚠️ 错误：{record.error_message}
                </Text>
              )}
            </div>
          ),
        }}
      />
      <IntegrityReportModal
        paperId={integrityPaperId}
        open={integrityPaperId !== null}
        onClose={() => setIntegrityPaperId(null)}
      />
    </div>
  );
}
