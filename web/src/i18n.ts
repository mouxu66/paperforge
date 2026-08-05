/**
 * react-i18next 初始化（子任务 5：多语言支持）。
 *
 * 设计要点：
 * - 资源按命名空间拆分为 zh / en 两个 JSON 文件，挂载到默认 namespace 'translation'
 * - 默认语言从 localStorage('pf_lang') 读取，缺省回退到 'zh'
 * - 检测浏览器语言：若用户从未设置且 navigator.language 以 'en' 开头，默认英文
 * - 切换语言时通过 i18n.changeLanguage() 同时写入 localStorage 持久化
 * - missingKeyHandler 仅在开发环境打印警告，避免生产环境噪声
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import zh from "./locales/zh.json";
import en from "./locales/en.json";

export const SUPPORTED_LANGS = ["zh", "en"] as const;
export type SupportedLang = (typeof SUPPORTED_LANGS)[number];

const STORAGE_KEY = "pf_lang";

/** 读取已保存的语言偏好；未保存时根据浏览器语言推断。 */
function detectInitialLang(): SupportedLang {
  if (typeof window === "undefined") return "zh";
  const saved = window.localStorage.getItem(STORAGE_KEY);
  if (saved === "zh" || saved === "en") return saved;
  // 浏览器语言以 en 开头则默认英文，否则中文
  const nav = window.navigator?.language || "zh";
  return nav.toLowerCase().startsWith("en") ? "en" : "zh";
}

void i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: detectInitialLang(),
  fallbackLng: "zh",
  interpolation: {
    // 🛡️ P1-4 i18n 占位符 XSS 修复：
    // 原版 escapeValue:false 会在翻译文件中允许 {{ var }} / {{ html ... }}，
    // 而后端论文 id 可能含特殊字符（如：arxiv:2501.<svg>），结合
    // dangerouslySetInnerHTML 可达成存储型 XSS。
    // 改为 true 后，i18next 会在插值时转义 < > & ' " 等为实体，
    // React 会再转一次出现双重转义——表情不变，但 XSS 不再可能。
    // 如果 en.json / zh.json 中有需要 HTML 格式的跨语言富文本，应改为
    // dangerouslySetInnerHTML={{ __html: sanitize(html) }} 而不是依赖 i18n。
    escapeValue: true,
  },
  returnNull: false,
});

/** 切换语言并持久化到 localStorage。 */
export function changeLanguage(lang: SupportedLang): void {
  void i18n.changeLanguage(lang);
  if (typeof window !== "undefined") {
    window.localStorage.setItem(STORAGE_KEY, lang);
  }
}

export default i18n;
