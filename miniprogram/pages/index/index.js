const { call } = require('../../utils/cloud');
const { sharePaper } = require('../../utils/share');

const PAGE_SIZE = 20;

Page({
  data: {
    list: [],
    keyword: '',
    activeCat: 'all',
    cats: [
      { key: 'all', label: '全部' },
      { key: 'imported', label: '导入' },
      { key: 'report', label: '报告' },
      { key: 'my', label: '我的' },
    ],
    page: 0,
    loading: false,
    hasMore: true,
    showImport: false,
    importQuery: '',
    importing: false,
  },

  onLoad() {
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
      let res;
      if (this.data.keyword.trim()) {
        // 关键词检索：标题/摘要/作者 三字段 OR
        res = await call('paperSearch', {
          keyword: this.data.keyword.trim(),
          page,
          pageSize: PAGE_SIZE,
        });
      } else {
        // 分类浏览
        res = await call('paperList', {
          category: this.data.activeCat,
          page,
          pageSize: PAGE_SIZE,
        });
      }
      const list = reset ? res.list : this.data.list.concat(res.list);
      this.setData({
        list,
        page,
        hasMore: list.length < res.total,
      });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },

  onSearchInput(e) {
    this.setData({ keyword: e.detail.value });
  },

  onSearchConfirm() {
    this.loadData(true);
  },

  onClearKeyword() {
    this.setData({ keyword: '' }, () => this.loadData(true));
  },

  onCategoryTap(e) {
    const key = e.currentTarget.dataset.key;
    if (key === this.data.activeCat) return;
    this.setData({ activeCat: key }, () => this.loadData(true));
  },

  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: `/pages/detail/detail?id=${id}` });
  },

  // 列表内快捷收藏切换
  async onFavTap(e) {
    const { id, idx } = e.currentTarget.dataset;
    try {
      const r = await call('paperMutate', { id, action: 'toggleFavorite' });
      const key = `list[${idx}].favorite`;
      this.setData({ [key]: r.favorite });
    } catch (err) {
      wx.showToast({ title: '操作失败', icon: 'none' });
    }
  },

  // 导入弹窗
  onImportTap() {
    this.setData({ showImport: true, importQuery: '' });
  },
  onImportInput(e) {
    this.setData({ importQuery: e.detail.value });
  },
  onImportCancel() {
    this.setData({ showImport: false });
  },
  async onImportConfirm() {
    const q = this.data.importQuery.trim();
    if (!q) return;
    this.setData({ importing: true });
    try {
      const r = await call('paperImport', { query: q, perPage: 20 });
      wx.showToast({ title: `已导入 ${r.inserted} 篇`, icon: 'none' });
      this.setData({ showImport: false, keyword: '', activeCat: 'all' });
      this.loadData(true);
    } catch (err) {
      wx.showToast({ title: '导入失败', icon: 'none' });
    } finally {
      this.setData({ importing: false });
    }
  },

  // 阻止弹窗内部点击透传到遮罩（关闭）
  noop() {},

  onShareAppMessage() {
    return { title: 'PaperForge 论文库', path: '/pages/index/index' };
  },
});
