const cloud = require('wx-server-sdk');
const https = require('https');
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https
      .get(url, (resp) => {
        let data = '';
        resp.on('data', (chunk) => (data += chunk));
        resp.on('end', () => {
          try {
            resolve(JSON.parse(data));
          } catch (e) {
            reject(e);
          }
        });
      })
      .on('error', reject);
  });
}

// 把 OpenAlex 的倒排摘要还原成正常文本
function reconstruct(inv) {
  if (!inv) return '';
  const words = [];
  Object.keys(inv).forEach((w) => {
    inv[w].forEach((pos) => (words[pos] = w));
  });
  return words.join(' ');
}

// 从 OpenAlex 检索并写入云数据库（云端代理，规避 CORS / 速率限制）
exports.main = async (event) => {
  const { query, perPage = 10 } = event;
  if (!query) return { code: 1, message: '缺少查询词' };

  const url =
    'https://api.openalex.org/works?search=' +
    encodeURIComponent(query) +
    '&per-page=' + perPage +
    '&mailto=paperforge@example.com';

  const json = await httpsGet(url);
  const results = (json && json.results) || [];
  let inserted = 0;

  for (const r of results) {
    const paperId = r.doi || r.id;
    if (!paperId) continue;
    const authors = (r.authorships || [])
      .map((a) => a.author && a.author.display_name)
      .filter(Boolean);
    const src = (r.primary_location && r.primary_location.source) || {};
    const paper = {
      _id: paperId,
      title: r.title || '无标题',
      authors,
      abstract: reconstruct(r.abstract_inverted_index),
      year: r.publication_year || 0,
      journal: src.display_name || '',
      category: 'imported',
      tags: [],
      favorite: false,
      notes: [],
      citations: r.cited_by_count || 0,
      source: 'openalex',
      doi: r.doi || '',
      url: (r.primary_location && r.primary_location.landing_page_url) || r.id,
      pdfUrl: '',
      createdAt: Date.now(),
    };
    try {
      await db.collection('papers').doc(paperId).set(paper); // 幂等写入
      inserted += 1;
    } catch (e) {
      // 已存在则跳过
    }
  }

  return { code: 0, data: { inserted, total: results.length } };
};
