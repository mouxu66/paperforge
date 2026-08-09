const cloud = require('wx-server-sdk');
const https = require('https');
cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });
const db = cloud.database();

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https
      .get(
        url,
        { headers: { 'User-Agent': 'PaperForge/1.0 (mailto:paperforge@example.com)' } },
        (resp) => {
          let data = '';
          resp.on('data', (chunk) => (data += chunk));
          resp.on('end', () => {
            try {
              resolve(JSON.parse(data));
            } catch (e) {
              reject(e);
            }
          });
        }
      )
      .on('error', reject);
  });
}

// 归一化用于字符串比对：去标点、小写、压缩空白
function norm(s) {
  return (s || '')
    .toLowerCase()
    .replace(/[^a-z0-9一-龥]/g, '')
    .replace(/\s+/g, '')
    .trim();
}

// 名字相似度（Jaccard on 单词集合），>=0.6 视为匹配
function nameSimilarity(a, b) {
  const na = new Set(norm(a).match(/[a-z0-9一-龥]+/g) || []);
  const nb = new Set(norm(b).match(/[a-z0-9一-龥]+/g) || []);
  if (!na.size || !nb.size) return 0;
  let inter = 0;
  na.forEach((w) => nb.has(w) && inter++);
  return inter / (na.size + nb.size - inter);
}

// 引用核验：用 Crossref 权威注册库核对用户填写的 DOI / 标题 / 作者是否对得上
// 这是「零 AI」的硬核验——能抓出乱写、捏造、前后不一致的引用数据
exports.main = async (event) => {
  const { id, doi, title } = event;

  // 若传入库内论文 id，则从云库取该论文的 doi / title
  let lookupDoi = doi;
  let lookupTitle = title;
  if (id && !lookupDoi && !lookupTitle) {
    const doc = await db.collection('papers').doc(id).get().catch(() => null);
    if (doc && doc.data) {
      lookupDoi = doc.data.doi;
      lookupTitle = doc.data.title;
    }
  }

  if (!lookupDoi && !lookupTitle) {
    return { code: 1, message: '缺少 DOI 或标题，无法核验' };
  }

  let crossref = null;
  try {
    if (lookupDoi) {
      const cleanDoi = encodeURIComponent(lookupDoi.replace(/^https?:\/\/doi\.org\//, ''));
      const json = await httpsGet('https://api.crossref.org/works/' + cleanDoi);
      crossref = json && json.message ? json.message : null;
    }
    if (!crossref && lookupTitle) {
      const json = await httpsGet(
        'https://api.crossref.org/works?query.bibliographic=' +
          encodeURIComponent(lookupTitle) +
          '&rows=1'
      );
      const items = (json && json.message && json.message.items) || [];
      crossref = items[0] || null;
    }
  } catch (e) {
    return { code: 2, message: 'Crossref 查询失败', detail: String(e) };
  }

  // 没有在 Crossref 找到 -> DOI 或标题对不上（很可能是捏造/写错）
  if (!crossref) {
    return {
      code: 0,
      data: {
        found: false,
        checked: { doi: lookupDoi, title: lookupTitle },
        titleMatch: false,
        authorMatch: false,
        message: 'Crossref 中未找到匹配记录，DOI 或标题可能错误或不存在',
      },
    };
  }

  const crTitle = (crossref.title && crossref.title[0]) || '';
  const crAuthors = (crossref.author || []).map((a) => a.given + ' ' + a.family).filter(Boolean);
  const crYear = crossref.published
    ? (crossref.published['date-parts'] && crossref.published['date-parts'][0] && crossref.published['date-parts'][0][0])
    : (crossref['published-print'] &&
        crossref['published-print']['date-parts'] &&
        crossref['published-print']['date-parts'][0] &&
        crossref['published-print']['date-parts'][0][0]) || 0;
  const crDoi = crossref.DOI || '';

  const titleMatch = norm(lookupTitle) ? nameSimilarity(lookupTitle, crTitle) >= 0.6 : true;
  // 作者匹配：用户填写作者中至少一个与 Crossref 作者相似即算匹配
  let authorMatch = false;
  if (lookupTitle && crAuthors.length) {
    // 当按标题检索时，没有用户作者列表，仅做标题核对
  }
  if (event.authors && event.authors.length && crAuthors.length) {
    authorMatch = event.authors.some(
      (ua) => crAuthors.some((ca) => nameSimilarity(ua, ca) >= 0.6)
    );
  }

  return {
    code: 0,
    data: {
      found: true,
      checked: { doi: lookupDoi, title: lookupTitle },
      crossref: {
        doi: crDoi,
        title: crTitle,
        authors: crAuthors,
        year: crYear,
        journal: (crossref['container-title'] && crossref['container-title'][0]) || '',
        publisher: crossref.publisher || '',
        type: crossref.type || '',
      },
      titleMatch,
      authorMatch,
      message: titleMatch
        ? '核验通过：标题与 Crossref 注册记录一致'
        : '标题不一致：与 Crossref 注册记录不符，请检查是否写错或捏造',
    },
  };
};
