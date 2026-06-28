"""PaperForge mock 后端 Pydantic 响应模型。

字段使用 camelCase，与前端 TypeScript 类型保持一致。
原 models.py 中的 Pydantic 模型已迁移至此，models.py 改为存放 SQLAlchemy ORM。
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


Source = Literal["arxiv", "pubmed", "ieee", "springer"]


class Paper(BaseModel):
    id: str
    title: str
    authors: list[str]
    year: int
    abstract: str
    category: str
    tags: list[str] = Field(default_factory=list)
    citations: int = 0
    chunkCount: int = 0
    indexSize: int = 0  # bytes
    pdfUrl: str = ""
    source: Source = "arxiv"
    journal: str = ""
    favorited: bool = False
    # FTS5 snippet() 返回的高亮片段（仅搜索建议接口填充，普通接口为 None）
    highlight: Optional[str] = None
    # B2: Semantic Scholar 富化字段（未富化时为 None）
    influentialCitations: Optional[int] = None
    fieldsOfStudy: Optional[list[str]] = None


class PageResult(BaseModel):
    items: list[Paper]
    total: int
    page: int
    pageSize: int


class LibraryStats(BaseModel):
    totalPapers: int
    totalChunks: int
    totalSize: int
    byCategory: list[dict]


class FavoriteToggle(BaseModel):
    favorited: bool


class FavoriteCreate(BaseModel):
    """POST /api/favorites 请求体。"""
    paper_id: str


# ==================== LLM 模型管理（自定义配置 CRUD） ====================


class LLMConfigCreate(BaseModel):
    """POST /api/models 请求体 —— 新增模型配置。"""
    displayName: str
    apiUrl: str
    apiKey: str = ""
    modelId: str
    enabled: bool = True


class LLMConfigUpdate(BaseModel):
    """PUT /api/models/{id} 请求体 —— 更新模型配置（所有字段可选）。"""
    displayName: Optional[str] = None
    apiUrl: Optional[str] = None
    apiKey: Optional[str] = None
    modelId: Optional[str] = None
    enabled: Optional[bool] = None


class LLMConfigResponse(BaseModel):
    """模型配置响应（列表/单条共用）。API Key 脱敏返回。"""
    id: int
    displayName: str
    apiUrl: str
    apiKey: str  # 脱敏后的 key（如 sk-***1234），前端不回传原 key
    modelId: str
    enabled: bool
    provider: str  # 自动识别的 provider 名称（openai/zhipu/deepseek）
    createdAt: str = ""
    updatedAt: str = ""


class ModelInfo(BaseModel):
    """单个可用模型信息（从 DB 加载，用于下拉菜单）。"""
    value: str  # DB 记录的 id（字符串形式）
    label: str  # display_name
    provider: str
    model: str  # model_id


class CurrentModelResponse(BaseModel):
    """GET /api/model/current 响应。"""
    current: str
    label: str
    available: list[ModelInfo]
    enabled: bool = True


class SwitchModelRequest(BaseModel):
    """POST /api/model/switch 请求体。"""
    model: str  # LLMConfig.id 的字符串形式


class SwitchModelResponse(BaseModel):
    """POST /api/model/switch 响应。"""
    success: bool
    current: str
    label: str
    message: str = ""


# ==================== 问答 / 综述生成 ====================


class AskRequest(BaseModel):
    """POST /api/ask 请求体。"""
    question: str
    paperIds: list[str] = Field(default_factory=list)  # 限定检索范围，空表示全库


class AskResponse(BaseModel):
    """POST /api/ask 响应。"""
    answer: str
    references: list[dict] = Field(default_factory=list)
    model: str = ""  # 实际使用的模型 label


class GenerateRequest(BaseModel):
    """POST /api/generate 请求体。"""
    topic: str
    paperIds: list[str] = Field(default_factory=list)


class GenerateResponse(BaseModel):
    """POST /api/generate 响应。"""
    content: str
    references: list[dict] = Field(default_factory=list)
    model: str = ""


# ==================== PDF 上传 ====================


class UploadedPaper(BaseModel):
    """单篇 PDF 解析结果。"""
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    abstract: str = ""
    source: str = "upload"
    success: bool = True
    error: str = ""


class UploadBatchResponse(BaseModel):
    """POST /api/upload-batch 响应。"""
    results: list[UploadedPaper]
    successCount: int
    failCount: int


# ==================== 打包教师版 ====================


class PackageResponse(BaseModel):
    """POST /api/admin/package 响应。"""
    success: bool
    message: str = ""
    downloadUrl: str = ""


# ==================== arXiv 检索 / 导入 ====================


class ArxivSearchRequest(BaseModel):
    keyword: str
    maxResults: int = 10


class ArxivPaperPreview(BaseModel):
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    abstract: str = ""
    pdfUrl: str = ""
    source: str = "arxiv"
    category: str = "arxiv"
    tags: list[str] = Field(default_factory=list)


class ArxivSearchResponse(BaseModel):
    items: list[ArxivPaperPreview]
    total: int


class ArxivImportRequest(BaseModel):
    papers: list[ArxivPaperPreview]


class ArxivImportResponse(BaseModel):
    results: list[UploadedPaper]
    successCount: int
    failCount: int


# ==================== Zotero 导入（B3） ====================
class ZoteroImportRequest(BaseModel):
    """POST /api/zotero/import 请求体。"""
    userId: str
    apiKey: str = ""


class ZoteroImportResponse(BaseModel):
    """Zotero 导入结果。"""
    results: list[UploadedPaper]
    successCount: int
    failCount: int
    totalFetched: int  # 从 Zotero 拉取的条目总数


# ==================== PDF 批注/高亮（B4） ====================
class PdfAnnotationCreate(BaseModel):
    """POST /api/pdf-annotations 请求体。"""
    paperId: str
    page: int
    quadpoints: list  # JSON 数组，存储四边形坐标
    color: str = "#FFEB3B"
    note: str = ""


class PdfAnnotationResponse(BaseModel):
    """批注响应。"""
    id: str
    paperId: str
    page: int
    quadpoints: list
    color: str
    note: str
    createdAt: str


class PdfAnnotationUpdate(BaseModel):
    """PATCH /api/pdf-annotations/{id} 请求体。"""
    color: Optional[str] = None
    note: Optional[str] = None


# ==================== 论文写作工作台 ====================


class ProjectCreate(BaseModel):
    """POST /api/writing/projects 请求体。"""
    title: str
    keywords: list[str] = Field(default_factory=list)
    targetJournal: str = ""
    templateId: Optional[str] = None  # 指定模板时自动创建大纲章节


class ProjectUpdate(BaseModel):
    """PUT /api/writing/projects/{id} 请求体（所有字段可选）。"""
    title: Optional[str] = None
    keywords: Optional[list[str]] = None
    targetJournal: Optional[str] = None
    targetWordCount: Optional[int] = None  # 目标字数（0=未设定）


class GenerateOutlineRequest(BaseModel):
    """POST /api/writing/generate-outline 请求体（P1 自动生成大纲）。"""
    projectId: int
    topic: str
    keywords: list[str] = Field(default_factory=list)


class ProjectResponse(BaseModel):
    """写作项目响应。"""
    id: int
    title: str
    keywords: list[str] = Field(default_factory=list)
    targetJournal: str = ""
    targetWordCount: int = 0
    chapterCount: int = 0
    createdAt: str = ""
    updatedAt: str = ""


class ChapterCreate(BaseModel):
    """POST /api/writing/projects/{id}/chapters 请求体。"""
    title: str
    parentId: Optional[int] = None  # 父章节 ID，None 表示根章节
    content: str = ""
    order: int = 0


class ChapterUpdate(BaseModel):
    """PUT /api/writing/chapters/{id} 请求体（所有字段可选）。"""
    title: Optional[str] = None
    content: Optional[str] = None
    order: Optional[int] = None


class ChapterMove(BaseModel):
    """PUT /api/writing/chapters/{id}/move 请求体（拖拽排序）。"""
    parentId: Optional[int] = None  # 目标父章节，None 表示移到根
    order: int = 0  # 在同级中的新顺序


class ChapterResponse(BaseModel):
    """章节响应（单条，扁平结构）。"""
    id: int
    projectId: int
    parentId: Optional[int] = None
    title: str
    content: str = ""
    order: int = 0
    createdAt: str = ""
    updatedAt: str = ""


class ChapterTreeNode(ChapterResponse):
    """章节树节点（递归包含子章节，用于大纲树渲染）。"""
    children: list["ChapterTreeNode"] = Field(default_factory=list)


class ChapterGenerateResponse(BaseModel):
    """POST /api/writing/.../generate 响应。"""
    chapterId: int
    content: str  # LLM 生成的 Markdown 内容
    model: str  # 使用的模型标签


class ExportReference(BaseModel):
    """导出时解析出的参考文献条目（对应正文中的 [@id] 引用）。"""
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0


class ExportResponse(BaseModel):
    """POST /api/writing/projects/{id}/export 响应。"""
    content: str  # 拼接后的 Markdown 全文（[@id] 已替换为 [N]）
    filename: str  # 建议的下载文件名
    references: list[ExportReference] = Field(default_factory=list)


class ExportTaskCreateResponse(BaseModel):
    """POST /api/writing/projects/{id}/export 异步任务创建响应。"""
    taskId: str
    status: str = "pending"


class ExportTaskProgressResponse(BaseModel):
    """GET /api/writing/export/{task_id}/progress 响应。"""
    taskId: str
    progress: int  # 0-100
    status: str  # pending | running | done | error
    content: str = ""
    filename: str = ""
    references: list[ExportReference] = Field(default_factory=list)
    error: str = ""
    # P2-2: 分块进度（用于前端显示「正在处理第 X/Y 章节」）
    currentChapter: int = 0
    totalChapters: int = 0


# ==================== 写作模板 ====================


class TemplateChapter(BaseModel):
    """模板中的章节节点（递归包含子章节）。"""
    title: str
    children: list["TemplateChapter"] = Field(default_factory=list)


class TemplateResponse(BaseModel):
    """写作模板响应。"""
    id: str
    name: str
    description: str = ""
    chapters: list[TemplateChapter] = Field(default_factory=list)


# ==================== 字数统计 ====================


class WordCountItem(BaseModel):
    """单章节字数统计。"""
    id: int
    title: str
    wordCount: int


class WordCountResponse(BaseModel):
    """项目字数统计响应。"""
    total: int
    chapters: list[WordCountItem]


# 递归自引用（ChapterTreeNode.children: list["ChapterTreeNode"]）需要 rebuild 解析
ChapterTreeNode.model_rebuild()
TemplateChapter.model_rebuild()


# ==================== 论文笔记 ====================


class NoteCreate(BaseModel):
    """POST /api/papers/notes 请求体。"""
    paperId: str
    projectId: Optional[int] = None  # 可选：关联写作项目
    content: str = ""


class NoteUpdate(BaseModel):
    """PUT /api/papers/notes/{id} 请求体。"""
    content: str = ""


class NoteResponse(BaseModel):
    """笔记响应。"""
    id: str
    paperId: str
    projectId: Optional[int] = None
    content: str = ""
    createdAt: str = ""
    updatedAt: str = ""


# ==================== 用户自定义模板 ====================


class UserTemplateCreate(BaseModel):
    """POST /api/writing/templates/user 请求体。"""
    name: str
    description: str = ""
    chapters: list[TemplateChapter] = Field(default_factory=list)


class UserTemplateResponse(BaseModel):
    """用户自定义模板响应。"""
    id: str
    name: str
    description: str = ""
    chapters: list[TemplateChapter] = Field(default_factory=list)
    createdAt: str = ""


# ==================== 写作统计 ====================


class DailyWordTrend(BaseModel):
    """单日字数趋势点。"""
    date: str  # 'YYYY-MM-DD'
    wordCount: int


class WritingStatsResponse(BaseModel):
    """GET /api/writing/stats 响应。"""
    totalProjects: int
    totalWords: int
    todayWords: int  # 今日新增字数（今日快照 - 昨日快照，仅统计有快照的项目）
    trend: list[DailyWordTrend]  # 近 7 天每日字数变化


# ==================== 章节历史版本 ====================


class ChapterVersionResponse(BaseModel):
    """章节历史版本响应（列表项，不含完整 content）。"""
    id: str
    chapterId: int
    wordCount: int
    createdAt: str = ""


class ChapterVersionDetail(BaseModel):
    """章节历史版本详情（含完整 content）。"""
    id: str
    chapterId: int
    content: str
    wordCount: int
    createdAt: str = ""


class ChapterRestoreResponse(BaseModel):
    """恢复章节版本响应。"""
    chapterId: int
    versionId: str
    content: str
    wordCount: int


# ==================== 引用智能推荐 ====================
class RecommendCitationsRequest(BaseModel):
    """POST /api/writing/chapters/{id}/recommend-citations 请求体。"""
    content: str


class RecommendedPaper(BaseModel):
    """推荐论文条目。"""
    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    score: float = 0.0


class RecommendCitationsResponse(BaseModel):
    """引用推荐响应。"""
    papers: list[RecommendedPaper] = Field(default_factory=list)
