import { BookOpen, Search, UploadCloud, Lightbulb, Zap, CheckCircle2, Cpu, Cloud, FileSearch } from "lucide-react";
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

/**
 * 便捷工厂：构建「未配置模型」引导的 guide 对象。
 *
 * 只讲「下一步怎么做」，不讲模型原理：新手卡住的地方是「不知道去哪配、配哪种、
 * 不配是不是就完全不能用」，这三条各对应一步。
 */
export function buildNoModelGuide(t: TFunction) {
  return {
    title: t("noModel.guideTitle", "3 步配好模型"),
    steps: [
      {
        icon: <Cpu />,
        title: t("noModel.step1Title", "打开模型管理"),
        desc: t(
          "noModel.step1Desc",
          "顶部「设置 → 模型管理」，或直接点下方按钮；页面里可以「检测本地端口」自动发现已运行的推理服务",
        ),
      },
      {
        icon: <Cloud />,
        title: t("noModel.step2Title", "选云端或本地"),
        desc: t(
          "noModel.step2Desc",
          "云端：填 API Key 即可用；本地：先启动推理服务（如 Ollama / vLLM）再检测端口。两种都支持，按手头条件选",
        ),
      },
      {
        icon: <CheckCircle2 />,
        title: t("noModel.step3Title", "回到本页重试"),
        desc: t(
          "noModel.step3Desc",
          "模型就绪后问答、综述、写作的生成能力立刻可用；在此之前，检索、图检索、深度审稿等不依赖生成模型的功能不受影响",
        ),
      },
    ],
  };
}

/**
 * 便捷工厂：构建「论文索引未完成」引导的 guide 对象。
 *
 * 与 buildOcrGuide 的区别：那个面向「整库语义搜索无结果」，
 * 这个面向「某一篇论文的索引还没建好」——此时其余论文正常可搜，
 * 用户需要的是「这一篇怎么了 / 现在还能怎么用它」。
 */
export function buildIndexPendingGuide(t: TFunction) {
  return {
    title: t("indexPending.guideTitle", "这一篇为什么搜不到？"),
    steps: [
      {
        icon: <FileSearch />,
        title: t("indexPending.step1Title", "它的全文索引还没建好"),
        desc: t(
          "indexPending.step1Desc",
          "新导入或刚 OCR 完的论文需要完成分块与向量化，之后才会进入语义检索的候选集",
        ),
      },
      {
        icon: <Search />,
        title: t("indexPending.step2Title", "先用关键词搜索它"),
        desc: t(
          "indexPending.step2Desc",
          "关键词搜索基于 FTS5，不依赖向量索引，现在就能按标题、作者、术语找到它",
        ),
      },
      {
        icon: <Zap />,
        title: t("indexPending.step3Title", "其余功能不受影响"),
        desc: t(
          "indexPending.step3Desc",
          "阅读、标注、引用、深度审稿都只用元数据或原文，不依赖索引；等索引完成后语义检索会自动覆盖它",
        ),
      },
    ],
  };
}
