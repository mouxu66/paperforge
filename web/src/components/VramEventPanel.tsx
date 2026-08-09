import { CheckCircle, Clock, XCircle, Trash2, Loader, RefreshCw, ArrowLeftRight } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Card, Empty, Space, Tag, Tooltip, Typography } from "antd";

import { clearQwenEvents, getQwenEvents, type VramEvent as VramEventType } from "@/api/qwen";
import { List } from "@/components/CompatList";

const { Text } = Typography;

const POLL_INTERVAL = 3000;

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

function eventIcon(kind: string) {
  if (kind === "qwen_ready" || kind === "ready") {
    return <CheckCircle style={{ color: "#52c41a" }} />;
  }
  if (kind === "ocr_active") {
    return <Loader style={{ color: "#faad14" }} />;
  }
  if (kind === "switch") {
    return <ArrowLeftRight style={{ color: "#1890ff" }} />;
  }
  if (kind === "qwen_failed") {
    return <XCircle style={{ color: "#f5222d" }} />;
  }
  return <Clock style={{ color: "#8c8c8c" }} />;
}

function eventColor(kind: string): string {
  if (kind === "qwen_ready" || kind === "ready") return "success";
  if (kind === "ocr_active") return "warning";
  if (kind === "switch") return "processing";
  if (kind === "qwen_failed") return "error";
  return "default";
}

function eventKindKey(kind: string): string {
  const map: Record<string, string> = {
    qwen_ready: "settings.vramEventKind.qwenReady",
    ready: "settings.vramEventKind.ready",
    ocr_active: "settings.vramEventKind.ocrActive",
    switch: "settings.vramEventKind.switch",
    qwen_failed: "settings.vramEventKind.qwenFailed",
    qwen_shutdown: "settings.vramEventKind.qwenShutdown",
    qwen_starting: "settings.vramEventKind.qwenStarting",
    ocr_finished: "settings.vramEventKind.ocrFinished",
  };
  return map[kind] || "settings.vramEventKind.unknown";
}

/**
 * 显存调度事件面板：展示 llama-server / OCR 切换历史。
 */
export default function VramEventPanel() {
  const { t } = useTranslation();
  const [events, setEvents] = useState<VramEventType[]>([]);
  const [initialized, setInitialized] = useState(false);
  const timerRef = useRef<number | null>(null);

  const fetchEvents = useCallback(async () => {
    try {
      const { events } = await getQwenEvents(20);
      setEvents(events);
    } catch (err) {

      console.error("Failed to fetch VRAM events", err);
    } finally {
      setInitialized(true);
    }
  }, []);

  const handleRefresh = useCallback(() => {
    fetchEvents();
  }, [fetchEvents]);

  const handleClear = useCallback(async () => {
    try {
      await clearQwenEvents();
      setEvents([]);
    } catch (err) {

      console.error("Failed to clear VRAM events", err);
    }
  }, []);

  useEffect(() => {
    fetchEvents();
    timerRef.current = window.setInterval(fetchEvents, POLL_INTERVAL);
    return () => {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
      }
    };
  }, [fetchEvents]);

  return (
    <Card
      className="pf-glass-card"
      title={t("settings.vramEventPanelTitle")}
      loading={!initialized}
      extra={
        <Space>
          <Tooltip title={t("common.refresh")}>
            <Button
              type="text"
              size="small"
              icon={<RefreshCw />}
              onClick={handleRefresh}
              aria-label={t("common.refresh")}
            />
          </Tooltip>
          <Tooltip title={t("common.clear")}>
            <Button
              type="text"
              size="small"
              icon={<Trash2 />}
              onClick={handleClear}
              aria-label={t("common.clear")}
            />
          </Tooltip>
        </Space>
      }
    >
      {events.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t("settings.vramEventEmpty")}
        />
      ) : (
        <List
          rowKey={(item) => `${item.ts}-${item.kind}-${item.message}`}
          size="small"
          style={{ maxHeight: 320, overflow: "auto" }}
          dataSource={events}
          renderItem={(item) => (
            <List.Item>
              <div style={{ width: "100%" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  {eventIcon(item.kind)}
                  <Tag color={eventColor(item.kind)}>{t(eventKindKey(item.kind))}</Tag>
                  <Text type="secondary" style={{ fontSize: 12, marginLeft: "auto" }}>
                    {formatTime(item.ts)}
                  </Text>
                </div>
                <Text style={{ fontSize: 13 }}>{item.message}</Text>
              </div>
            </List.Item>
          )}
        />
      )}
    </Card>
  );
}
