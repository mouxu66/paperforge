import { Typography } from "antd";
import { BookOpen, LibraryBig } from "lucide-react";
import { useTranslation } from "react-i18next";

import { HERO_IMAGES } from "./heroImages";

export default function HomeHeader() {
  const { t } = useTranslation();

  return (
    <section className="pf-page-hero" aria-labelledby="paperforge-home-title">
      <div className="pf-hero-copy">
        <div className="pf-page-kicker">
          <LibraryBig size={14} strokeWidth={2.2} />
          {t("home.kicker", "PaperForge research workspace")}
        </div>
        <Typography.Title
          id="paperforge-home-title"
          level={3}
          className="pf-page-title"
          style={{ marginBottom: 0 }}
        >
          {t("home.title")}
        </Typography.Title>
        <div className="pf-subtitle">{t("home.subtitle")}</div>
        <div className="pf-hero-meta" aria-label={t("home.heroSignal", "研究工作台状态")}>
          <span className="pf-hero-signal">
            <span className="pf-hero-signal-dot" aria-hidden="true" />
            {t("home.heroSignal", "研究工作台已就绪")}
          </span>
          <span className="pf-hero-divider" aria-hidden="true" />
          <span className="pf-hero-flow">{t("home.heroFlow", "收集、理解、创作")}</span>
        </div>
      </div>

      <div className="pf-hero-art" aria-hidden="true">
        <div className="pf-hero-material-stack">
          <div className="pf-hero-material pf-hero-material--wide">
            <img
              src={HERO_IMAGES.archive}
              alt=""
              loading="eager"
              decoding="async"
              onError={(event) => {
                event.currentTarget.parentElement?.classList.add("is-failed");
                event.currentTarget.style.display = "none";
              }}
            />
          </div>
          <div className="pf-hero-material pf-hero-material--tall">
            <img
              src={HERO_IMAGES.lab}
              alt=""
              loading="eager"
              decoding="async"
              onError={(event) => {
                event.currentTarget.parentElement?.classList.add("is-failed");
                event.currentTarget.style.display = "none";
              }}
            />
          </div>
          <div className="pf-hero-material pf-hero-material--small">
            <img
              src={HERO_IMAGES.notes}
              alt=""
              loading="eager"
              decoding="async"
              onError={(event) => {
                event.currentTarget.parentElement?.classList.add("is-failed");
                event.currentTarget.style.display = "none";
              }}
            />
          </div>
          <div className="pf-hero-material-mark">
            <BookOpen size={23} strokeWidth={1.7} />
          </div>
        </div>
        <div className="pf-hero-art-lines" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <span className="pf-hero-art-label">{t("home.heroArtLabel", "研究库")}</span>
      </div>
    </section>
  );
}
