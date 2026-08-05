import { Filter, Send } from "lucide-react";
import { Card, Select, Input, Button, Tag } from "antd";

import { useTranslation } from "react-i18next";
import type { Paper } from "@/api/types";

const { TextArea } = Input;

interface AskInputProps {
  query: string;
  onQueryChange: (v: string) => void;
  loading: boolean;
  onSubmit: () => void;
  paperOptions: Paper[];
  selectedIds: string[];
  onSelectedIdsChange: (ids: string[]) => void;
}

/** 提问输入区：论文范围选择 + 文本框 + 提交按钮 */
export default function AskInput({
  query,
  onQueryChange,
  loading,
  onSubmit,
  paperOptions,
  selectedIds,
  onSelectedIdsChange,
}: AskInputProps) {
  const { t } = useTranslation();
  // Select 选项：标题 + 年份，便于搜索定位
  const selectOptions = paperOptions.map((p) => ({
    value: p.id,
    label: `${p.title} (${p.year})`,
  }));

  return (
    <Card className="pf-glass-card pf-ask-input" variant="borderless" style={{ marginBottom: 20, padding: 8 }}>
      {/* 论文范围选择 */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8, display: "flex", alignItems: "center", gap: 8 }}>
          <Filter style={{ color: "var(--pf-primary)", fontSize: 13 }} />
          <span style={{ fontSize: 13, fontWeight: 500, color: "var(--pf-text-secondary)" }}>
            {t("ask.paperScope")}
          </span>
          <Tag
            color={selectedIds.length > 0 ? "blue" : "default"}
            style={{ marginInlineStart: 0, fontSize: 12 }}
          >
            {selectedIds.length > 0
              ? t("ask.selectedCount", { count: selectedIds.length })
              : t("ask.allPapers")}
          </Tag>
        </div>
        <Select
          mode="multiple"
          showSearch
          allowClear
          optionFilterProp="label"
          aria-label={t("ask.paperScope")}
          placeholder={t("ask.searchPaperPlaceholder")}
          value={selectedIds}
          onChange={onSelectedIdsChange}
          options={selectOptions}
          maxTagCount={3}
          style={{ width: "100%" }}
          size="large"
        />
      </div>

      <div className="pf-ask-input-label">
        <span className="pf-ask-input-pulse" aria-hidden="true" />
        <span>{t("ask.submit")}</span>
        <span className="pf-ask-input-mode">RAG</span>
      </div>
      <TextArea
        aria-label={t("ask.inputPlaceholder")}
        value={query}
        onChange={(e) => onQueryChange(e.target.value)}
        placeholder={t("ask.inputPlaceholder")}
        autoSize={{ minRows: 3, maxRows: 6 }}
        onPressEnter={(e) => {
          if (!e.shiftKey) {
            e.preventDefault();
            onSubmit();
          }
        }}
        style={{ borderRadius: 8, resize: "none" }}
      />
      <div
        style={{
          marginTop: 12,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }}>
          {t("ask.inputHint")}
        </span>
        <Button type="primary" icon={<Send />} onClick={onSubmit} loading={loading}>
          {t("ask.submit")}
        </Button>
      </div>
    </Card>
  );
}
