import { Monitor, Webhook, CheckCircle } from "lucide-react";
import { useState } from "react";
import { Card, Button, Space, Tag, Tooltip, message } from "antd";

import { useTranslation } from "react-i18next";
import type { FormInstance } from "antd";

/**
 * 模型预设模板 + 本地端口检测（P1：首次使用模型配置）。
 *
 * 设计目标：让非技术用户不必知道 API URL / Model ID 该填什么，
 * 点一下预设或「检测本地端口」即可把表单填好，再去「添加模型」保存。
 *
 * 与 AddModelModal 的关系：父组件把 Form 实例传进来，
 * 本组件通过 form.setFieldsValue 一键填入预设/探测结果。
 */

interface PresetValues {
  displayName: string;
  apiUrl: string;
  modelId: string;
  /** 是否需要 API Key（云端为 true，本地为 false） */
  needsKey: boolean;
  /** i18n 文案 key（models.presetXxx） */
  key: string;
}

const PRESETS: PresetValues[] = [
  {
    displayName: "OpenAI GPT-4o",
    apiUrl: "https://api.openai.com/v1",
    modelId: "gpt-4o",
    needsKey: true,
    key: "models.presetOpenai",
  },
  {
    displayName: "DeepSeek",
    apiUrl: "https://api.deepseek.com/v1",
    modelId: "deepseek-chat",
    needsKey: true,
    key: "models.presetDeepseek",
  },
  {
    displayName: "Zhipu GLM-4",
    apiUrl: "https://open.bigmodel.cn/api/paas/v4",
    modelId: "glm-4",
    needsKey: true,
    key: "models.presetZhipu",
  },
  {
    displayName: "llama.cpp (local)",
    apiUrl: "http://127.0.0.1:8080/v1",
    modelId: "local-model",
    needsKey: false,
    key: "models.presetLlamaCpp",
  },
  {
    displayName: "Ollama (local)",
    apiUrl: "http://127.0.0.1:11434/v1",
    modelId: "llama3.1",
    needsKey: false,
    key: "models.presetOllama",
  },
  {
    displayName: "LM Studio (local)",
    apiUrl: "http://127.0.0.1:1234/v1",
    modelId: "local-model",
    needsKey: false,
    key: "models.presetLmStudio",
  },
];

/** 本地推理服务探测目标：常见端口 + 探测路径。 */
const LOCAL_PROBES: { url: string; name: string; key: string }[] = [
  { url: "http://127.0.0.1:8080/v1", name: "llama.cpp", key: "models.presetLlamaCpp" },
  { url: "http://127.0.0.1:11434/v1", name: "Ollama", key: "models.presetOllama" },
  { url: "http://127.0.0.1:1234/v1", name: "LM Studio", key: "models.presetLmStudio" },
  { url: "http://127.0.0.1:5000/v1", name: "vLLM / other", key: "models.presetLmStudio" },
];

interface Props {
  form: FormInstance;
  /** 选择预设或探测命中后是否立即打开 Add 弹窗。默认 true。 */
  onPicked?: () => void;
}

/**
 * 前端探测本地推理服务：请求 /v1/models 端点，1.5s 超时。
 * 跨域时浏览器可能拦截，但本地推理服务通常允许 CORS（llama.cpp/Ollama 默认开）。
 * 失败不抛错，仅返回 null。
 */
async function probeLocalService(url: string, timeoutMs = 1500): Promise<string | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    // /v1/models 是 OpenAI 兼容标准端点；探测到即说明服务在线
    const resp = await fetch(`${url}/models`, {
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
    });
    if (!resp.ok) return null;
    const data = await resp.json().catch(() => null);
    // 尝试从返回里取出真实 model id（OpenAI 兼容格式：{data:[{id,...}]}）
    const firstId = data?.data?.[0]?.id || data?.models?.[0]?.id || data?.id || null;
    return typeof firstId === "string" && firstId.length > 0 ? firstId : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export default function ModelPresets({ form, onPicked }: Props) {
  const { t } = useTranslation();
  const [detecting, setDetecting] = useState(false);

  /** 应用一个预设到表单。 */
  const applyPreset = (p: PresetValues) => {
    form.setFieldsValue({
      displayName: p.displayName,
      apiUrl: p.apiUrl,
      apiKey: "",
      modelId: p.modelId,
      enabled: true,
    });
    message.info(t(p.key) + " · " + p.apiUrl);
    onPicked?.();
  };

  /** 探测本地推理服务：并发探测多个端口，命中第一个即填入表单。 */
  const handleDetect = async () => {
    setDetecting(true);
    message.loading({ content: t("models.detecting"), key: "detect", duration: 0 });
    try {
      const results = await Promise.all(
        LOCAL_PROBES.map(async (probe) => {
          const modelId = await probeLocalService(probe.url);
          return { ...probe, modelId };
        }),
      );
      const hit = results.find((r) => r.modelId !== null);
      message.destroy("detect");
      if (hit) {
        form.setFieldsValue({
          displayName: hit.name,
          apiUrl: hit.url,
          apiKey: "",
          modelId: hit.modelId,
          enabled: true,
        });
        message.success(t("models.detectFound", { name: hit.name, url: hit.url }));
        onPicked?.();
      } else {
        message.warning(t("models.detectNotFound"));
      }
    } catch (err0: unknown) {
      const e = err0 as Error & { response?: { status?: number; data?: { detail?: string } } };
      message.destroy("detect");
      message.error(t("models.detectError", { error: e?.message || String(e) }));
    } finally {
      setDetecting(false);
    }
  };

  return (
    <Card
      className="pf-glass-card"
      variant="borderless"
      style={{ marginBottom: 20, padding: 8 }}
      title={
        <Space>
          <Webhook style={{ color: "var(--pf-primary)" }} />
          <span className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
            {t("models.presetsTitle")}
          </span>
        </Space>
      }
      extra={
        <Tooltip title={t("models.detectLocal")}>
          <Button
            size="small"
            icon={<Monitor />}
            loading={detecting}
            onClick={handleDetect}
          >
            {t("models.detectLocal")}
          </Button>
        </Tooltip>
      }
    >
      <div
        style={{
          fontSize: 13,
          color: "var(--pf-text-muted)",
          marginBottom: 12,
          lineHeight: 1.6,
        }}
      >
        {t("models.emptyHint")}
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
          gap: 12,
        }}
      >
        {PRESETS.map((p) => {
          const isLocal = !p.needsKey;
          return (
            <Button
              key={p.apiUrl}
              block
              onClick={() => applyPreset(p)}
              style={{
                height: "auto",
                padding: "12px 14px",
                textAlign: "left",
                display: "flex",
                alignItems: "center",
                gap: 10,
                whiteSpace: "normal",
              }}
            >
              <CheckCircle
                style={{ color: isLocal ? "var(--pf-success)" : "var(--pf-primary)" }}
              />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "var(--pf-text-primary)",
                  }}
                >
                  {t(p.key)}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--pf-text-placeholder)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {p.apiUrl}
                </div>
              </div>
              <Tag
                color={isLocal ? "default" : "blue"}
                style={{ marginInlineEnd: 0, fontSize: 10 }}
              >
                {isLocal ? "Local" : "Cloud"}
              </Tag>
            </Button>
          );
        })}
      </div>
    </Card>
  );
}
