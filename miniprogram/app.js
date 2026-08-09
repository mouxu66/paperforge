// PaperForge 小程序 · 入口
// 重要：把 ENV_ID 换成你在「云开发控制台」看到的环境 ID（形如 xxxxxx-paperforge）
const ENV_ID = 'your-cloud-env-id';

App({
  globalData: {
    envId: ENV_ID,
  },
  onLaunch() {
    if (!wx.cloud) {
      console.error('当前基础库不支持云开发，请使用 2.2.3 或以上版本');
      return;
    }
    wx.cloud.init({
      env: ENV_ID,
      traceUser: true,
    });
  },
});
