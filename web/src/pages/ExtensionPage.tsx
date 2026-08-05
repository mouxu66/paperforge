import { Button, Card, Space, Steps, Typography } from "antd";
import { Download, Puzzle } from "lucide-react";
import { useTranslation } from "react-i18next";
import PageHeader from "@/components/PageHeader";

const { Title, Paragraph } = Typography;

export default function ExtensionPage() {
  const { t } = useTranslation();

  const installSteps = [
    t("extension.step1"),
    t("extension.step2"),
    t("extension.step3"),
  ];

  const supportedSites = (t("extension.sites", { returnObjects: true }) as string[]) || [];

  return (
    <div style={{ maxWidth: 800, margin: "0 auto" }}>
      <PageHeader title={t("extension.title")} description={t("extension.subtitle")} />
      <Card className="pf-glass-card" style={{ marginBottom: 24 }}>
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Title level={5} style={{ marginTop: 0 }}>
            <Puzzle size={20} style={{ marginRight: 8, verticalAlign: "middle" }} />
            {t("extension.download")}
          </Title>
          <Paragraph>{t("extension.downloadDesc")}</Paragraph>
          <Button
            type="primary"
            href="/extension/paperforge-clipper.zip"
            download
            icon={<Download size={16} />}
          >
            {t("extension.download")}
          </Button>
        </Space>
      </Card>

      <Card className="pf-glass-card" style={{ marginBottom: 24 }}>
        <Title level={5} style={{ marginTop: 0, marginBottom: 16 }}>
          {t("extension.installTitle")}
        </Title>
        <Steps
          direction="vertical"
          current={-1}
          items={installSteps.map((title) => ({ title }))}
        />
      </Card>

      <Card className="pf-glass-card">
        <Title level={5} style={{ marginTop: 0 }}>
          {t("extension.supported")}
        </Title>
        <ul style={{ marginBottom: 16, paddingLeft: 20 }}>
          {supportedSites.map((site) => (
            <li key={site}>{site}</li>
          ))}
        </ul>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {t("extension.note")}
        </Paragraph>
      </Card>
    </div>
  );
}
