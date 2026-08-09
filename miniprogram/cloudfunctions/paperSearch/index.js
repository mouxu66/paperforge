const cloud = require('wx-server-sdk');
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();
const _ = db.command;

// 关键词检索：标题 / 摘要 / 作者 三字段 OR 匹配
exports.main = async (event) => {
  const { keyword, page = 0, pageSize = 20 } = event;
  if (!keyword) return { code: 0, data: { list: [], total: 0 } };

  const rx = db.RegExp({ regexp: keyword, options: 'i' });
  const where = _.or([{ title: rx }, { abstract: rx }, { authors: rx }]);

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
