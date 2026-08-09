const cloud = require('wx-server-sdk');
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();
const _ = db.command;

// 论文变更：收藏开关 / 加标签 / 加笔记
exports.main = async (event) => {
  const { id, action, payload } = event;
  if (!id) return { code: 1, message: '缺少 id' };
  const coll = db.collection('papers');

  if (action === 'toggleFavorite') {
    const doc = await coll.doc(id).get();
    const cur = !!(doc.data && doc.data.favorite);
    await coll.doc(id).update({ data: { favorite: !cur } });
    return { code: 0, data: { favorite: !cur } };
  }

  if (action === 'addTag' && payload && payload.tag) {
    await coll.doc(id).update({ data: { tags: _.addToSet(payload.tag) } });
    return { code: 0, data: { ok: true } };
  }

  if (action === 'addNote' && payload && payload.content) {
    const note = {
      noteId: Date.now().toString(),
      content: payload.content,
      createdAt: Date.now(),
    };
    await coll.doc(id).update({ data: { notes: _.push(note) } });
    return { code: 0, data: { note } };
  }

  if (action === 'remove') {
    await coll.doc(id).remove();
    return { code: 0, data: { ok: true } };
  }

  return { code: 1, message: '未知操作' };
};
