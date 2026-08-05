import { Home, Star, Github, BookOpen, HelpCircle, Pencil, Zap, Globe, Settings, Loader, Radar, Image, Moon, Sun, Monitor, Copy, Table, Puzzle, Menu as MenuIcon } from "lucide-react";
import { Layout, Menu, Badge, Tooltip, Button, Tag, Dropdown } from "antd";

import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useFavoriteStore } from "@/store/useFavoriteStore";
import { useDepthStore } from "@/store/useDepthStore";
import { useThemeStore, type ThemeMode } from "@/store/useThemeStore";
import { changeLanguage, type SupportedLang } from "@/i18n";
import ModelSelector from "./ModelSelector";
import DevTools from "./DevTools";
import VramStatusIndicator from "./VramStatusIndicator";

const { Header } = Layout;

// 路由前缀 → 菜单 key 映射（替代嵌套三元）
const ROUTE_MAP = [
  { prefix: "/favorites", key: "/favorites" },
  { prefix: "/extension", key: "/extension" },
  { prefix: "/figures", key: "/figures" },
  { prefix: "/duplicates", key: "/duplicates" },
  { prefix: "/compare", key: "/compare" },
  { prefix: "/write", key: "/write" },
  { prefix: "/depth-v4", key: "/depth" },
  { prefix: "/depth", key: "/depth" },
  { prefix: "/ask", key: "/ask" },
  { prefix: "/generate", key: "/generate" },
  { prefix: "/models", key: "/models" },
  { prefix: "/settings", key: "/settings" },
  { prefix: "/paper", key: "/" },
] as const;

export default function TopNav({ onToggleNav }: { onToggleNav?: () => void }) {
  const location = useLocation();
  const favCount = useFavoriteStore((s) => s.items.length);
  const runningCount = useDepthStore((s) => s.tasks.filter((t) => t.status === "running").length);
  const { mode: themeMode, setMode } = useThemeStore();
  const { t, i18n } = useTranslation();

  const matched = ROUTE_MAP.find((r) => location.pathname.startsWith(r.prefix));
  const selectedKey = matched?.key ?? location.pathname;

  const currentLang = (i18n.language?.startsWith("en") ? "en" : "zh") as SupportedLang;

  const toggleLanguage = () => {
    changeLanguage(currentLang === "zh" ? "en" : "zh");
  };

  const themeMenuItems = [
    { key: "light", icon: <Sun />, label: t("theme.light", "浅色") },
    { key: "dark", icon: <Moon />, label: t("theme.dark", "深色") },
    { key: "system", icon: <Monitor />, label: t("theme.system", "跟随系统") },
  ];

  const themeIcon =
    themeMode === "dark" ? (
      <Moon />
    ) : themeMode === "system" ? (
      <Monitor />
    ) : (
      <Sun />
    );

  return (
    <Header
      className="pf-top-nav"
      style={{
        display: "flex",
        alignItems: "center",
        borderBottom: "1px solid var(--pf-border)",
        boxShadow: "0 1px 2px rgba(15,23,42,0.04)",
        position: "sticky",
        top: 0,
        zIndex: 20,
        background: "var(--pf-bg-primary)",
      }}
    >
      {onToggleNav && (
        <button
          type="button"
          className="pf-nav-toggle"
          onClick={onToggleNav}
          aria-label={t("sidebar.open", "打开导航")}
        >
          <MenuIcon />
        </button>
      )}
      <Link
        to="/"
        className="pf-brand"
        aria-label={t("app.title", "PaperForge")}
        style={{
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: 10,
          marginRight: 32,
          textDecoration: "none",
        }}
      >
        <div
          style={{
            width: 34,
            height: 34,
            borderRadius: 8,
            background: "var(--pf-primary)",
            color: "#fff",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontFamily: "Georgia, serif",
            fontWeight: 700,
            fontSize: 20,
          }}
        >
          P
        </div>
        <div style={{ lineHeight: 1.15 }}>
          <div
            className="pf-serif"
            style={{
              fontSize: 18,
              fontWeight: 600,
              color: "var(--pf-text-primary)",
              letterSpacing: "0.2px",
            }}
          >
            {t("app.title")}
          </div>
          <div style={{ fontSize: 11, color: "var(--pf-text-placeholder)" }}>
            {t("app.subtitle")}
          </div>
        </div>
      </Link>

      <Menu
        mode="horizontal"
        selectedKeys={[selectedKey]}
        style={{ flex: 1, borderBottom: "none", background: "transparent" }}
        items={[
          // 📥 收集 (Collect) — 上传/导入/入库/收藏（首页即主要收集入口）
          {
            key: "collect",
            label: t("nav.domain.collect", "收集"),
            children: [
              {
                key: "/",
                icon: <Home />,
                label: <Link to="/">{t("nav.home")}</Link>,
              },
              {
                key: "/favorites",
                icon: (
                  <Badge count={favCount} size="small" offset={[6, -2]}>
                    <Star />
                  </Badge>
                ),
                label: <Link to="/favorites">{t("nav.favorites")}</Link>,
              },
              {
                key: "/extension",
                icon: <Puzzle />,
                label: <Link to="/extension">{t("nav.extension")}</Link>,
              },
            ],
          },
          // 🗂 整理 (Organize) — 去重/对比/标签
          {
            key: "organize",
            label: t("nav.domain.organize", "整理"),
            children: [
              {
                key: "/duplicates",
                icon: <Copy />,
                label: <Link to="/duplicates">{t("nav.duplicates", "去重")}</Link>,
              },
              {
                key: "/compare",
                icon: <Table />,
                label: <Link to="/compare">{t("nav.compare", "对比")}</Link>,
              },
            ],
          },
          // 🔍 发现 (Discover) — 检索/图表/统计
          {
            key: "discover",
            label: t("nav.domain.discover", "发现"),
            children: [
              {
                key: "/figures",
                icon: <Image />,
                label: <Link to="/figures">{t("nav.figures", "图检索")}</Link>,
              },
            ],
          },
          // 🧠 理解/分析 (Understand & Analyze) — 问答/DEPTH/综述
          {
            key: "understand",
            label: t("nav.domain.understand", "理解"),
            children: [
              {
                key: "/ask",
                icon: <HelpCircle />,
                label: <Link to="/ask">{t("nav.ask")}</Link>,
              },
              {
                key: "/depth",
                icon: <Radar />,
                label: (
                  <Link to="/depth">
                    {t("nav.depth")}
                    {runningCount > 0 ? (
                      <Tooltip title={t("depth.evaluating")}>
                        <Tag
                          color="processing"
                          style={{
                            marginLeft: 6,
                            fontSize: 10,
                            lineHeight: "16px",
                            padding: "0 4px",
                            cursor: "default",
                          }}
                        >
                          <Loader className="pf-spin" style={{ fontSize: 10 }} /> {runningCount}
                        </Tag>
                      </Tooltip>
                    ) : null}
                  </Link>
                ),
              },
            ],
          },
          // ✍️ 创作 (Create) — 综述/写作
          {
            key: "create",
            label: t("nav.domain.create", "创作"),
            children: [
              {
                key: "/generate",
                icon: <Pencil />,
                label: <Link to="/generate">{t("nav.generate")}</Link>,
              },
              {
                key: "/write",
                icon: <BookOpen />,
                label: <Link to="/write">{t("nav.write")}</Link>,
              },
            ],
          },
          // ⚙️ 设置 (Configure) — 模型/设置
          {
            key: "configure",
            label: t("nav.domain.configure", "设置"),
            children: [
              {
                key: "/models",
                icon: <Zap />,
                label: <Link to="/models">{t("nav.models")}</Link>,
              },
              {
                key: "/settings",
                icon: <Settings />,
                label: <Link to="/settings">{t("nav.settings")}</Link>,
              },
            ],
          },
        ]}
      />

      <div className="pf-nav-actions" style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <VramStatusIndicator />
        <ModelSelector />
        <DevTools />
        {/* 主题切换 */}
        <Dropdown
          menu={{
            items: themeMenuItems,
            selectable: true,
            selectedKeys: [themeMode],
            onClick: ({ key }) => setMode(key as ThemeMode),
          }}
          placement="bottomRight"
        >
          <Button
            className="pf-theme-button"
            type="text"
            size="small"
            icon={themeIcon}
            aria-label={t("nav.theme", "主题")}
            style={{ color: "var(--pf-text-muted)", fontWeight: 500 }}
          >
            {t("nav.theme", "主题")}
          </Button>
        </Dropdown>
        {/* 语言切换按钮 */}
        <Tooltip title={t("nav.language")}>
          <Button
            className="pf-language-button"
            type="text"
            size="small"
            icon={<Globe />}
            onClick={toggleLanguage}
            style={{ color: "var(--pf-text-muted)", fontWeight: 500 }}
          >
            {currentLang === "zh" ? "中" : "EN"}
          </Button>
        </Tooltip>
        <Tooltip title={t("nav.localRag")}>
          <Link to="/local-rag">
            <BookOpen
              style={{ fontSize: 16, color: "var(--pf-text-muted)", cursor: "pointer" }}
            />
          </Link>
        </Tooltip>
        <Tooltip title={t("nav.repo")}>
          <a href="https://github.com/mouxu/PaperForge" target="_blank" rel="noreferrer">
            <Github
              style={{ fontSize: 16, color: "var(--pf-text-muted)", cursor: "pointer" }}
            />
          </a>
        </Tooltip>
        <span
          style={{
            fontSize: 12,
            color: "var(--pf-text-placeholder)",
            borderLeft: "1px solid var(--pf-border)",
            paddingLeft: 16,
          }}
        >
          {t("app.version")}
        </span>
      </div>
    </Header>
  );
}
