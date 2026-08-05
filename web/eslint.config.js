import js from "@eslint/js";
import tseslint from "typescript-eslint";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import prettier from "eslint-config-prettier";
import globals from "globals";

// PaperForge 前端 ESLint 9 扁平配置（配套 prettier，格式化交给 prettier，eslint 管代码质量）
export default tseslint.config(
  {
    ignores: ["dist", "node_modules", "coverage"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: {
      react,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: "detect" } },
    rules: {
      ...react.configs["jsx-runtime"].rules,
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // 生产源码逐步清理 any；测试文件因 mock 数据/组件需要保留 any
      "@typescript-eslint/no-explicit-any": "warn",
    },
  },
  // 测试文件：允许使用 any 进行 mock / 组件 stub
  {
    files: ["**/*.test.{ts,tsx}", "**/test-setup.ts", "**/__tests__/**"],
    rules: {
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
  // 工具/辅助文件：不包含 React 组件，关闭 fast-refresh 规则以避免误报
  {
    files: ["**/*.utils.{ts,tsx}", "**/*.guide.{ts,tsx}"],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
  prettier, // 关闭与 prettier 冲突的格式化规则
  // Node 上下文的构建/配置文件：声明 Node 全局变量，避免 no-undef 误报
  {
    files: ["vite.config.js", "vite.config.ts", "vitest.config.ts", "*.config.js", "scripts/**/*.js"],
    languageOptions: {
      globals: { ...globals.node },
      sourceType: "module",
    },
  },
);
