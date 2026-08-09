const { call } = require('../../utils/cloud');

Page({
  data: {
    a: null,
    b: null,
    showPicker: false,
    candidates: [],
    keyword: '',
  },

  async onLoad(options) {
    try {
      const a = await call('paperDetail', { id: options.a });
      this.setData({ a });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  async onPickTap() {
    this.setData({ showPicker: true, keyword: '' });
    await this.loadCandidates('');
  },

  async loadCandidates(keyword) {
    try {
      const res = await call('paperList', { keyword, page: 0, pageSize: 50 });
      // 排除已经作为 A 的论文
      const candidates = res.list.filter((p) => p._id !== (this.data.a && this.data.a._id));
      this.setData({ candidates });
    } catch (e) {
      this.setData({ candidates: [] });
    }
  },

  onSearchInput(e) {
    this.setData({ keyword: e.detail.value });
    this.loadCandidates(e.detail.value.trim());
  },

  async onSelectB(e) {
    const id = e.currentTarget.dataset.id;
    try {
      const b = await call('paperDetail', { id });
      this.setData({ b, showPicker: false });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    }
  },

  onClosePicker() {
    this.setData({ showPicker: false });
  },

  noop() {},
});
