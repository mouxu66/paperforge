/**
 * 判断 URL 是否来自受支持的论文站点。
 *
 * 注意：utils.js 必须保持为经典脚本（classic script），因为 background.js
 * 通过 importScripts('utils.js') 加载它。不要将其转换为 ES Module。
 */
function isValidClipperUrl(url) {
  try {
    const u = new URL(url);
    const hostname = u.hostname.toLowerCase();
    return (
      hostname.endsWith("arxiv.org") ||
      hostname.endsWith("cnki.net") ||
      hostname.endsWith("cnki.com.cn") ||
      hostname.startsWith("scholar.google")
    );
  } catch {
    return false;
  }
}

/**
 * 根据 URL 检测论文来源站点。
 */
function detectSource(url) {
  try {
    const hostname = new URL(url).hostname.toLowerCase();
    if (hostname.endsWith("arxiv.org")) return "arxiv";
    if (hostname.endsWith("cnki.net") || hostname.endsWith("cnki.com.cn")) return "cnki";
    if (hostname.startsWith("scholar.google")) return "google_scholar";
  } catch {}
  return "other";
}

// 兼容 Node 测试与浏览器扩展两种环境
if (typeof module !== "undefined" && module.exports) {
  module.exports = { isValidClipperUrl, detectSource };
}
