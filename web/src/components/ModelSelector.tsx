import { Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Select, Tooltip, message } from "antd";

import { useTranslation } from "react-i18next";
import { useModelStore } from "@/store/useModelStore";

/**
 * 模型切换下拉菜单。
 *
 * - 挂载时拉取后端可用模型列表 + 当前模型。
 * - 切换时按钮 loading，文案变为"模型切换中，请稍候..."。
 * - 切换失败回退到上一个模型并提示错误（回退逻辑在 store 内完成）。
 * - 切换成功后触发短暂高亮动画（pf-just-switched）。
 */
export default function ModelSelector() {
  const { t } = useTranslation();
  const { current, available, enabled, switching, load, switchModel } = useModelStore();
  const [justSwitched, setJustSwitched] = useState(false);
  const glowTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    load();
  }, [load]);

  // 卸载时清理定时器，避免设置状态到已卸载组件
  useEffect(() => {
    return () => {
      if (glowTimer.current) clearTimeout(glowTimer.current);
    };
  }, []);

  // 功能未启用时不渲染
  if (enabled === false) return null;

  const triggerGlow = () => {
    setJustSwitched(true);
    if (glowTimer.current) clearTimeout(glowTimer.current);
    glowTimer.current = setTimeout(() => setJustSwitched(false), 1200);
  };

  const handleChange = async (value: string) => {
    try {
      const ok = await switchModel(value);
      if (ok) {
        message.success(t("models.modelSwitched", { label: useModelStore.getState().label }));
        triggerGlow();
      }
    } catch {
      // store 已回退到上一个模型，这里只提示
      message.error(t("models.modelSwitchFailed"));
    }
  };

  const options = available.map((m) => ({
    value: m.value,
    label: (
      <span style={{ fontWeight: m.value === current ? 600 : 400 }}>
        {m.label}
        {m.value === current && (
          <span style={{ color: "var(--pf-primary)" }}>{t("models.current")}</span>
        )}
      </span>
    ),
  }));

  // 组合 className：基础过渡 + 切换中透明 + 成功后高亮
  const cls = [
    "pf-model-selector",
    switching ? "pf-switching" : "",
    justSwitched ? "pf-just-switched" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Tooltip title={t("models.switchTooltip")}>
      <Select
        value={current}
        onChange={handleChange}
        loading={switching}
        size="middle"
        className={cls}
        suffixIcon={<Zap style={{ color: "var(--pf-primary)" }} />}
        style={{ minWidth: 180 }}
        placeholder={switching ? t("models.switching") : t("models.selectModel")}
        options={options}
      />
    </Tooltip>
  );
}
