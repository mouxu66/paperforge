import { Merge, Home } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import {
  Alert,
  Breadcrumb,
  Button,
  Card,
  Checkbox,
  Empty,
  Radio,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from "antd";

import {
  useDuplicateGroups,
  makeDefaultSelection,
  groupKey,
  FIELD_KEYS,
} from "@/hooks/useDuplicateGroups";
import FieldValue from "@/components/duplicate/FieldValue";
import PageHeader from "@/components/PageHeader";

const { Text } = Typography;

export default function DuplicatePapersPage() {
  const { t } = useTranslation();
  const {
    groups,
    loading,
    mergingKey,
    selections,
    handleMerge,
    setTarget,
    setFieldSource,
    setAllFieldsFromPaper,
  } = useDuplicateGroups();

  if (loading) {
    return (
      <div style={{ padding: 40, textAlign: "center" }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="pf-page-wide" style={{ paddingBottom: 24 }}>
      <Breadcrumb
        style={{ marginBottom: 16 }}
        items={[
          {
            title: (
              <Link to="/">
                <Home />
              </Link>
            ),
          },
          {
            title: t("duplicates.title"),
          },
        ]}
      />

      <PageHeader title={t("duplicates.title")} description={t("duplicates.subtitle")} />
      <Alert
        type="info"
        showIcon
        title={t("duplicates.hintTitle")}
        description={t("duplicates.hintDescription")}
        style={{ marginBottom: 24 }}
      />

      {groups.length === 0 ? (
        <Empty description={t("duplicates.noDuplicates")} />
      ) : (
        <Space orientation="vertical" size="large" style={{ width: "100%" }}>
          {groups.map((group, idx) => {
            const key = groupKey(group);
            const sel = selections[key] ?? makeDefaultSelection(group);

            const columns = [
              {
                title: t("duplicates.field"),
                dataIndex: "field",
                width: 140,
              },
              ...group.papers.map((p) => ({
                title: (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <span>
                      {t("duplicates.paperColumn", { index: group.papers.indexOf(p) + 1 })}
                    </span>
                    <Button
                      type="link"
                      size="small"
                      style={{ padding: 0, fontSize: 11, lineHeight: 1.2 }}
                      onClick={() => setAllFieldsFromPaper(key, p.id)}
                    >
                      {t("duplicates.useAllFields", "Use all fields")}
                    </Button>
                  </div>
                ),
                dataIndex: p.id,
                key: p.id,
              })),
            ];

            const dataSource: Record<string, unknown>[] = [];

            // Target selection row
            dataSource.push({
              key: "target",
              field: (
                <Text strong style={{ color: "#1677ff" }}>
                  {t("duplicates.target")}
                </Text>
              ),
              ...group.papers.reduce(
                (acc, p) => {
                  acc[p.id] = (
                    <Radio checked={sel.target === p.id} onChange={() => setTarget(key, p.id)}>
                      {t("duplicates.useAsTarget")}
                    </Radio>
                  );
                  return acc;
                },
                {} as Record<string, React.ReactNode>,
              ),
            });

            // Field rows
            FIELD_KEYS.forEach((field) => {
              const row: Record<string, unknown> = {
                key: field,
                field: t(`duplicates.fields.${field}`),
              };
              group.papers.forEach((p) => {
                row[p.id] = (
                  <div style={{ padding: "4px 0" }}>
                    <Checkbox
                      checked={sel.fields[field] === p.id}
                      onChange={() => setFieldSource(key, field, p.id)}
                    />
                    <div style={{ marginTop: 4 }}>
                      <FieldValue field={field} paper={p} />
                    </div>
                  </div>
                );
              });
              dataSource.push(row);
            });

            return (
              <Card
                key={key}
                title={
                  <Space>
                    <Merge />
                    <span>{t("duplicates.groupTitle", { index: idx + 1 })}</span>
                    <Tag>{t("duplicates.paperCount", { count: group.papers.length })}</Tag>
                  </Space>
                }
                extra={
                  <Button
                    type="primary"
                    icon={<Merge />}
                    loading={mergingKey === key}
                    onClick={() => handleMerge(group, key)}
                  >
                    {t("duplicates.merge")}
                  </Button>
                }
              >
                <Table
                  size="small"
                  pagination={false}
                  rowKey="key"
                  dataSource={dataSource}
                  columns={columns}
                />
              </Card>
            );
          })}
        </Space>
      )}
    </div>
  );
}
