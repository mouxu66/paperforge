import { useState } from "react";
import { useTranslation } from "react-i18next";

/** Shared brand banner shown above every desktop route. */
export default function PageBanner() {
  const { t } = useTranslation();
  const [imageFailed, setImageFailed] = useState(false);

  return (
    <div
      className={`pf-page-banner${imageFailed ? " is-failed" : ""}`}
      role="img"
      aria-label={t("app.pageBannerAlt", "PaperForge 研究工作台")}
    >
      {imageFailed && (
        <div className="pf-page-banner-fallback">
          <span className="pf-page-banner-fallback-kicker">PaperForge</span>
          <strong>{t("home.title", "论文库")}</strong>
          <span>{t("home.subtitle", "本地 RAG 学术写作助手，搜索、问答、写作")}</span>
        </div>
      )}
      <img
        className="pf-page-banner-image"
        src="/assets/paperforge-page-banner.png"
        alt=""
        onError={() => setImageFailed(true)}
        aria-hidden="true"
      />
    </div>
  );
}
