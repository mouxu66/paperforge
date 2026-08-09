const cloud = require('wx-server-sdk');
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });

exports.main = async () => ({ code: 0, data: { ok: true, time: Date.now() } });
