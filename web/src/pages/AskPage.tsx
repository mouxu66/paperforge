import { Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, Card, App } from "antd";

import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { askPaperStream } from "@/api/ask";
import { fetchPapers } from "@/api/papers";
import type { AskResponse, AskReference, Paper } from "@/api/types";
import { useModelStore } from "@/store/useModelStore";
import AskHistorySidebar from "@/components/AskHistorySidebar";
import PageHeader from "@/components/PageHeader";
import AskInput from "@/components/AskInput";
import AskResult from "@/components/AskResult";
import { useHistoryStore, type HistoryItem } from "@/store/useHistoryStore";

export default function AskPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<AskResponse | null>(null);
  // 论文范围限定
  const [paperOptions, setPaperOptions] = useState<Paper[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  // 历史记录当前选中项
  const [activeHistoryId, setActiveHistoryId] = useState<string | null>(null);
  // 流式问答状态
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [streamingRefs, setStreamingRefs] = useState<AskReference[]>([]);
  const streamingTextRef = useRef("");
  const streamingRefsRef = useRef<AskReference[]>([]);

  const historyItems = useHistoryStore((s) => s.items);
  const addHistory = useHistoryStore((s) => s.add);
  const removeHistory = useHistoryStore((s) => s.remove);
  const clearHistory = useHistoryStore((s) => s.clear);
  // 模型可用性：available 为空即未配置任何启用模型。
  const modelAvailable = useModelStore((s) => s.available);
  const modelCurrent = useModelStore((s) => s.current);
  const modelLoad = useModelStore((s) => s.load);
  const hasModel = (modelAvailable?.length ?? 0) > 0 || !!modelCurrent;

  useEffect(() => {
    // 进入页时拉取最新模型状态，避免使用过期缓存误判「已配置」。
    void modelLoad();
  }, [modelLoad]);

  // 挂载时拉取全部论文，供范围选择下拉使用
  useEffect(() => {
    fetchPapers({ page: 1, pageSize: 1000 })
      .then((res) => setPaperOptions(res.items))
      .catch(() => {
        // 错误已在 client.ts 拦截器统一提示，此处静默
      });
  }, []);

  // 实际发起流式问答的内部实现（先于 handleSubmit 声明，避免 TDZ 困惑）。
  const doSubmit = async () => {
    const q = query.trim();
    if (!q || loading || streaming) return;
    setLoading(true);
    setResult(null);
    setStreaming(false);
    setStreamingText("");
    setStreamingRefs([]);
    streamingTextRef.current = "";
    streamingRefsRef.current = [];
    setActiveHistoryId(null);
    try {
      await askPaperStream(
        {
          question: q,
          paperIds: selectedIds.length > 0 ? selectedIds : undefined,
        },
        {
          onRefs: (refs) => {
            setLoading(false);
            setStreaming(true);
            setStreamingRefs(refs);
            streamingRefsRef.current = refs;
          },
          onToken: (token) => {
            streamingTextRef.current += token;
            setStreamingText(streamingTextRef.current);
          },
          onError: (error) => {
            setStreaming(false);
            setLoading(false);
            // ask.ts 对前端原产错误传 i18n key（streamTimeout / streamUnexpectedEnd），
            // 对后端错误传原始 detail 文案。这里只在 key 命中时翻译，否则原样显示。
            const knownKeys = ["streamTimeout", "streamUnexpectedEnd", "streamAborted"];
            const friendly = knownKeys.includes(error) ? t(`ask.${error}`) : error;
            message.error(friendly);
          },
          onDone: () => {
            setStreaming(false);
            const finalResult: AskResponse = {
              answer: streamingTextRef.current,
              references: streamingRefsRef.current,
            };
            setResult(finalResult);
            addHistory({
              query: q,
              answer: finalResult.answer,
              references: finalResult.references,
              timestamp: Date.now(),
              selectedIds,
            });
          },
        },
        { timeoutMs: 180_000 },
      );
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      const detail = e?.message || t("ask.askFailed");
      message.error(detail);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async () => {
    const q = query.trim();
    if (!q || loading || streaming) return;
    // P1 守卫：未配置模型时给出明确提示，而不是静默发起一个注定 502 的流式请求。
    if (!hasModel) {
      modal.confirm({
        title: t("ask.noModelTitle"),
        content: t("ask.noModelDesc"),
        okText: t("ask.goToModels"),
        cancelText: t("ask.continueWithoutModel"),
        onOk: () => navigate("/models"),
        onCancel: () => {
          // 用户选择「仍要提问」则照常发起（后端会返回 502，错误信息会显示）
          void doSubmit();
        },
      });
      return;
    }
    await doSubmit();
  };

  // 点击历史项：回填 query + 渲染 answer/references + 回填论文范围 + 高亮该项
  const handleHistoryClick = (item: HistoryItem) => {
    setQuery(item.query);
    setResult({ answer: item.answer, references: item.references });
    setSelectedIds(item.selectedIds || []);
    setActiveHistoryId(item.id);
  };

  const handleClearAll = () => {
    modal.confirm({
      title: t("ask.clearHistoryTitle"),
      content: t("ask.clearHistoryContent"),
      okText: t("ask.clearHistoryOk"),
      okType: "danger",
      cancelText: t("ask.cancel"),
      onOk: () => {
        clearHistory();
        setActiveHistoryId(null);
      },
    });
  };

  // 删除单条历史：若删除的是当前选中项，则取消高亮
  const handleRemoveItem = (id: string) => {
    removeHistory(id);
    if (activeHistoryId === id) setActiveHistoryId(null);
  };

  return (
    <div className="pf-ask-workspace" style={{ display: "flex", gap: 20, maxWidth: 1180, margin: "0 auto" }}>
      {/* 历史对话侧边栏 */}
      <div style={{ width: 260, flexShrink: 0 }}>
        <AskHistorySidebar
          items={historyItems}
          activeHistoryId={activeHistoryId}
          onSelect={handleHistoryClick}
          onRemoveItem={handleRemoveItem}
          onClearAll={handleClearAll}
        />
      </div>

      {/* 主内容区 */}
      <div className="pf-ask-main" style={{ flex: 1, maxWidth: 900 }}>
        <PageHeader title={t("ask.title")} description={t("ask.subtitle")} />

        {/* 提问输入区 */}
        <AskInput
          query={query}
          onQueryChange={setQuery}
          loading={loading}
          onSubmit={handleSubmit}
          paperOptions={paperOptions}
          selectedIds={selectedIds}
          onSelectedIdsChange={setSelectedIds}
        />

        {/* 未配置模型时的内联提示（不阻断提问，但给出明确引导） */}
        {!hasModel && !loading && !streaming && !result && (
          <Card
            className="pf-glass-card"
            variant="borderless"
            style={{ marginBottom: 20, padding: 16 }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
              <Zap style={{ color: "var(--pf-text-placeholder)", fontSize: 20 }} />
              <div style={{ flex: 1, minWidth: 240 }}>
                <div
                  className="pf-serif"
                  style={{ fontSize: 15, fontWeight: 600, color: "var(--pf-text-primary)" }}
                >
                  {t("ask.noModelTitle")}
                </div>
                <div style={{ fontSize: 13, color: "var(--pf-text-muted)", marginTop: 2 }}>
                  {t("ask.noModelDesc")}
                </div>
              </div>
              <Button
                type="primary"
                icon={<Zap />}
                onClick={() => navigate("/models")}
              >
                {t("ask.goToModels")}
              </Button>
            </div>
          </Card>
        )}

        {/* 结果展示区 */}
        <AskResult
          result={result}
          loading={loading}
          onNavigate={navigate}
          streaming={streaming}
          streamingText={streamingText}
          streamingRefs={streamingRefs}
        />
      </div>
    </div>
  );
}
