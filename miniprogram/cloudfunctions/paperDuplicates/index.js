const cloud = require('wx-server-sdk');
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

// 归一化标题：去标点、小写、去空白，用于基于标题的重复判定（规则实现，零 AI）
function norm(t) {
  return (t || '')
    .toLowerCase()
    .replace(/[^a-z0-9一-龥]/g, '')
    .trim();
}

// 扫描库内标题完全相同的论文，返回重复分组
exports.main = async () => {
  const MAX = 1000; // 单次查询上限，库更大时可分页累加
  const res = await db.collection('papers').limit(MAX).get();
  const map = {};
  (res.data || []).forEach((p) => {
    const k = norm(p.title);
    if (!k) return;
    if (!map[k]) map[k] = [];
    map[k].push({ _id: p._id, title: p.title, year: p.year });
  });
  const groups = Object.keys(map)
    .map((k) => map[k])
    .filter((g) => g.length > 1);
  return {
    code: 0,
    data: { groups, total: (res.data || []).length, scanned: MAX },
  };
};
