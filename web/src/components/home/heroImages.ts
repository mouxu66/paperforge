/**
 * 首页 hero 装饰图（本地 SVG data URI）。
 *
 * 背景：原实现使用 https://picsum.photos 外部占位图，被后端 CSP
 * (img-src 'self' data: blob:) 拦截 —— 浏览器控制台报错、图片不显示。
 * 改为内联 SVG data URI：完全离线可用、无 CSP 违规、无外部网络依赖，
 * 与 PaperForge「完全离线」的产品承诺一致。
 */

/** 将 SVG 字符串编码为 data URI（encodeURIComponent 处理 # < > 等字符）。 */
function svgDataUri(svg: string): string {
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

/** 生成带渐变背景 + 抽象几何元素的装饰 SVG。 */
function art(
  w: number,
  h: number,
  from: string,
  to: string,
  children: string,
): string {
  return svgDataUri(
    `<svg xmlns='http://www.w3.org/2000/svg' width='${w}' height='${h}' viewBox='0 0 ${w} ${h}' preserveAspectRatio='xMidYMid slice'>
  <defs>
    <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
      <stop offset='0' stop-color='${from}'/>
      <stop offset='1' stop-color='${to}'/>
    </linearGradient>
  </defs>
  <rect width='${w}' height='${h}' fill='url(#g)'/>
  ${children}
</svg>`.trim(),
  );
}

/** 研究档案（首页 hero，宽幅 360x240）：靛蓝渐变 + 文档卡片抽象。 */
const ARCHIVE = art(360, 240, "#6366f1", "#4f46e5", `
  <circle cx='308' cy='48' r='64' fill='rgba(255,255,255,0.13)'/>
  <circle cx='56' cy='208' r='84' fill='rgba(255,255,255,0.09)'/>
  <rect x='48' y='56' width='196' height='132' rx='10' fill='rgba(255,255,255,0.16)'/>
  <rect x='70' y='86' width='140' height='9' rx='4.5' fill='rgba(255,255,255,0.55)'/>
  <rect x='70' y='108' width='152' height='6' rx='3' fill='rgba(255,255,255,0.32)'/>
  <rect x='70' y='124' width='120' height='6' rx='3' fill='rgba(255,255,255,0.32)'/>
  <rect x='70' y='140' width='150' height='6' rx='3' fill='rgba(255,255,255,0.32)'/>
`);

/** 实验室（hero，竖幅 240x320）：青蓝渐变 + 烧瓶/圆点抽象。 */
const LAB = art(240, 320, "#06b6d4", "#2563eb", `
  <circle cx='188' cy='58' r='72' fill='rgba(255,255,255,0.13)'/>
  <circle cx='52' cy='256' r='58' fill='rgba(255,255,255,0.10)'/>
  <path d='M96 310 L136 224 L136 186 L120 170 L120 132 L142 132 L142 118 L98 118 L98 132 L120 132 L120 170 L104 186 L104 224 L144 310 Z'
        fill='rgba(255,255,255,0.16)'/>
  <circle cx='121' cy='200' r='8' fill='rgba(255,255,255,0.5)'/>
  <circle cx='121' cy='232' r='6' fill='rgba(255,255,255,0.38)'/>
`);

/** 笔记（hero，小图 240x200）：琥珀渐变 + 便签线条。 */
const NOTES = art(240, 200, "#f59e0b", "#d97706", `
  <circle cx='202' cy='40' r='52' fill='rgba(255,255,255,0.13)'/>
  <rect x='36' y='38' width='168' height='126' rx='10' fill='rgba(255,255,255,0.16)'/>
  <rect x='56' y='62' width='128' height='8' rx='4' fill='rgba(255,255,255,0.55)'/>
  <rect x='56' y='82' width='96' height='6' rx='3' fill='rgba(255,255,255,0.35)'/>
  <rect x='56' y='100' width='120' height='6' rx='3' fill='rgba(255,255,255,0.35)'/>
  <rect x='56' y='118' width='72' height='6' rx='3' fill='rgba(255,255,255,0.35)'/>
  <circle cx='176' cy='140' r='14' fill='rgba(255,255,255,0.4)'/>
`);

/** 首页 hero 装饰图映射（key 保持与旧 picsum seed 语义一致）。 */
export const HERO_IMAGES = {
  archive: ARCHIVE,
  lab: LAB,
  notes: NOTES,
};
