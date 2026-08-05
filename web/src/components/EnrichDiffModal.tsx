import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Modal, Table, Tag, Button, Space, Empty, Typography } from "antd";
import type { Paper } from "@/api/types";

interface EnrichDiffModalProps {
  open: boolean;
  original: Paper | null;
  enriched: Paper | null;
  loading?: boolean;
  onAccept: () => void;
  onCancel: () => void;
}

interface DiffRow {
  key: string;
  field: string;
  oldValue: string;
  newValue: string;
  changed: boolean;
}

const FIELD_LABELS: Record<string, string> = {
  title: "paper.title",
  authors: "paper.authors",
  year: "paper.year",
  abstract: "paper.abstract",
  journal: "paper.journal",
  citations: "paper.citations",
  influentialCitations: "paper.influentialCitations",
  fieldsOfStudy: "paper.fieldsOfStudy",
};

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (Array.isArray(value)) {
    if (value.length === 0) return "-";
    return value.join(", ");
  }
  return String(value);
}

function arraysEqual(a: unknown[] | null | undefined, b: unknown[] | null | undefined): boolean {
  const aArr = Array.isArray(a) ? a : [];
  const bArr = Array.isArray(b) ? b : [];
  if (aArr.length !== bArr.length) return false;
  return aArr.every((item, index) => item === bArr[index]);
}

function isChanged(field: keyof Paper, original: Paper | null, enriched: Paper | null): boolean {
  if (!original || !enriched) return false;
  const a = original[field];
  const b = enriched[field];
  if (Array.isArray(a) || Array.isArray(b)) {
    return !arraysEqual(a as unknown[], b as unknown[]);
  }
  return a !== b;
}

export default function EnrichDiffModal({
  open,
  original,
  enriched,
  loading,
  onAccept,
  onCancel,
}: EnrichDiffModalProps) {
  const { t } = useTranslation();

  const diffRows = useMemo<DiffRow[]>(() => {
    if (!original || !enriched) return [];
    const fields: (keyof Paper)[] = [
      "title",
      "authors",
      "year",
      "abstract",
      "journal",
      "citations",
      "influentialCitations",
      "fieldsOfStudy",
    ];
    return fields
      .map((field) => ({
        key: field,
        field: t(FIELD_LABELS[field] || field, field),
        oldValue: formatValue(original[field]),
        newValue: formatValue(enriched[field]),
        changed: isChanged(field, original, enriched),
      }))
      .filter((row) => row.changed);
  }, [original, enriched, t]);

  const hasChanges = diffRows.some((row) => row.changed);

  return (
    <Modal
      open={open}
      title={t("paper.enrichMetadataTitle", "Review Metadata Changes")}
      onCancel={onCancel}
      width={720}
      footer={
        <Space>
          <Button onClick={onCancel}>{t("common.cancel")}</Button>
          <Button type="primary" loading={loading} onClick={onAccept} disabled={!hasChanges}>
            {t("paper.acceptChanges", "Accept Updates")}
          </Button>
        </Space>
      }
    >
      {diffRows.length === 0 ? (
        <Empty description={t("paper.noChangesFound", "No new metadata found.")} />
      ) : (
        <Table<DiffRow>
          dataSource={diffRows}
          pagination={false}
          size="small"
          columns={[
            {
              title: t("paper.field", "Field"),
              dataIndex: "field",
              key: "field",
              width: 140,
            },
            {
              title: t("paper.oldValue", "Original"),
              dataIndex: "oldValue",
              key: "oldValue",
              render: (value: string, record: DiffRow) => (
                <Typography.Text
                  delete={record.changed}
                  type={record.changed ? "secondary" : undefined}
                  style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
                >
                  {value}
                </Typography.Text>
              ),
            },
            {
              title: t("paper.newValue", "Enriched"),
              dataIndex: "newValue",
              key: "newValue",
              render: (value: string, record: DiffRow) => (
                <Typography.Text style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {record.changed ? (
                    <Tag color="green" style={{ marginRight: 8 }}>
                      {t("common.updated", "Updated")}
                    </Tag>
                  ) : null}
                  {value}
                </Typography.Text>
              ),
            },
          ]}
        />
      )}
    </Modal>
  );
}
