import { Typography } from "antd";

import { useTranslation } from "react-i18next";
import type { Paper } from "@/api/types";
import { formatSize } from "@/utils/format";

const { Title } = Typography;

interface AbstractTabProps {
  paper: Paper;
}

/**
 * 摘要详情面板：档案字段（一行） + 摘要正文。
 *
 * 此前这里是一张 8 格 `Descriptions` 表（作者/期刊/年份/来源/引用/ID/文本块/索引大小），
 * 但其中 **作者、年份、来源、引用 4 项在标题卡里已经展示过**——
 * 标题卡有作者行与 `SOURCE / year / citations` 三枚 Tag。重复列一遍既占高度，
 * 又让页面读起来像数据库字段面板。
 *
 * 现在只保留标题卡没有的档案字段，压成一行等宽小字，摘要正文因此获得完整阅读宽度。
 */
export default function AbstractTab({ paper }: AbstractTabProps) {
  const { t } = useTranslation();

  const spec: Array<{ key: string; label: string; value: string; mono?: boolean }> = [
    { key: "journal", label: t("paper.journal"), value: paper.journal || "arXiv preprint" },
    { key: "id", label: "ID", value: paper.id, mono: true },
    { key: "chunks", label: t("paper.textChunks"), value: String(paper.chunkCount) },
    { key: "indexSize", label: t("paper.indexSize"), value: formatSize(paper.indexSize) },
  ];

  return (
    <div>
      <div className="pf-abstract-spec">
        {spec.map((item) => (
          <span className="pf-abstract-spec-item" key={item.key}>
            <span className="pf-abstract-spec-label">{item.label}</span>
            <span
              className={
                item.mono ? "pf-abstract-spec-value pf-abstract-spec-value--mono" : "pf-abstract-spec-value"
              }
            >
              {item.value}
            </span>
          </span>
        ))}
      </div>

      <Title level={5} className="pf-serif" style={{ margin: "22px 0 12px" }}>
        {t("paper.abstract")}
      </Title>
      <p
        style={{
          color: "var(--pf-text-secondary)",
          fontSize: 15,
          lineHeight: 1.9,
          textAlign: "justify",
          margin: 0,
        }}
      >
        {paper.abstract}
      </p>
    </div>
  );
}
