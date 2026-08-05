import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Empty, List, Skeleton, Typography } from "antd";
import { listPdfAnnotations } from "@/api/pdfAnnotations";
import type { PdfAnnotation } from "@/api/types";

interface PdfAnnotationsTabProps {
  paperId: string;
  refreshKey?: number;
}

export default function PdfAnnotationsTab({ paperId, refreshKey = 0 }: PdfAnnotationsTabProps) {
  const { t } = useTranslation();
  const [annotations, setAnnotations] = useState<PdfAnnotation[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    listPdfAnnotations(paperId)
      .then((items) => {
        if (!active) return;
        setAnnotations(items);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [paperId, refreshKey]);

  if (loading) {
    return (
      <div data-testid="pdf-annotations-tab">
        <Skeleton active paragraph={{ rows: 4 }} />
      </div>
    );
  }

  if (!annotations.length) {
    return (
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t("paper.noAnnotations", "暂无 PDF 批注，点击「提取批注」开始提取")}
      />
    );
  }

  return (
    <div data-testid="pdf-annotations-tab">
      <List
        dataSource={annotations}
        renderItem={(item) => (
          <List.Item
            style={{
              borderLeft: `4px solid ${item.color || "#d9d9d9"}`,
              paddingLeft: 12,
              marginBottom: 8,
              background: "var(--pf-bg-tertiary)",
              borderRadius: 8,
            }}
          >
            <List.Item.Meta
              title={
                <Typography.Text strong>
                  {t("paper.annotationPage", "第 {{page}} 页", { page: item.page })}
                </Typography.Text>
              }
              description={
                <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                  {item.note || t("paper.emptyAnnotation", "（无文本内容）")}
                </div>
              }
            />
            <div style={{ fontSize: 12, color: "var(--pf-text-placeholder)" }}>
              {new Date(item.createdAt).toLocaleString()}
            </div>
          </List.Item>
        )}
      />
    </div>
  );
}
