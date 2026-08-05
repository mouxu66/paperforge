import {
  Library,
  BookOpen,
  Scale,
  GraduationCap,
  PenTool,
  Landmark,
  FlaskConical,
  Cpu,
  Sprout,
  HeartPulse,
  Shield,
  PieChart,
  Palette,
  Network,
  Tag,
  Package,
  X,
  Hash,
  ChevronDown,
  ChevronUp,
  Brain,
  TrendingUp,
  Languages,
  Search,
  Bot,
  Lock,
  Code2,
  MousePointer,
  Database,
  Server,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { usePaperStore } from "@/store/usePaperStore";
import { useTagStore } from "@/store/useTagStore";
import { CATEGORY_LABELS, formatLabel } from "./SideBar.utils";

export interface SideBarProps {
  /** 移动端抽屉是否展开 */
  mobileOpen?: boolean;
  /** 移动端关闭抽屉（点击条目 / 遮罩 / 关闭按钮时触发） */
  onClose?: () => void;
}

// 分类 key → Lucide 图标映射（统一线性风格，stroke=currentColor 由 CSS 控色）
// 以教育部 14 大学科门类为主，历史/source-based key 映射到对应门类图标。
const CATEGORY_ICON: Record<string, LucideIcon> = {
  all: Library,

  // 14 大学科门类
  philosophy: BookOpen, // 哲学
  economics: PieChart, // 经济学
  law: Scale, // 法学
  education: GraduationCap, // 教育学
  literature: PenTool, // 文学
  history: Landmark, // 历史学
  science: FlaskConical, // 理学
  engineering: Cpu, // 工学
  agriculture: Sprout, // 农学
  medicine: HeartPulse, // 医学
  military: Shield, // 军事学
  management: PieChart, // 管理学
  arts: Palette, // 艺术学
  interdisciplinary: Network, // 交叉学科

  // 兼容映射：常见 arXiv 子类 → 独立图标
  cs: Cpu,
  computer_science: Cpu,
  ai: Brain, // 人工智能
  artificial_intelligence: Brain,
  llm: Cpu,
  cv: Cpu,
  nlp: Languages, // 自然语言处理
  lora: Cpu,
  quant: Cpu,
  ri: Cpu,
  rl: Cpu,
  comm: Cpu,
  audio: Cpu,
  speech: Cpu,
  agent: Cpu,
  agents: Cpu,
  robot: Bot,
  robotics: Bot, // 机器人学
  ml: TrendingUp, // 机器学习
  ir: Search, // 信息检索
  se: Code2, // 软件工程
  networks: Network, // 计算机网络
  multimodal: Cpu,
  gan: Cpu,
  gen: Cpu,
  generation: Cpu,
  diffusion: Cpu,
  transformer: Cpu,
  bert: Cpu,
  gpt: Cpu,
  rag: Cpu,
  search: Cpu,
  db: Database, // 数据库
  database: Cpu,
  graph: Cpu,
  kg: Cpu,
  knowledge_graph: Cpu,
  time_series: Cpu,
  optimization: Cpu,
  security: Lock, // 信息安全
  privacy: Cpu,
  systems: Server, // 分布式系统
  cloud: Cpu,
  edge: Cpu,
  federated: Cpu,
  hardware: Cpu, // 硬件
  software: Cpu,
  code: Cpu,
  programming: Cpu,
  algorithm: Cpu,
  data_mining: Cpu,
  hci: MousePointer, // 人机交互

  // 兼容映射：理科 / 医科 / 社科
  math: FlaskConical,
  theory: FlaskConical,
  biology: FlaskConical,
  chemistry: FlaskConical,
  physics: FlaskConical,
  quantum: FlaskConical,
  climate: FlaskConical,
  bio: HeartPulse,
  medical: HeartPulse,
  neuroscience: HeartPulse,
  social: Scale,
  finance: PieChart,
  environment: Sprout,
  energy: Cpu,
  game: PieChart,

  // 兼容映射：source-based 旧分类
  arxiv: Cpu,
  upload: Network,
  cnki: Network,
  google_scholar: Network,
  web_clipper: Network,
  pubmed: HeartPulse,
  ieee: Cpu,
  springer: Network,
};

const FALLBACK_ICON = Tag; // 未知分类兜底
const UNCATEGORIZED_ICON = Package; // 未分类

interface CategoryItemProps {
  icon: LucideIcon;
  label: string;
  count: number | string;
  active: boolean;
  muted?: boolean;
  isSubItem?: boolean;
  onClick?: () => void;
}

function CategoryItem({
  icon: Icon,
  label,
  count,
  active,
  muted,
  isSubItem,
  onClick,
}: CategoryItemProps) {
  const className = `pf-category-item${active ? " pf-category-active" : ""}${
    muted ? " pf-category-item--muted" : ""
  }${isSubItem ? " pf-category-subitem" : ""}`;

  const handleClick = () => {
    if (muted) return;
    onClick?.();
  };

  if (muted) {
    return (
      <div className={className} style={{ opacity: 0.6, cursor: "default" }}>
        <span className="pf-category-main">
          <Icon className="pf-category-icon" size={18} strokeWidth={1.75} aria-hidden="true" />
          <span className="pf-category-label">{label}</span>
        </span>
        <span className="pf-category-count">{count}</span>
      </div>
    );
  }

  return (
    <button
      type="button"
      className={className}
      onClick={handleClick}
      aria-current={active ? "page" : undefined}
    >
      <span className="pf-category-main">
        <Icon className="pf-category-icon" size={18} strokeWidth={1.75} aria-hidden="true" />
        <span className="pf-category-label">{label}</span>
      </span>
      <span className="pf-category-count">{count}</span>
    </button>
  );
}

export default function SideBar({ mobileOpen = false, onClose }: SideBarProps) {
  const { t } = useTranslation();
  const category = usePaperStore((s) => s.category);
  const setCategory = usePaperStore((s) => s.setCategory);
  const setKeyword = usePaperStore((s) => s.setKeyword);
  const stats = usePaperStore((s) => s.stats);
  const tags = useTagStore((s) => s.tags);
  const loadTags = useTagStore((s) => s.load);

  // 分类展开/收起（默认折叠，避免分类过多时侧边栏过长）
  const [showAllCategories, setShowAllCategories] = useState(false);

  // 加载标签（侧边栏作为第二分类入口）
  useEffect(() => {
    void loadTags();
  }, [loadTags]);

  // 从 API 统计动态构建分类列表（排除空分类，按数量降序）
  const rawCategories = stats?.byCategory ?? [];

  // 感悟报告数量（从 stats 单独字段取，更稳；兜底按 byCategory 计算）
  const reportCount =
    stats?.reportCount ?? rawCategories.find((c) => c.category === "report")?.count ?? 0;

  // 有效分类（非空，且排除 'all' / 'report'，避免与静态项重复）
  const dynamicCategories = rawCategories
    .filter(
      (c) =>
        c.category && c.category.trim() !== "" && c.category !== "all" && c.category !== "report",
    )
    .sort((a, b) => b.count - a.count);

  // 未分类论文计数（category 为空/缺失的）
  const uncategorizedCount = rawCategories
    .filter((c) => !c.category || c.category.trim() === "")
    .reduce((sum, c) => sum + c.count, 0);

  const totalCount = stats?.totalPapers ?? 0;

  // 默认展示前 8 个分类，点击「展开」后显示全部
  const CATEGORY_DISPLAY_LIMIT = 8;
  const hasMoreCategories = dynamicCategories.length > CATEGORY_DISPLAY_LIMIT;
  const visibleCategories = showAllCategories
    ? dynamicCategories
    : dynamicCategories.slice(0, CATEGORY_DISPLAY_LIMIT);

  // 顶部标签页与侧边栏分类同步：
  // - 任何非 report 分类（含 all 与动态分类）都对应顶部「论文」标签
  // - report 分类对应顶部「感悟报告」标签
  const isPaperTabActive = category !== "report";

  // 分类高亮规则：
  // - 顶部标签指示器负责展示「论文 / 感悟报告」一级状态
  // - 下方分类列表只高亮当前选中的具体分类（all 或动态分类），
  //   避免「全部论文」与动态分类同时高亮

  const handleSelect = (cat: string) => {
    setCategory(cat);
    onClose?.(); // 移动端选择后自动收起抽屉
  };

  // 点击标签：回到全部论文并按标签名关键词检索
  const handleTagClick = (tagName: string) => {
    setCategory("all");
    setKeyword(tagName);
    onClose?.();
  };

  return (
    <>
      {/* 移动端遮罩 */}
      <div
        className={`pf-sidebar-backdrop${mobileOpen ? " is-open" : ""}`}
        onClick={onClose}
        aria-hidden={!mobileOpen}
      />

      <aside
        className={`pf-sidebar${mobileOpen ? " is-open" : ""}`}
        aria-label={t("sidebar.categories")}
      >
        <div className="pf-sidebar-header">
          <span className="pf-sidebar-title">{t("sidebar.categories")}</span>
          <button
            type="button"
            className="pf-sidebar-close"
            onClick={onClose}
            aria-label={t("sidebar.close", "关闭导航")}
          >
            <X size={18} />
          </button>
        </div>

        {/* 顶部「论文 / 感悟报告」标签页指示器：与 HomePage 顶部标签同步，
            将一级内容切换与具体分类高亮分离，避免两个项目同时高亮 */}
        <div className="pf-sidebar-tabs">
          <button
            type="button"
            className={`pf-sidebar-tab${isPaperTabActive ? " active" : ""}`}
            onClick={() => handleSelect("all")}
            aria-pressed={isPaperTabActive}
          >
            {t("home.tabs.papers", "论文")}
          </button>
          <button
            type="button"
            className={`pf-sidebar-tab${!isPaperTabActive ? " active" : ""}`}
            onClick={() => handleSelect("report")}
            aria-pressed={!isPaperTabActive}
          >
            {t("home.tabs.reports", "感悟报告")}
          </button>
        </div>

        <nav className="pf-category-list" aria-label={t("sidebar.categories")}>
          {/* 论文（静态首项，与顶部「论文」标签镜像）——仅当 category === "all" 时高亮 */}
          <CategoryItem
            icon={Library}
            label={CATEGORY_LABELS.all}
            count={totalCount}
            active={category === "all"}
            onClick={() => handleSelect("all")}
          />

          {/* 动态分类（作为「论文」的子项展示） */}
          {visibleCategories.map((c) => (
            <CategoryItem
              key={c.category}
              icon={CATEGORY_ICON[c.category] ?? FALLBACK_ICON}
              label={formatLabel(c.category)}
              count={c.count}
              active={category === c.category}
              isSubItem
              onClick={() => handleSelect(c.category)}
            />
          ))}

          {/* 分类展开/收起 */}
          {hasMoreCategories && (
            <button
              type="button"
              className="pf-category-toggle"
              onClick={() => setShowAllCategories((v) => !v)}
              aria-expanded={showAllCategories}
            >
              <span>
                {showAllCategories
                  ? t("sidebar.showLess", "收起")
                  : t("sidebar.showMore", "展开更多")}
              </span>
              {showAllCategories ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          )}

          {/* 感悟报告（单独入口，与论文列表分离） */}
          <CategoryItem
            icon={BookOpen}
            label={t("sidebar.reflectionReports")}
            count={reportCount}
            active={category === "report"}
            onClick={() => handleSelect("report")}
          />

          {/* 未分类论文（仅当存在时显示，不可点击筛选，仅展示计数） */}
          {uncategorizedCount > 0 && (
            <CategoryItem
              icon={UNCATEGORIZED_ICON}
              label={t("sidebar.uncategorized")}
              count={uncategorizedCount}
              active={false}
              muted
            />
          )}
        </nav>

        {/* 热门标签（第二分类入口） */}
        {tags.length > 0 && (
          <div className="pf-sidebar-section">
            <div className="pf-sidebar-section-title">{t("sidebar.topTags", "热门标签")}</div>
            <nav className="pf-category-list" aria-label={t("sidebar.topTags", "热门标签")}>
              {tags.slice(0, 8).map((tag) => (
                <CategoryItem
                  key={tag.name}
                  icon={Hash}
                  label={tag.name}
                  count={tag.count}
                  active={false}
                  isSubItem
                  onClick={() => handleTagClick(tag.name)}
                />
              ))}
            </nav>
          </div>
        )}

        <div className="pf-sidebar-footer">
          <div className="pf-sidebar-footer-title">{t("sidebar.systemStatus")}</div>
          <div className="pf-sidebar-status">
            <span className="pf-status-dot" />
            {t("sidebar.apiOnline")}
          </div>
        </div>
      </aside>
    </>
  );
}
