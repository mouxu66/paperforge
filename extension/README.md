# PaperForge Web Clipper

WP-4.1：一键将网页上的论文保存到本地 PaperForge 库。

## 功能

- 支持在以下站点一键抓取论文：
  - **arXiv**：论文详情页 → 自动转 PDF 并入库
  - **中国知网 CNKI**：论文详情页 → 提取标题/作者/摘要/期刊；开启「允许导入知网 PDF」后，借浏览器登录态一键下载 PDF 存入 PaperForge（默认关闭，需在扩展设置中手动开启）
  - **Google Scholar**：搜索结果/详情页 → 提取标题/作者/年份，尝试下载 [PDF] 链接
- 在支持的页面右下角注入「Save to PaperForge」悬浮按钮
- 通过扩展图标 popup 保存当前页面
- 后端 `/api/ingest/url` 接收前端提取的元数据，PDF 下载失败时自动降级为仅保存元数据

## 安装

1. 打开 Chrome/Edge 扩展管理页面（`chrome://extensions` 或 `edge://extensions`）
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择本 `extension/` 目录

## 构建/打包

```bash
cd extension
npm install      # 安装构建依赖（adm-zip）
npm run build    # 生成 dist/paperforge-clipper-v<version>.zip
npm run pack     # 同上
```

打包产物为 `dist/paperforge-clipper-v<version>.zip`，可直接上传 Chrome/Edge Web Store。

### 重新生成图标

`icons/*.png` 是通过 `scripts/generate-icons.py` 生成的产物。如需调整品牌色、字号或尺寸，修改脚本后重新运行即可：

```bash
cd extension
# 需要 Pillow
pip install Pillow
python scripts/generate-icons.py
```

生成后会在 `icons/` 目录下产出 `icon16.png`、`icon32.png`、`icon48.png`、`icon128.png`，并被 `manifest.json` 引用。

## 配置

默认后端地址：`http://localhost:8770`

点击扩展图标，在 popup 底部可修改 API Base URL，或编辑 `background.js` 中的 `DEFAULT_API_BASE`。

## 使用

1. 打开任意支持的论文页面
2. 点击右下角「Save to PaperForge」按钮
3. 等待按钮变为「Saved!」即表示入库成功
4. 回到 PaperForge 前端，刷新即可看到新论文

### 知网 PDF 一键导入

知网 PDF 通常需要登录/机构权限，后端无法直接下载。本扩展支持在浏览器内借用用户当前登录态下载 PDF 并直传到 PaperForge：

1. 点击扩展图标打开 popup
2. 勾选「允许导入知网 PDF」开关（默认关闭）
3. 打开知网论文详情页，右下角会出现「Save to PaperForge」按钮
4. 点击按钮 — 扩展会用你浏览器的知网登录态静默下载 PDF，并上传到本地 PaperForge
5. 按钮变为「Saved!」即表示入库成功

**安全约束**：
- 该按钮仅在用户主动点击时触发，不会自动导入
- 默认关闭，必须在扩展设置中手动开启才会显示按钮
- 不做任何批量/自动连续导入
- 下载失败（如付费墙/登录过期）时自动降级为仅保存元数据

开关切换后无需刷新页面，按钮会实时显示/隐藏。

## 技术栈

- Manifest V3
- content script（注入按钮 + 页面元数据提取）
- service worker（后台请求）
- popup（扩展图标面板 + 配置）

## 中文源适配说明

- CNKI 页面结构多样，content script 会尝试多种 CSS 选择器提取标题、作者、摘要、期刊
- 知网 PDF 通常需要登录/机构权限：开启「允许导入知网 PDF」后由浏览器在页面上下文内借用登录态下载；未开启或下载失败时后端降级保存元数据，避免丢失文献条目
- 所有中文元数据均按 UTF-8 处理并持久化到本地 SQLite

## 限制

- 需要 PaperForge 后端已启动
- Google Scholar 的 [PDF] 链接可能来自第三方，下载成功率取决于来源站点
- 部分 CNKI 页面需要登录才能看到完整元数据
- 知网 PDF 一键导入依赖浏览器当前登录态：若登录过期或无机构权限，PDF 下载会失败并降级保存元数据
