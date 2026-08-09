// 分享配置：把一篇论文甩进微信群 / 朋友圈
function sharePaper(paper) {
  if (!paper) return { title: 'PaperForge 论文库', path: '/pages/index/index' };
  return {
    title: paper.title || '一篇论文',
    path: `/pages/detail/detail?id=${paper._id}`,
  };
}

module.exports = { sharePaper };
