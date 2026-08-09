const { call } = require('../../utils/cloud');

Page({
  data: {
    total: 0,
    favorites: 0,
    loading: true,
    showAbout: false,
  },

  async onShow() {
    this.setData({ loading: true });
    try {
      const all = await call('paperList', { page: 0, pageSize: 1 });
      const fav = await call('paperList', { favorite: true, page: 0, pageSize: 1 });
      this.setData({ total: all.total, favorites: fav.total });
    } catch (e) {
      // 静默
    } finally {
      this.setData({ loading: false });
    }
  },

  goDuplicates() {
    wx.navigateTo({ url: '/pages/duplicates/duplicates' });
  },

  toggleAbout() {
    this.setData({ showAbout: !this.data.showAbout });
  },

  noop() {},
});
