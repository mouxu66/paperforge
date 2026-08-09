const { call } = require('../../utils/cloud');
const { sharePaper } = require('../../utils/share');

Page({
  data: {
    id: '',
    paper: null,
    loading: true,
    showTag: false,
    tagInput: '',
    showNote: false,
    noteInput: '',
    checking: false,
    checkResult: null,
  },

  onLoad(options) {
    this.setData({ id: options.id });
    this.loadDetail();
  },

  async loadDetail() {
    this.setData({ loading: true });
    try {
      const paper = await call('paperDetail', { id: this.data.id });
      this.setData({ paper });
    } catch (e) {
      wx.showToast({ title: '加载失败', icon: 'none' });
    } finally {
      this.setData({ loading: false });
    }
  },

  async onFavTap() {
    const r = await call('paperMutate', { id: this.data.id, action: 'toggleFavorite' });
    this.setData({ 'paper.favorite': r.favorite });
  },

  // 标签
  onTagTap() {
    this.setData({ showTag: true, tagInput: '' });
  },
  onTagInput(e) {
    this.setData({ tagInput: e.detail.value });
  },
  onTagCancel() {
    this.setData({ showTag: false });
  },
  async onTagConfirm() {
    const t = this.data.tagInput.trim();
    if (!t) return;
    await call('paperMutate', { id: this.data.id, action: 'addTag', payload: { tag: t } });
    const tags = this.data.paper.tags.concat(t);
    this.setData({ 'paper.tags': tags, showTag: false });
    wx.showToast({ title: '已添加标签', icon: 'none' });
  },

  // 笔记
  onNoteTap() {
    this.setData({ showNote: true, noteInput: '' });
  },
  onNoteInput(e) {
    this.setData({ noteInput: e.detail.value });
  },
  onNoteCancel() {
    this.setData({ showNote: false });
  },
  async onNoteConfirm() {
    const c = this.data.noteInput.trim();
    if (!c) return;
    const r = await call('paperMutate', { id: this.data.id, action: 'addNote', payload: { content: c } });
    const notes = this.data.paper.notes.concat(r.note);
    this.setData({ 'paper.notes': notes, showNote: false });
    wx.showToast({ title: '已保存笔记', icon: 'none' });
  },

  // 引用核验（零 AI）
  async onCheckTap() {
    this.setData({ checking: true, checkResult: null });
    try {
      const r = await call('citationCheck', { id: this.data.id });
      this.setData({ checkResult: r });
    } catch (e) {
      wx.showToast({ title: '核验失败', icon: 'none' });
    } finally {
      this.setData({ checking: false });
    }
  },

  goCompare() {
    wx.navigateTo({ url: `/pages/compare/compare?a=${this.data.id}` });
  },

  noop() {},

  onShareAppMessage() {
    return sharePaper(this.data.paper);
  },
});
