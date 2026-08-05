import { BookOpen, Search, UploadCloud, Lightbulb, Zap } from "lucide-react";
import type { TFunction } from "i18next";

interface GuideStep {
  icon: React.ReactNode;
  title: string;
  desc: string;
}

const FIRST_RUN_DEFAULT_STEPS = (t: TFunction): GuideStep[] => [
  {
    icon: <UploadCloud />,
    title: t("home.firstRun.step1Title", "上传论文"),
    desc: t("home.firstRun.step1Desc", "拖拽 PDF/DOCX 或从 arXiv 导入"),
  },
  {
    icon: <Lightbulb />,
    title: t("home.firstRun.step2Title", "自动索引"),
    desc: t("home.firstRun.step2Desc", "系统自动提取元数据并建全文索引"),
  },
  {
    icon: <Zap />,
    title: t("home.firstRun.step3Title", "检索 & 问答"),
    desc: t("home.firstRun.step3Desc", "搜索、关系图、深度审稿一键可用"),
  },
];

/** 便捷工厂：构建首次跑引导的 guide 对象（供调用方复用默认 i18n 文案） */
export function buildFirstRunGuide(t: TFunction) {
  return {
    title: t("home.firstRun.guideTitle", "3 步开始"),
    steps: FIRST_RUN_DEFAULT_STEPS(t),
  };
}

/** 便捷工厂：构建「索引缺失（语义搜索无结果）」引导的 guide 对象 */
export function buildOcrGuide(t: TFunction) {
  return {
    title: t("search.noOcrGuideTitle", "为什么没有结果？"),
    steps: [
      {
        icon: <BookOpen />,
        title: t("search.noOcrStep1Title", "全文索引未完成"),
        desc: t(
          "search.noOcrStep1Desc",
          "语义检索依赖论文的全文向量索引，新导入的论文需要时间完成索引",
        ),
      },
      {
        icon: <Search />,
        title: t("search.noOcrStep2Title", "切换关键词搜索"),
        desc: t("search.noOcrStep2Desc", "关键词搜索基于 FTS5，不依赖向量索引，立即可用"),
      },
    ],
  };
}
