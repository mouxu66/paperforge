import { Space, Tag, Typography } from "antd";
import type { DuplicateGroupPaper } from "@/api/types";

type FieldKey = "title" | "authors" | "year" | "abstract" | "journal" | "pdfUrl" | "tags";

interface FieldValueProps {
  field: FieldKey;
  paper: DuplicateGroupPaper;
}

export default function FieldValue({ field, paper }: FieldValueProps) {
  let value: React.ReactNode = "";
  switch (field) {
    case "title":
      value = paper.title || "-";
      break;
    case "authors":
      value = paper.authors?.join(", ") || "-";
      break;
    case "year":
      value = paper.year || "-";
      break;
    case "abstract":
      value = paper.abstract ? `${paper.abstract.slice(0, 120)}...` : "-";
      break;
    case "journal":
      value = paper.journal || "-";
      break;
    case "pdfUrl":
      value = paper.pdfUrl ? (
        <a href={paper.pdfUrl} target="_blank" rel="noreferrer">
          {paper.pdfUrl.slice(0, 40)}...
        </a>
      ) : (
        "-"
      );
      break;
    case "tags":
      value =
        paper.tags && paper.tags.length > 0 ? (
          <Space size={[0, 4]} wrap>
            {paper.tags.map((tag) => (
              <Tag key={tag}>{tag}</Tag>
            ))}
          </Space>
        ) : (
          "-"
        );
      break;
  }
  return <Typography.Text style={{ fontSize: 12 }}>{value}</Typography.Text>;
}
