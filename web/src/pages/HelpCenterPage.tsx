import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button, Tag } from "antd";
import {
  UploadCloud,
  Search,
  MessagesSquare,
  PenLine,
  Radar,
  Compass,
  PlayCircle,
  ArrowRight,
  Lightbulb,
  AlertTriangle,
} from "lucide-react";
import PageHeader from "@/components/PageHeader";
import { useOnboardingStore } from "@/store/useOnboardingStore";

interface HelpStep {
  titleKey: string;
  descKey: string;
}

interface HelpSection {
  id: string;
  /** 章节序号（纯展示，用 01/02 的排版节奏） */
  no: string;
  icon: ReactNode;
  titleKey: string;
  introKey: string;
  steps: HelpStep[];
  /** 「你会看到什么」——把抽象描述落到界面上可验证的观察点 */
  seeKey: string;
  /** 「常见误区」——新手最容易误判的地方 */
  pitfallKey: string;
  /** 对应功能页路由（可选） */
  to?: string;
  toLabelKey?: string;
}

/**
 * 帮助中心：核心功能实操。
 *
 * 内容取向：只讲「怎么操作 + 结果怎么读 + 容易误判什么」，
 * 不讲实现细节——实现细节在 docs/ 与 RUNBOOK.md 里。
 * 每条描述都以代码实际行为为准（四维权重、裁决档位、一票否决阈值），
 * 不写「大概」「可能」这类无法验证的说法。
 */
const SECTIONS: HelpSection[] = [
  {
    id: "help-import",
    no: "01",
    icon: <UploadCloud />,
    titleKey: "help.import.title",
    introKey: "help.import.intro",
    steps: [
      { titleKey: "help.import.s1.title", descKey: "help.import.s1.desc" },
      { titleKey: "help.import.s2.title", descKey: "help.import.s2.desc" },
      { titleKey: "help.import.s3.title", descKey: "help.import.s3.desc" },
    ],
    seeKey: "help.import.see",
    pitfallKey: "help.import.pitfall",
    to: "/",
    toLabelKey: "help.import.cta",
  },
  {
    id: "help-search",
    no: "02",
    icon: <Search />,
    titleKey: "help.search.title",
    introKey: "help.search.intro",
    steps: [
      { titleKey: "help.search.s1.title", descKey: "help.search.s1.desc" },
      { titleKey: "help.search.s2.title", descKey: "help.search.s2.desc" },
      { titleKey: "help.search.s3.title", descKey: "help.search.s3.desc" },
    ],
    seeKey: "help.search.see",
    pitfallKey: "help.search.pitfall",
    to: "/",
    toLabelKey: "help.search.cta",
  },
  {
    id: "help-ask",
    no: "03",
    icon: <MessagesSquare />,
    titleKey: "help.ask.title",
    introKey: "help.ask.intro",
    steps: [
      { titleKey: "help.ask.s1.title", descKey: "help.ask.s1.desc" },
      { titleKey: "help.ask.s2.title", descKey: "help.ask.s2.desc" },
      { titleKey: "help.ask.s3.title", descKey: "help.ask.s3.desc" },
    ],
    seeKey: "help.ask.see",
    pitfallKey: "help.ask.pitfall",
    to: "/ask",
    toLabelKey: "help.ask.cta",
  },
  {
    id: "help-write",
    no: "04",
    icon: <PenLine />,
    titleKey: "help.write.title",
    introKey: "help.write.intro",
    steps: [
      { titleKey: "help.write.s1.title", descKey: "help.write.s1.desc" },
      { titleKey: "help.write.s2.title", descKey: "help.write.s2.desc" },
      { titleKey: "help.write.s3.title", descKey: "help.write.s3.desc" },
      { titleKey: "help.write.s4.title", descKey: "help.write.s4.desc" },
    ],
    seeKey: "help.write.see",
    pitfallKey: "help.write.pitfall",
    to: "/write",
    toLabelKey: "help.write.cta",
  },
  {
    id: "help-depth",
    no: "05",
    icon: <Radar />,
    titleKey: "help.depth.title",
    introKey: "help.depth.intro",
    steps: [
      { titleKey: "help.depth.s1.title", descKey: "help.depth.s1.desc" },
      { titleKey: "help.depth.s2.title", descKey: "help.depth.s2.desc" },
      { titleKey: "help.depth.s3.title", descKey: "help.depth.s3.desc" },
      { titleKey: "help.depth.s4.title", descKey: "help.depth.s4.desc" },
    ],
    seeKey: "help.depth.see",
    pitfallKey: "help.depth.pitfall",
    to: "/depth",
    toLabelKey: "help.depth.cta",
  },
];

function scrollToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export default function HelpCenterPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const startOnboarding = useOnboardingStore((s) => s.start);
  const [activeId, setActiveId] = useState(SECTIONS[0].id);

  // 目录高亮：用 IntersectionObserver 跟随阅读位置，比监听 scroll 便宜
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setActiveId(visible[0].target.id);
      },
      { rootMargin: "-90px 0px -60% 0px" },
    );
    SECTIONS.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, []);

  // 引导锚点都在首页，所以先回首页再启动
  const handleReplayOnboarding = () => {
    navigate("/");
    startOnboarding();
  };

  return (
    <div className="pf-help-page">
      <PageHeader
        kicker={t("help.kicker", "Help center")}
        title={t("help.title", "帮助中心")}
        description={t(
          "help.subtitle",
          "五个核心功能怎么操作、结果怎么读、哪里容易误判。跟着做一遍就会了。",
        )}
        actions={
          <Button icon={<PlayCircle size={15} />} onClick={handleReplayOnboarding}>
            {t("help.replayOnboarding", "重看新手引导")}
          </Button>
        }
      />

      <div className="pf-help-quickstart">
        <div className="pf-help-quickstart-head">
          <Compass size={15} />
          <span>{t("help.quickstart.title", "3 分钟上手")}</span>
        </div>
        <div className="pf-help-quickstart-steps">
          {[1, 2, 3].map((n) => (
            <div className="pf-help-quickstart-step" key={n}>
              <span className="pf-help-quickstart-no">{n}</span>
              <div>
                <div className="pf-help-quickstart-step-title">
                  {t(`help.quickstart.s${n}.title`)}
                </div>
                <div className="pf-help-quickstart-step-desc">{t(`help.quickstart.s${n}.desc`)}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="pf-help-body">
        <aside className="pf-help-toc" aria-label={t("help.toc", "目录")}>
          <div className="pf-help-toc-title">{t("help.toc", "目录")}</div>
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              className={
                s.id === activeId ? "pf-help-toc-item pf-help-toc-item-active" : "pf-help-toc-item"
              }
              onClick={() => scrollToSection(s.id)}
            >
              <span className="pf-help-toc-no">{s.no}</span>
              <span className="pf-help-toc-label">{t(s.titleKey)}</span>
            </button>
          ))}
        </aside>

        <div className="pf-help-sections">
          {SECTIONS.map((s) => (
            <section className="pf-help-section" id={s.id} key={s.id}>
              <div className="pf-help-section-head">
                <span className="pf-help-section-icon">{s.icon}</span>
                <div>
                  <div className="pf-help-section-no">{s.no}</div>
                  <h2 className="pf-help-section-title">{t(s.titleKey)}</h2>
                </div>
              </div>

              <p className="pf-help-section-intro">{t(s.introKey)}</p>

              <ol className="pf-help-steps">
                {s.steps.map((step, i) => (
                  <li className="pf-help-step" key={step.titleKey}>
                    <span className="pf-help-step-no">{i + 1}</span>
                    <div className="pf-help-step-body">
                      <div className="pf-help-step-title">{t(step.titleKey)}</div>
                      <div className="pf-help-step-desc">{t(step.descKey)}</div>
                    </div>
                  </li>
                ))}
              </ol>

              <div className="pf-help-note pf-help-note-see">
                <Lightbulb size={14} />
                <div>
                  <strong>{t("help.seeLabel", "你会看到")}</strong>
                  <span>{t(s.seeKey)}</span>
                </div>
              </div>

              <div className="pf-help-note pf-help-note-pitfall">
                <AlertTriangle size={14} />
                <div>
                  <strong>{t("help.pitfallLabel", "常见误区")}</strong>
                  <span>{t(s.pitfallKey)}</span>
                </div>
              </div>

              {s.to && s.toLabelKey && (
                <Button
                  type="link"
                  className="pf-help-cta"
                  onClick={() => navigate(s.to as string)}
                >
                  {t(s.toLabelKey)}
                  <ArrowRight size={13} style={{ marginLeft: 4 }} />
                </Button>
              )}
            </section>
          ))}

          <section className="pf-help-section pf-help-section-more" id="help-more">
            <div className="pf-help-section-head">
              <span className="pf-help-section-icon">
                <Compass />
              </span>
              <div>
                <div className="pf-help-section-no">06</div>
                <h2 className="pf-help-section-title">{t("help.more.title", "还想了解更多")}</h2>
              </div>
            </div>
            <div className="pf-help-more-list">
              <div className="pf-help-more-item">
                <Tag color="blue">{t("help.more.docsTag", "文档")}</Tag>
                <span>{t("help.more.docs")}</span>
              </div>
              <div className="pf-help-more-item">
                <Tag color="orange">{t("help.more.troubleTag", "排障")}</Tag>
                <span>{t("help.more.trouble")}</span>
              </div>
              <div className="pf-help-more-item">
                <Tag color="green">{t("help.more.repoTag", "仓库")}</Tag>
                <span>{t("help.more.repo")}</span>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
