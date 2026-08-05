import { useState } from "react";
import { Alert, Card, Col, List, Row, Typography, Upload } from "antd";
import type { UploadFile } from "antd";
import { Inbox, Zap, Pencil, BookOpen } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";

const { Title, Paragraph, Text } = Typography;

/** 格式化文件大小为人类可读字符串 */
function formatSize(bytes: number | undefined): string {
  if (!bytes) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

interface FeatureCardProps {
  to: string;
  icon: React.ReactNode;
  title: string;
  desc: string;
}

function FeatureCard({ to, icon, title, desc }: FeatureCardProps) {
  return (
    <Col xs={24} sm={8}>
      <Link to={to}>
        <Card className="pf-glass-card" style={{ height: "100%", cursor: "pointer" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div
              style={{
                width: 40,
                height: 40,
                borderRadius: 10,
                background: "var(--pf-primary-soft)",
                color: "var(--pf-primary)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              {icon}
            </div>
            <Text className="pf-serif" style={{ fontSize: 16, fontWeight: 600 }}>
              {title}
            </Text>
            <Text type="secondary" style={{ fontSize: 13, lineHeight: 1.6 }}>
              {desc}
            </Text>
          </div>
        </Card>
      </Link>
    </Col>
  );
}

export default function LocalRagPage() {
  const { t } = useTranslation();
  const [files, setFiles] = useState<UploadFile[]>([]);

  return (
    <div style={{ maxWidth: 900, margin: "0 auto", padding: "0 24px" }}>
      <PageHeader
        title={t("nav.localRag")}
        description={t("nav.localRagDesc", "基于本地知识库的检索增强生成 — 问答 · 综述 · 写作")}
      />
      {/* 三个 RAG 功能入口卡片 */}
      <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
        <FeatureCard
          to="/ask"
          icon={<Zap size={20} />}
          title={t("nav.ask")}
          desc={t(
            "localRag.askDesc",
            "基于本地论文库的 RAG 问答，提问并获取带引用的回答",
          )}
        />
        <FeatureCard
          to="/generate"
          icon={<Pencil size={20} />}
          title={t("nav.generate")}
          desc={t(
            "localRag.generateDesc",
            "输入主题，自动从本地论文库检索相关文献并生成综述初稿",
          )}
        />
        <FeatureCard
          to="/write"
          icon={<BookOpen size={20} />}
          title={t("nav.write")}
          desc={t(
            "localRag.writeDesc",
            "基于本地知识库的 RAG 写作助手，输入主题生成初稿并支持引用追溯",
          )}
        />
      </Row>

      {/* 本地文档管理 */}
      <Card className="pf-glass-card">
        <Title level={5} style={{ marginTop: 0, marginBottom: 8 }}>
          {t("localRag.docsTitle", "本地文档管理")}
        </Title>
        <Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 16 }}>
          {t(
            "localRag.docsDesc",
            "文档知识库上传接口尚未接入；当前仅可在本页临时选择文件，不会写入后端或建立索引。",
          )}
        </Paragraph>

        <Alert
          type="warning"
          showIcon
          message={t("localRag.notConnectedTitle", "文档知识库尚未接入")}
          description={t(
            "localRag.notConnectedDesc",
            "当前文件只保留在本页内存中用于预览，刷新页面后会消失，也不会参与问答、综述或写作检索。",
          )}
          style={{ marginBottom: 16 }}
        />

        <Upload.Dragger
          accept=".pdf,.docx,.txt,.md"
          showUploadList={false}
          multiple
          beforeUpload={(file) => {
            setFiles((prev) => [
              ...prev,
              {
                uid: `${file.uid ?? Date.now()}-${file.name}`,
                name: file.name,
                size: file.size,
              },
            ]);
            // 返回 false 阻止实际上传请求；这里仅做临时选择预览，不代表已入库。
            return false;
          }}
        >
          <p style={{ marginBottom: 8, color: "var(--pf-text-muted)" }}>
            <Inbox size={36} />
          </p>
          <p className="pf-serif" style={{ marginBottom: 4, fontSize: 15 }}>
            {t("localRag.dragHint", "点击或拖拽文件到此区域预览")}
          </p>
          <p style={{ marginBottom: 0, fontSize: 12, color: "var(--pf-text-placeholder)" }}>
            {t("localRag.dragDesc", "支持 PDF / DOCX / TXT / MD；当前不会上传到服务器")}
          </p>
        </Upload.Dragger>

        {files.length > 0 && (
          <List
            size="small"
            style={{ marginTop: 16 }}
            dataSource={files}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={item.name}
                  description={
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {formatSize(item.size)} · {t("localRag.statusLocalOnly", "仅本地预览，未入库")}
                    </Text>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </Card>
    </div>
  );
}
