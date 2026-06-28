// 论文领域分类，用于侧边栏
export interface Category {
  key: string
  label: string
  icon: string
}

export const CATEGORIES: Category[] = [
  { key: 'all', label: '全部论文', icon: '📚' },
  { key: 'llm', label: '大语言模型', icon: '🧠' },
  { key: 'lora', label: '参数高效微调', icon: '🔧' },
  { key: 'rl', label: '强化学习', icon: '🎯' },
  { key: 'cv', label: '计算机视觉', icon: '👁️' },
  { key: 'comm', label: '通信与信号', icon: '📡' },
  { key: 'quant', label: '模型量化', icon: '⚖️' },
]

// 排序选项
export const SORT_OPTIONS = [
  { label: '最新发表', value: 'year_desc' },
  { label: '最早发表', value: 'year_asc' },
  { label: '引用最多', value: 'citations_desc' },
  { label: '文本块最多', value: 'chunks_desc' },
] as const

export const PAGE_SIZE = 8

// 论文来源 → antd Tag color（PaperCard / DetailPage 共用）
export const SOURCE_COLOR: Record<string, string> = {
  arxiv: 'blue',
  pubmed: 'green',
  ieee: 'orange',
  springer: 'purple',
}

// 分类 → 学科色彩（PaperCard 圆点指示器 / SideBar 侧边栏指示器共用）
export const CATEGORY_COLOR: Record<string, string> = {
  all: '#94a3b8',
  llm: '#1e40af',
  lora: '#3b82f6',
  rl: '#8b5cf6',
  cv: '#ec4899',
  comm: '#10b981',
  quant: '#f59e0b',
}
