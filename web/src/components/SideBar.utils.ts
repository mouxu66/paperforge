/** 分类 key → 中文显示名对照表
 *
 * 采用中国教育部《学位授予和人才培养学科目录》的 14 大学科门类，
 * 同时保留对历史/source-based key 的兼容映射，避免老数据出现空白标签。
 */
export const CATEGORY_LABELS: Record<string, string> = {
  // 静态项
  all: "全部论文",

  // ── 14 大学科门类 ─────────────────────────────────────────
  philosophy: "哲学",
  economics: "经济学",
  law: "法学",
  education: "教育学",
  literature: "文学",
  history: "历史学",
  science: "理学",
  engineering: "工学",
  agriculture: "农学",
  medicine: "医学",
  military: "军事学",
  management: "管理学",
  arts: "艺术学",
  interdisciplinary: "交叉学科",

  // ── 兼容映射：常见 arXiv 子类 → 独立中文名 ────────────
  cs: "计算机科学",
  computer_science: "计算机科学",
  ai: "人工智能",
  artificial_intelligence: "人工智能",
  llm: "大语言模型",
  cv: "计算机视觉",
  nlp: "自然语言处理",
  lora: "LoRA微调",
  quant: "量化计算",
  ri: "信息检索",
  rl: "强化学习",
  comm: "通信工程",
  audio: "音频处理",
  speech: "语音识别",
  agent: "智能体",
  agents: "智能体",
  robot: "机器人学",
  robotics: "机器人学",
  ml: "机器学习",
  ir: "信息检索",
  se: "软件工程",
  networks: "计算机网络",
  math: "理学",
  multimodal: "工学",
  gan: "工学",
  gen: "工学",
  generation: "工学",
  diffusion: "工学",
  transformer: "工学",
  bert: "工学",
  gpt: "工学",
  rag: "工学",
  search: "工学",
  db: "数据库",
  database: "工学",
  graph: "工学",
  kg: "工学",
  knowledge_graph: "工学",
  time_series: "工学",
  optimization: "工学",
  security: "信息安全",
  privacy: "工学",
  systems: "分布式系统",
  cloud: "工学",
  edge: "工学",
  federated: "工学",
  hardware: "硬件架构",
  software: "工学",
  code: "工学",
  programming: "工学",
  algorithm: "工学",
  theory: "理学",
  data_mining: "工学",
  bio: "医学",
  biology: "理学",
  medical: "医学",
  chemistry: "理学",
  physics: "理学",
  quantum: "理学",
  neuroscience: "医学",
  social: "法学",
  hci: "人机交互",
  finance: "经济学",
  environment: "农学",
  climate: "理学",
  energy: "工学",
  game: "经济学",

  // ── 兼容映射：source-based 旧分类 ──────────────────────────
  // 这些分类来自早期按来源/出处分类的实现，现在仅作为兜底，
  // 后续导入新论文时会写入真正的学科门类。
  arxiv: "工学",
  upload: "交叉学科",
  cnki: "交叉学科",
  google_scholar: "交叉学科",
  web_clipper: "交叉学科",
  pubmed: "医学",
  ieee: "工学",
  springer: "交叉学科",
};

/** 将数据库分类 key 转为中文显示名（优先查表，未命中时原样格式化）。 */
export function formatLabel(key: string): string {
  if (!key) return "";
  // 优先从对照表取中文名
  if (CATEGORY_LABELS[key]) return CATEGORY_LABELS[key];
  // 未配置映射的分类：分隔符换空格 + 每词首字母大写
  return key.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
