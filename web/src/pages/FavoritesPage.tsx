import { Star } from "lucide-react";
import { Space } from "antd";

import { useTranslation } from "react-i18next";
import PaperCard from "@/components/PaperCard";
import EmptyState from "@/components/EmptyState";
import { Reveal } from "@/components/motion";
import { useFavoriteStore } from "@/store/useFavoriteStore";
import PageHeader from "@/components/PageHeader";

export default function FavoritesPage() {
  // items 由 persist 中间件自动从 localStorage 恢复，无需手动 load
  const items = useFavoriteStore((s) => s.items);
  const { t } = useTranslation();

  return (
    <div>
      <PageHeader title={t("favorites.title")} description={t("favorites.subtitle")} />

      {items.length === 0 ? (
        <EmptyState description={t("favorites.empty")} />
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 16 }}>
          {items.map((p, index) => (
            <Reveal key={p.id} delay={Math.min(index * 40, 300)} style={{ height: "100%" }}>
              <PaperCard paper={p} />
            </Reveal>
          ))}
        </div>
      )}

      <div style={{ marginTop: 24, textAlign: "center" }}>
        <Space style={{ color: "var(--pf-text-placeholder)", fontSize: 13 }}>
          <Star /> {t("favorites.total", { count: items.length })}
        </Space>
      </div>
    </div>
  );
}
