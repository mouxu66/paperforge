const cloud = require('wx-server-sdk');
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

// 论文详情
exports.main = async (event) => {
  const { id } = event;
  if (!id) return { code: 1, message: '缺少 id' };
  const res = await db.collection('papers').doc(id).get();
  return { code: 0, data: res.data };
};
