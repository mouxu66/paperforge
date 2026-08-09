// 统一云函数调用封装：成功返回 data，失败 reject
function call(name, data = {}) {
  return new Promise((resolve, reject) => {
    wx.cloud.callFunction({
      name,
      data,
      success: (res) => {
        const result = res.result || {};
        if (result.code === 0) {
          resolve(result.data);
        } else {
          reject(new Error(result.message || '调用失败'));
        }
      },
      fail: (err) => reject(err),
    });
  });
}

module.exports = { call };
