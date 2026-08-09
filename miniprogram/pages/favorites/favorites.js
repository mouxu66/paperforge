const { call } = require('../../utils/cloud');

const PAGE_SIZE = 20;

Page({
  data: {
    list: [],
    page: 0,
    loading: false,
    hasMore: true,
  },

  onShow() {
    // 每次进入刷新，收藏状态可能在详情页被改过
    this.loadData(true);
  },

  onPullDownRefresh() {
    this.loadData(true).then(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (this.data.hasMore && !this.data.loading) this.loadData(false);
  },

  async loadData(reset) {
    if (this.data.loading) return;
    this.setData({ loading: true });
    const page = reset ? 0 : this.data.page + 1;
    try {
      const res = await call('paperList', { favorite: true, page, pageSize: PAGE_SIZE });
      const list = reset ? res.list : this.data.list.concat(res.list);
      this.setData({ list, page, hasMore: list.length < res.total });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },

  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: `/pages/detail/detail?id=${id}` });
  },
});
