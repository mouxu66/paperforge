const { call } = require('../../utils/cloud');

Page({
  data: {
    groups: [],
    total: 0,
    scanned: 0,
    checking: false,
    checked: false,
  },

  onLoad() {
    // 进入即自动跑一次
    this.runCheck();
  },

  async runCheck() {
    this.setData({ checking: true });
    try {
      const r = await call('paperDuplicates', {});
      this.setData({
        groups: r.groups,
        total: r.total,
        scanned: r.scanned,
        checked: true,
      });
    } catch (e) {
      wx.showToast({ title: '检测失败', icon: 'none' });
    } finally {
      this.setData({ checking: false });
    }
  },

  async onDelete(e) {
    const { gid, id } = e.currentTarget.dataset;
    wx.showModal({
      title: '删除这篇重复论文？',
      content: '删除后不可恢复，仅删这一条',
      success: async (res) => {
        if (!res.confirm) return;
        try {
          await call('paperMutate', { id, action: 'remove' });
          // 从对应分组移除
          const groups = this.data.groups.slice();
          const g = groups[parseInt(gid, 10)].filter((p) => p._id !== id);
          groups[parseInt(gid, 10)] = g;
          const cleaned = groups.filter((x) => x.length > 1);
          this.setData({ groups: cleaned });
          wx.showToast({ title: '已删除', icon: 'none' });
        } catch (err) {
          wx.showToast({ title: '删除失败', icon: 'none' });
        }
      },
    });
  },

  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: `/pages/detail/detail?id=${id}` });
  },
});
