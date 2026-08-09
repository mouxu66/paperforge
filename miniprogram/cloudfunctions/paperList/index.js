const cloud = require('wx-server-sdk');
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

// 论文列表：支持分类、收藏筛选、标题关键词、分页
exports.main = async (event) => {
  const { category, favorite, keyword, page = 0, pageSize = 20 } = event;
  const where = {};
  if (category && category !== 'all') where.category = category;
  if (favorite) where.favorite = true;
  if (keyword) where.title = db.RegExp({ regexp: keyword, options: 'i' });

  const coll = db.collection('papers');
  const countRes = await coll.where(where).count();
  const listRes = await coll
    .where(where)
    .orderBy('createdAt', 'desc')
    .skip(page * pageSize)
    .limit(pageSize)
    .get();

  return { code: 0, data: { list: listRes.data, total: countRes.total } };
};
