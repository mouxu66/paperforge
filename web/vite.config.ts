import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import viteCompression from "vite-plugin-compression";
import path from "node:path";

// 端口可通过 VITE_API_PORT 环境变量覆盖（默认 8770）。
// 当 launcher 因端口冲突自动 fallback 到 8771/8772 时，前端需要相应修改。
const apiPort = process.env.VITE_API_PORT || "8770";

// P2-3: Vite 默认为首屏静态导入的 vendor chunk（react-vendor / antd-core /
// rc-vendor / antd-icons）自动注入 <link rel="modulepreload">，这是 ES 模块的
// 现代预加载机制（等价于 rel="preload" as="script" 且会处理模块求值）。
// 因此无需自定义 preload 插件 —— 见 dist/index.html 中已生成的 modulepreload 链接。

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // P2-3: 生成 gzip 压缩文件（.gz），仅压缩 > 10KB 的资源
    viteCompression({
      algorithm: "gzip",
      threshold: 10240,
      deleteOriginFile: false,
      // vite-plugin-compression@0.5.1 prints an incorrect `dist/C:/...` prefix
      // on Windows even though it writes the .gz files to dist/assets correctly.
      // Keep the artifacts, but silence that misleading path-only log noise.
      verbose: false,
    }),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    open: true,
    // 开发环境将 /api 代理到 FastAPI mock 后端，避免跨域
    proxy: {
      "/api": {
        target: `http://127.0.0.1:${apiPort}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    // The remaining antd and ECharts vendor chunks are intentionally shared
    // runtime bundles and sit just above 1 MB after minification. Route-specific
    // heavy vendors are split below; keep the limit high enough not to flag
    // these known, measured bundles as build noise.
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // 拆分 vendor：antd 生态（antd + @ant-design/icons + rc-*）合并为单一
        // antd-core chunk。这些包之间存在大量运行期相互引用，拆分会触发
        // "Cannot access 'X' before initialization" 错误；合并后彻底消除循环。
        manualChunks(id) {
          if (id.includes("node_modules")) {
            // Heavy, route-specific libraries stay out of the shared UI vendor
            // chunk so the home/ask routes do not pay for PDF and graph code.
            if (id.includes("pdfjs-dist")) return "pdf-vendor";
            if (id.includes("echarts") || id.includes("echarts-for-react")) return "charts-vendor";
            if (id.includes("rc-")) return "antd-core";
            if (id.includes("@ant-design/icons")) return "antd-core";
            if (id.includes("antd")) return "antd-core";
            if (
              id.includes("react-markdown") ||
              id.includes("remark") ||
              id.includes("micromark") ||
              id.includes("mdast") ||
              id.includes("unified") ||
              id.includes("unist") ||
              id.includes("hast") ||
              id.includes("bail") ||
              id.includes("trim-lines") ||
              id.includes("trough") ||
              id.includes("vfile") ||
              id.includes("character-") ||
              id.includes("decode-named-character") ||
              id.includes("markdown-table")
            ) {
              return "markdown-vendor";
            }
            // P2-3: react-window 独立拆分，避免与 react-vendor 混合
            if (id.includes("react-window")) return "react-window";
            if (
              id.includes("/react/") ||
              id.includes("/react-dom/") ||
              id.includes("/react-router") ||
              id.includes("/scheduler/")
            ) {
              return "react-vendor";
            }
          }
          return undefined;
        },
      },
    },
  },
});
