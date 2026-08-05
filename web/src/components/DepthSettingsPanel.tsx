import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Button,
  Card,
  Collapse,
  Form,
  Input,
  Slider,
  Space,
  Typography,
  message,
} from "antd";

import { getDepthSettings, updateDepthSettings } from "@/api/depthSettings";
import type { DepthSettings } from "@/api/types";

const { Title, Text } = Typography;

interface FormValues {
  severityFallbackThreshold: number;
  severityFatalWeight: number;
  severityMinorWeight: number;
  q5cClaimSeverityFactor: number;
  claimValidationPenaltyPerClaim: number;
  claimValidationPenaltyMax: number;
  deltaDefaultMin: number;
  deltaDefaultMax: number;
  deltaBoundsOverrides: string;
}

function toFormValues(settings: DepthSettings): FormValues {
  return {
    ...settings,
    deltaBoundsOverrides: JSON.stringify(settings.deltaBoundsOverrides, null, 2),
  };
}

export default function DepthSettingsPanel() {
  const { t } = useTranslation();
  const [form] = Form.useForm<FormValues>();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setLoading(true);
    getDepthSettings()
      .then((settings) => {
        form.setFieldsValue(toFormValues(settings));
      })
      .catch(() => {
        message.error(t("depthSettings.fetchError", "获取 DEPTH 参数失败"));
      })
      .finally(() => {
        setLoading(false);
      });
  }, [form, t]);

  const handleSave = async (values: FormValues) => {
    let deltaBoundsOverrides: Record<string, { min: number; max: number }> = {};
    try {
      deltaBoundsOverrides = values.deltaBoundsOverrides
        ? JSON.parse(values.deltaBoundsOverrides)
        : {};
    } catch {
      message.error(t("depthSettings.invalidJson", "delta 覆盖 JSON 格式错误"));
      return;
    }

    const payload: Partial<DepthSettings> = {
      severityFallbackThreshold: values.severityFallbackThreshold,
      severityFatalWeight: values.severityFatalWeight,
      severityMinorWeight: values.severityMinorWeight,
      q5cClaimSeverityFactor: values.q5cClaimSeverityFactor,
      claimValidationPenaltyPerClaim: values.claimValidationPenaltyPerClaim,
      claimValidationPenaltyMax: values.claimValidationPenaltyMax,
      deltaDefaultMin: values.deltaDefaultMin,
      deltaDefaultMax: values.deltaDefaultMax,
      deltaBoundsOverrides,
    };

    setSaving(true);
    try {
      const updated = await updateDepthSettings(payload);
      form.setFieldsValue(toFormValues(updated));
      message.success(t("depthSettings.saveSuccess", "DEPTH 参数已保存"));
    } catch {
      // http 拦截器已提示错误
    } finally {
      setSaving(false);
    }
  };

  const sliderInput = (
    label: string,
    name: keyof FormValues,
    min: number,
    max: number,
    step: number,
    help?: string
  ) => (
    <Form.Item
      key={name}
      name={name}
      label={<Text strong>{label}</Text>}
      help={help}
      rules={[{ required: true, message: t("depthSettings.required", "必填") }]}
    >
      <Slider min={min} max={max} step={step} tooltip={{ open: true }} />
    </Form.Item>
  );

  return (
    <Card
      className="pf-glass-card"
      style={{ marginBottom: 24 }}
      title={
        <Space>
          <span role="img" aria-label="sliders">
            ⚙️
          </span>
          <span>{t("depthSettings.title", "DEPTH 参数调优")}</span>
        </Space>
      }
    >
      <Title level={5}>{t("depthSettings.severitySection", "严重度 / 惩罚")}</Title>
      <Form
        form={form}
        layout="vertical"
        onFinish={handleSave}
        initialValues={{
          severityFallbackThreshold: 0.5,
          severityFatalWeight: 1.0,
          severityMinorWeight: 0.25,
          q5cClaimSeverityFactor: 0.05,
          claimValidationPenaltyPerClaim: 0.1,
          claimValidationPenaltyMax: 0.3,
          deltaDefaultMin: -0.08,
          deltaDefaultMax: 0.12,
          deltaBoundsOverrides: "{}",
        }}
        disabled={loading}
      >
        {sliderInput(
          t("depthSettings.severityFallbackThreshold", "严重度回退阈值"),
          "severityFallbackThreshold",
          0,
          1,
          0.01,
          t("depthSettings.severityFallbackThresholdHelp", "无 ML 模型时 out-of-range claim 判 fatal 的相对偏差阈值")
        )}
        {sliderInput(
          t("depthSettings.severityFatalWeight", "fatal 权重"),
          "severityFatalWeight",
          0,
          5,
          0.05,
          t("depthSettings.severityFatalWeightHelp", "fatal 越界对 Q5c 惩罚因子的权重")
        )}
        {sliderInput(
          t("depthSettings.severityMinorWeight", "minor 权重"),
          "severityMinorWeight",
          0,
          5,
          0.05,
          t("depthSettings.severityMinorWeightHelp", "minor 越界对 Q5c 惩罚因子的权重")
        )}
        {sliderInput(
          t("depthSettings.q5cClaimSeverityFactor", "Q5c 惩罚系数"),
          "q5cClaimSeverityFactor",
          0,
          0.5,
          0.001,
          t("depthSettings.q5cClaimSeverityFactorHelp", "每条 severity 惩罚单位对 Q5c delta 下限的收紧系数")
        )}
        {sliderInput(
          t("depthSettings.claimValidationPenaltyPerClaim", "单条断言惩罚"),
          "claimValidationPenaltyPerClaim",
          0,
          1,
          0.01,
          t("depthSettings.claimValidationPenaltyPerClaimHelp", "单个 out-of-range claim 对 figure_consistency_score 的惩罚值")
        )}
        {sliderInput(
          t("depthSettings.claimValidationPenaltyMax", "惩罚上限"),
          "claimValidationPenaltyMax",
          0,
          1,
          0.01,
          t("depthSettings.claimValidationPenaltyMaxHelp", "figure_consistency_score 惩罚上限")
        )}

        <Title level={5} style={{ marginTop: 24 }}>
          {t("depthSettings.deltaSection", "Delta 区间")}
        </Title>
        {sliderInput(
          t("depthSettings.deltaDefaultMin", "默认 delta 下限"),
          "deltaDefaultMin",
          -0.25,
          0,
          0.001,
          t("depthSettings.deltaDefaultMinHelp", "Q5c 默认 delta 区间下限")
        )}
        {sliderInput(
          t("depthSettings.deltaDefaultMax", "默认 delta 上限"),
          "deltaDefaultMax",
          0,
          0.25,
          0.001,
          t("depthSettings.deltaDefaultMaxHelp", "Q5c 默认 delta 区间上限")
        )}

        <Collapse
          ghost
          items={[
            {
              key: "deltaBoundsOverrides",
              label: t("depthSettings.deltaBoundsOverrides", "按论文类型 / 算力模式覆盖"),
              children: (
                <Form.Item
                  name="deltaBoundsOverrides"
                  help={t(
                    "depthSettings.deltaBoundsOverridesHelp",
                    'JSON 格式，例如 {"A":{"min":-0.1,"max":0.15},"speed":{"min":-0.05,"max":0.08}}'
                  )}
                >
                  <Input.TextArea
                    rows={5}
                    style={{ fontFamily: "monospace" }}
                    placeholder='{"A":{"min":-0.1,"max":0.15}}'
                  />
                </Form.Item>
              ),
            },
          ]}
        />

        <Form.Item style={{ marginTop: 24 }}>
          <Button type="primary" htmlType="submit" loading={saving}>
            {t("depthSettings.save", "保存 DEPTH 参数")}
          </Button>
        </Form.Item>
      </Form>
    </Card>
  );
}
