"""PaperForge mock 后端 Pydantic 响应模型。

字段使用 camelCase，与前端 TypeScript 类型保持一致。
原 models.py 中的 Pydantic 模型已迁移至此，models.py 改为存放 SQLAlchemy ORM。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

OCRStatus = Literal["pending", "done", "failed"]

Source = Literal[
    "arxiv",
    "pubmed",
    "ieee",
    "springer",
    "upload",
    "cnki",
    "google_scholar",
    "web_clipper",
    "zotero",
    # PeerRead 校准语料（depth_calibration 回填）；缺失时 paper_to_schema 会
    # Pydantic 校验失败导致含该来源记录的分页接口整体 500。
    "peerread",
]


class Paper(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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
    highlight: str | None = None
    # B2: Semantic Scholar 富化字段（未富化时为 None）
    influentialCitations: int | None = None
    fieldsOfStudy: list[str] | None = None
    # WP-5.1: DOI（从 PDF 全文或元数据提取）
    doi: str | None = None
    # Reflection 报告关联的原论文（仅 category='report' 时有效）
    sourcePaperId: str | None = Field(default=None, alias="source_paper_id")
    sourcePaperStatus: str | None = Field(default=None, alias="source_paper_status")
    # WP-1.2: OCR 状态与扫描件标识
    ocrStatus: str | None = Field(default=None, alias="ocr_status")
    isScanned: bool | None = Field(default=None, alias="is_scanned")


class PageResult(BaseModel):
    items: list[Paper]
    total: int
    page: int
    pageSize: int


class LibraryStats(BaseModel):
    totalPapers: int
    reportCount: int = 0
    reportAvgScore: float = 0.0
    reportAvgFidelity: float = 0.0
    totalChunks: int
    totalSize: int
    byCategory: list[dict]
    bySource: list[dict] = Field(
        default_factory=list,
        description="各来源论文数量，如 [{'source': 'arxiv', 'count': 347}, ...]",
    )


class QFHealthResponse(BaseModel):
    """DEPTH 图链路(QF)健康面板 —— 按需聚合，不排定时任务。"""

    totalPapers: int
    papersWithFigures: int
    coverage: float = Field(..., ge=0.0, le=1.0)
    qfMean: float = 0.0
    qfStd: float = 0.0
    histogram: list[dict] = Field(default_factory=list)
    m0Active: bool = False
    figureCount: int = 0
    # Rec2: 真实图相关 KPI（按 figure_number 去重，抵消 detector 过度分割污染）
    realFigureCount: int = 0
    captionedFigureCount: int = 0
    captionCoverage: float = 0.0


class PaperFigureResponse(BaseModel):
    """单张 figure 的详情响应（含 axis_info / source_text_span / claim_validation）。"""

    id: int
    paperId: str = Field(..., alias="paper_id")
    page: int
    figureIndex: int = Field(..., alias="figure_index")
    figureNumber: int | None = Field(default=None, alias="figure_number")
    figurePath: str = Field(..., alias="figure_path")
    ocrText: str = Field(default="", alias="ocr_text")
    captionText: str | None = Field(default=None, alias="caption_text")
    qwenSummary: str | None = Field(default=None, alias="qwen_summary")
    source: str = "bitmap"
    sourceTextSpan: str | None = Field(default=None, alias="source_text_span")
    axisInfo: dict | None = Field(default=None, alias="axis_info")
    claimValidation: dict | None = Field(default=None, alias="claim_validation")
    curvePoints: list[dict] | None = Field(default=None, alias="curve_points")
    imageUrl: str | None = Field(default=None, alias="image_url")


class PaperTimelineStat(BaseModel):
    """Rec2 时间轴：单篇论文的 caption coverage before/after。"""

    paperId: str
    oldTotal: int = 0
    oldCaptioned: int = 0
    newTotal: int = 0
    newCaptioned: int = 0
    oldCoverage: float = 0.0
    newCoverage: float = 0.0


class QFTimelineResponse(BaseModel):
    """Rec2 时间轴前后对比：旧 KPI vs 新 KPI。"""

    oldKpiTotalClusters: int = 0
    oldKpiCaptioned: int = 0
    newKpiRealFigures: int = 0
    newKpiCaptionedReal: int = 0
    coverageBefore: float = 0.0
    coverageAfter: float = 0.0
    aggregateDelta: float = 0.0
    paperBreakdown: list[PaperTimelineStat] = Field(default_factory=list)


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

    displayName: str | None = None
    apiUrl: str | None = None
    apiKey: str | None = None
    modelId: str | None = None
    enabled: bool | None = None


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
    # WP-1.2: 解析阶段即透出 OCR 状态，方便上传列表实时反馈
    ocr_status: OCRStatus | None = None
    is_scanned: bool | None = None


class UploadBatchResponse(BaseModel):
    """POST /api/upload-batch 响应。"""

    results: list[UploadedPaper]
    successCount: int
    failCount: int
    unsupportedCount: int = 0


class ZipUploadResponse(BaseModel):
    """POST /api/upload-zip 响应。"""

    total: int = Field(description="ZIP 包内 PDF 文件总数")
    succeeded: int = Field(description="成功入库的论文数")
    failed: int = Field(description="解析/入库失败的论文数")
    failed_files: list[dict] = Field(
        default_factory=list,
        description="失败文件列表 [{filename: str, error: str}]",
    )
    papers: list[UploadedPaper] = Field(default_factory=list, description="已入库的论文列表")


# ==================== 打包桌面版 ====================


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


# ==================== Zotero Watchdog 目录监控 ====================


class ZoteroWatchConfig(BaseModel):
    """Zotero 目录监控配置。"""

    watchDir: str = Field(default="", description="Zotero 本地存储目录路径，留空表示不启用")
    enabled: bool = Field(default=False, description="是否启用自动监控")


class ZoteroWatchStatus(BaseModel):
    """GET /api/zotero/watch-status 响应。"""

    enabled: bool
    watchDir: str = ""
    running: bool = False  # 监控是否实际在运行
    lastEvent: str | None = None  # 最近一次文件变更时间
    autoImported: int = 0  # 已自动导入的论文数


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

    color: str | None = None
    note: str | None = None


# ==================== 论文写作工作台 ====================


class ProjectCreate(BaseModel):
    """POST /api/writing/projects 请求体。"""

    title: str
    keywords: list[str] = Field(default_factory=list)
    targetJournal: str = ""
    templateId: str | None = None  # 指定模板时自动创建大纲章节


class ProjectUpdate(BaseModel):
    """PUT /api/writing/projects/{id} 请求体（所有字段可选）。"""

    title: str | None = None
    keywords: list[str] | None = None
    targetJournal: str | None = None
    targetWordCount: int | None = None  # 目标字数（0=未设定）


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
    parentId: int | None = None  # 父章节 ID，None 表示根章节
    content: str = ""
    order: int = 0


class ChapterUpdate(BaseModel):
    """PUT /api/writing/chapters/{id} 请求体（所有字段可选）。"""

    title: str | None = None
    content: str | None = None
    order: int | None = None


class ChapterMove(BaseModel):
    """PUT /api/writing/chapters/{id}/move 请求体（拖拽排序）。"""

    parentId: int | None = None  # 目标父章节，None 表示移到根
    order: int = 0  # 在同级中的新顺序


class ChapterResponse(BaseModel):
    """章节响应（单条，扁平结构）。"""

    id: int
    projectId: int
    parentId: int | None = None
    title: str
    content: str = ""
    order: int = 0
    createdAt: str = ""
    updatedAt: str = ""


class ChapterTreeNode(ChapterResponse):
    """章节树节点（递归包含子章节，用于大纲树渲染）。"""

    children: list[ChapterTreeNode] = Field(default_factory=list)


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
    children: list[TemplateChapter] = Field(default_factory=list)


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
    projectId: int | None = None  # 可选：关联写作项目
    content: str = ""


class NoteUpdate(BaseModel):
    """PUT /api/papers/notes/{id} 请求体。"""

    content: str = ""


class NoteResponse(BaseModel):
    """笔记响应。"""

    id: str
    paperId: str
    projectId: int | None = None
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


class BatchDeleteRequest(BaseModel):
    """POST /api/papers/batch-delete 请求体。"""

    paper_ids: list[str] = Field(
        ..., min_length=1, description="要删除的论文 ID 列表，最少 1 篇，最多 50 篇"
    )


class BatchDeleteResponse(BaseModel):
    """POST /api/papers/batch-delete 响应。"""

    success: bool = True
    deleted_count: int = 0
    failed_ids: list[str] = Field(default_factory=list)


class BatchTagRequest(BaseModel):
    """POST /api/papers/batch-tag 请求体 —— 批量给多篇论文增/删标签。

    add_tags 与 remove_tags 可同时提供（先加后删，一般不会同标签）。
    标签规范化：strip + 去空 + 去重，保留原大小写。
    """

    paper_ids: list[str] = Field(
        ..., min_length=1, max_length=200, description="要打标签的论文 ID 列表"
    )
    add_tags: list[str] = Field(default_factory=list, description="要新增的标签")
    remove_tags: list[str] = Field(default_factory=list, description="要移除的标签")


class BatchTagResponse(BaseModel):
    """POST /api/papers/batch-tag 响应。"""

    success: bool = True
    updated_count: int = 0
    failed_ids: list[str] = Field(default_factory=list)


class TagInfo(BaseModel):
    """GET /api/papers/tags 单条标签信息。"""

    name: str
    count: int = 0


class RenameTagRequest(BaseModel):
    """POST /api/papers/tags/rename 请求体 —— 全库重命名标签。"""

    old_name: str = Field(..., min_length=1, description="原标签名")
    new_name: str = Field(..., min_length=1, description="新标签名")


class TagMutationResponse(BaseModel):
    """标签重命名/删除的通用响应。"""

    success: bool = True
    affected_count: int = 0


class BatchDeleteReviewRequest(BaseModel):
    """POST /api/depth/reviews/batch-delete 请求体 —— 批量删除深度审稿记录。

    两种调用粒度（互斥，可以同时提供，会合并去重）：
    - review_ids: 直接指定 DepthReviewV4 主键（适合脚本/调试）
    - paper_ids: 指定论文 ID，服务端解析为「每篇论文最新一条」审稿记录（适合 UI 跨分页操作）

    合并 list 后最多 50 条（避免长事务）。
    """

    review_ids: list[str] | None = Field(
        None, min_length=1, max_length=50, description="直接 DepthReviewV4 主键列表（可选）"
    )
    paper_ids: list[str] | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="论文 ID 列表，服务端解析为各论文最新一条审稿记录（可选）",
    )

    @model_validator(mode="after")
    def _at_least_one(self):
        if not self.review_ids and not self.paper_ids:
            raise ValueError("review_ids 与 paper_ids 至少提供一个")
        return self


# ==================== 去重合并（WP-5.2） ====================


class DuplicateGroupPaper(BaseModel):
    """重复组内的单篇论文。"""

    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    abstract: str = ""
    journal: str = ""
    pdfUrl: str = ""
    source: str = ""
    citations: int = 0
    tags: list[str] = Field(default_factory=list)


class DuplicateGroup(BaseModel):
    """一组重复论文。"""

    papers: list[DuplicateGroupPaper]


class DuplicateGroupsResponse(BaseModel):
    """GET /api/papers/duplicates 响应。"""

    groups: list[DuplicateGroup]


class MergePapersRequest(BaseModel):
    """POST /api/papers/merge 请求体。"""

    target_id: str = Field(..., description="保留的目标论文 ID")
    source_ids: list[str] = Field(..., min_length=1, description="要合并并删除的论文 ID 列表")
    field_sources: dict[str, str] = Field(
        default_factory=dict,
        description="字段来源映射 {field_name: paper_id}，未指定则使用 target 的值",
    )


class MergePapersResponse(BaseModel):
    """POST /api/papers/merge 响应。"""

    success: bool = True
    target_id: str
    deleted_ids: list[str] = Field(default_factory=list)
    paper: Paper | None = None


# ==================== 跨文档对比表（WP-2.3） ====================


class ComparePapersRequest(BaseModel):
    """POST /api/papers/compare 请求体。"""

    paper_ids: list[str] = Field(
        ..., min_length=2, max_length=20, description="要对比的论文 ID 列表"
    )
    question: str | None = Field(None, description="可选的对比问题/主题，引导 LLM 抽取重点")


class ComparisonRow(BaseModel):
    """单篇论文在对比表中的结构化字段。"""

    paper_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    method: str = Field(default="", description="论文核心方法/模型/技术路线")
    sample_size: str = Field(default="", description="数据集/样本规模/实验设置")
    main_results: str = Field(default="", description="主要结果/性能指标/核心发现")
    metrics: str = Field(default="", description="评估指标")
    limitations: str = Field(default="", description="论文自述的局限性")
    conclusion: str = Field(default="", description="结论/贡献")


class ComparePapersResponse(BaseModel):
    """POST /api/papers/compare 响应。"""

    rows: list[ComparisonRow]
    generated_at: str = Field(default="", description="生成时间 ISO 字符串")
    model: str = Field(default="", description="使用的模型标签")


# ==================== 指定论文分析（新功能） ====================


class AnalyzeRequest(BaseModel):
    """POST /api/papers/analyze 请求体。"""

    paper_ids: list[str] = Field(..., min_length=2, description="必填，至少 2 篇论文")
    topic: str | None = Field(None, description="可选主题，用于计算主题匹配度")
    weights: RankWeights | None = Field(None, description="各维度权重，默认均等")


class AnalyzedPaperSchema(BaseModel):
    """分析结果中的单篇论文。"""

    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    citations: int = 0
    journal: str = ""
    similarity_score: float | None = Field(None, ge=0.0, le=1.0, description="主题相似度")
    total_score: float = Field(ge=0.0, le=1.0, description="综合得分")


class AnalysisSummarySchema(BaseModel):
    """分析汇总统计。"""

    top_paper: str = Field(..., description="综合得分最高的论文 ID")
    total_citations: int = 0
    avg_year: float = 0.0
    distribution_by_category: dict[str, int] | None = Field(None, description="学科分布")


class AnalyzeResponse(BaseModel):
    """POST /api/papers/analyze 响应。"""

    analyzed_papers: list[AnalyzedPaperSchema]
    summary: AnalysisSummarySchema


# ==================== 论文综合评分与批量对比 ====================


class RankWeights(BaseModel):
    """综合评分各维度权重（总和应为 1.0）。"""

    citations: float = Field(default=0.30, ge=0.0, le=1.0, description="引用数权重")
    recency: float = Field(default=0.25, ge=0.0, le=1.0, description="发表时间权重")
    similarity: float = Field(default=0.30, ge=0.0, le=1.0, description="主题相似度权重")
    journal: float = Field(default=0.15, ge=0.0, le=1.0, description="期刊等级权重")


class RankRequest(BaseModel):
    """POST /api/papers/rank 请求体。"""

    topic: str = Field(..., min_length=1, description="评分主题，用于计算语义相似度")
    weights: RankWeights = Field(default_factory=RankWeights, description="各维度权重")
    paperIds: list[str] | None = Field(default=None, description="限定论文范围，空表示全库")
    topK: int = Field(default=20, ge=1, le=200, description="返回前 K 篇")


class DimensionScores(BaseModel):
    """各维度得分明细。"""

    citations: float = Field(ge=0.0, le=1.0)
    recency: float = Field(ge=0.0, le=1.0)
    similarity: float = Field(ge=0.0, le=1.0)
    journal: float = Field(ge=0.0, le=1.0)


class RankedPaper(BaseModel):
    """带综合评分的论文条目。"""

    id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int = 0
    journal: str = ""
    citations: int = 0
    compositeScore: float = Field(ge=0.0, le=1.0, description="综合得分")
    dimensions: DimensionScores


class RankResponse(BaseModel):
    """POST /api/papers/rank 响应。"""

    papers: list[RankedPaper]
    total: int
    topic: str
    weights: RankWeights


class DimensionStats(BaseModel):
    """单维度统计信息。"""

    min: float
    max: float
    mean: float
    median: float
    std: float


class AnalysisReport(BaseModel):
    """POST /api/papers/analysis 响应 —— 综合分析报告。"""

    papers: list[RankedPaper]
    total: int
    topic: str
    weights: RankWeights
    scoreDistribution: list[dict]  # [{range: "0.8-1.0", count: N}, ...]
    dimensionStats: dict[str, DimensionStats]  # {citations: {...}, recency: {...}, ...}
    topJournals: list[dict]  # [{journal: "Nature", count: N, avgScore: 0.85}, ...]


# ==================== DEPTH 论文多维评分 ====================


class DepthBatchRequest(BaseModel):
    """POST /api/depth/batch 请求体。"""

    paper_ids: list[str] = Field(..., min_length=1, description="要评估的论文 ID 列表")


class DepthScoreRequest(BaseModel):
    """POST /api/depth/score 请求体 —— 单篇 DEPTH 评分（同步）。"""

    compute_mode: Literal["fast", "speed", "deep"] | None = None  # [P2-3 SCHEMAS_COMPUTE_MODE]

    paper_id: str = Field(..., min_length=1, description="论文 ID")


class DepthV4ReviewSelectedRequest(BaseModel):
    """POST /api/depth/v4/review-selected 请求体 —— 批量提交指定论文审稿。"""

    compute_mode: Literal["fast", "speed", "deep"] | None = None  # [P2-3 SCHEMAS_COMPUTE_MODE]

    paper_ids: list[str] = Field(
        ..., min_length=1, max_length=50, description="论文 ID 列表，1~50 篇"
    )


class ContinuationRequest(BaseModel):
    """POST /api/writing/chapters/{id}/continue 请求体 —— 智能续写（SSE 流式）。"""

    direction: str = Field(default="", description="续写方向提示")


class RewriteRequest(BaseModel):
    """POST /api/writing/chapters/{id}/rewrite 请求体 —— 全文改写。"""

    text: str = Field(..., min_length=1, description="选中的原始文本")

    @field_validator("text")
    @classmethod
    def _strip_and_validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text 不能为空")
        return v


class SaveContinuationRequest(BaseModel):
    """POST /api/writing/chapters/{id}/continuations 请求体 —— 保存续写历史。

    provenance 类型（kind）由服务端根据端点路径自动盖戳，不可由客户端指定，
    避免客户端伪造 AI 辅助来源。为兼容旧客户端，额外字段会被忽略。
    """

    model_config = ConfigDict(extra="ignore")

    content: str = Field(..., min_length=1, description="续写生成的内容")
    direction: str = Field(default="", description="续写方向（可空）")


class ValidateCitationsRequest(BaseModel):
    """POST /api/writing/chapters/{id}/validate-citations 请求体 —— 传入实时文本。"""

    content: str = Field(default="", description="待校验的章节文本（含 AI 刚插入、未落库内容）")

    @field_validator("content")
    @classmethod
    def _strip_and_validate_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("content 不能为空")
        return v


class AdoptStructureRequest(BaseModel):
    """POST /api/writing/chapters/{id}/adopt-structure 请求体 —— 采纳结构建议。"""

    target_chapter_id: int = Field(..., description="目标父章节 ID")


class DepthBatchResponse(BaseModel):
    """POST /api/depth/batch 响应。"""

    task_id: str
    total_papers: int


class DepthScoreResponse(BaseModel):
    """单篇论文 DEPTH 评分结果（v3 —— 八问 + 类型自适应 + 辩论式校准）。"""

    paper_id: str
    title: str
    type: str = "B"
    confidence: float = 0.5  # 0~1
    has_substance: bool = True  # Q0 是否有实质性贡献
    expectation: float = 0.5  # Q0 期待度 0~1
    obj_score: float = 0.0  # 0~1 客观指标
    novelty_score: float = 0.0  # 0~1
    rigor_score: float = 0.0  # 0~1
    influence_score: float = 0.0  # 0~1
    reproducibility_score: float = 0.0  # 0~1
    calibrated_score: float = 0.0  # 0~1 主席校准分
    final_score: float = 0.0  # 0~100 百分制
    verdict: str = "major_revision"  # accept/minor_revision/major_revision/reject
    core_contribution: str = ""
    keywords: list[str] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list)
    critique_points: list[str] = Field(default_factory=list)  # Q5a 质疑点
    defense_points: list[str] = Field(default_factory=list)  # Q5b 辩护点
    chair_reasoning: str = ""  # Q5c 主席裁决理由
    # v4.2 QF claim-validation 对称惩罚/奖励（默认 0，API 可见）
    claim_validation_penalty: float = 0.0
    claim_validation_bonus: float = 0.0
    content_source: str = "title"
    evaluated_at: str | None = None
    rank: int | None = None


# ==================== DEPTH 报告类评分（独立维度） ====================

DocumentType = Literal["paper", "report"]
"""内容类型:
- 'paper': 学术论文 → DEPTH v4.1 九节点 DAG 流水线 (existing)
- 'report': 感悟/读后/复现报告 → reflection 轻量 pipeline (new)
"""


class ReflectionScores(BaseModel):
    """反思报告的 4 维评分。所有分数 0~1。"""

    understanding_accuracy: float = Field(
        default=0.0, ge=0.0, le=1.0, description="是否准确理解了原论文的核心方法/结论"
    )
    analysis_depth: float = Field(default=0.0, ge=0.0, le=1.0, description="对原论文细节的分析深度")
    innovative_insights: float = Field(
        default=0.0, ge=0.0, le=1.0, description="是否有自己的创新思考或批判性意见"
    )
    evidence_support: float = Field(
        default=0.0, ge=0.0, le=1.0, description="观点是否有逻辑或引用支撑"
    )
    fidelity: float | None = Field(default=None, ge=0.0, le=1.0, description="报告→论文有据性")
    coverage: float | None = Field(default=None, ge=0.0, le=1.0, description="论文→报告覆盖度")
    average: float = Field(default=0.0, ge=0.0, le=1.0, description="4 维平均分（硬编码层添加）")


class ReflectionClaim(BaseModel):
    """报告中的核心观点/隐含论点。"""

    id: str = Field(..., description="观点 ID，如 C1, C2")
    text: str = Field(..., description="观点内容，不超过 80 字")
    evidence_id: str | None = Field(None, description="支撑该观点的 evidence ID")


class ReflectionEvidenceItem(BaseModel):
    """从报告原文提取的支撑性片段。"""

    id: str = Field(..., description="证据 ID，如 E1, E2")
    snippet: str = Field(..., description="原文片段，不超过 100 字")
    claim_ref: str | None = Field(None, description="该片段支撑的观点 ID")


class ReflectionResult(BaseModel):
    """感悟报告评审结果 JSON：存入 DepthReviewV4.reflection_result。"""

    claims: list[ReflectionClaim] = Field(default_factory=list)
    evidence_pool: list[ReflectionEvidenceItem] = Field(default_factory=list)
    scores: ReflectionScores = Field(
        default_factory=ReflectionScores,
        description="4 维评分（默认全 0，兼容老数据/未评分记录）",
    )
    summary: str = Field(default="", description="报告核心内容总结")
    verdict: str = Field(
        default="needs_evidence",
        description="LLM 输出与硬编码阈值共同决定的最终判决",
    )
    verdict_reason: str = Field(default="", description="判决依据说明（包含硬编码触发原因）")

    @model_validator(mode="before")
    @classmethod
    def _legacy_data_compat(cls, data):
        """兼容老数据：v4.1 论文行的 reflection_result 字段为 None 或缺字段。

        旧 v4.1 记录（kind='paper' 或 kind=NULL）通常 reflection_result=NULL。
        若上层 schema 用 model_validate 把这条记录反序列化为 ReflectionResult
        （如统一审计页 / 前端 SSR），不应让前端收到 KeyError / ValidationError 报白屏。

        行为：
        - data 为 None → 返回一个最小可用结构（让 .scores.average 不再抛 AttributeError）
        - data 是 dict（字段可能为 None / 缺 / 非类型） → 都填充为安全默认值
        - data 已是完整结构 → 原样返回

        注意：不要原地修改调用方传入的 dict（Pydantic v2 best practice），
        统一用 `dict(data)` 生成副本。
        """
        if data is None:
            return {
                "claims": [],
                "evidence_pool": [],
                "scores": ReflectionScores().model_dump(),
                "summary": "",
                "verdict": "n/a",
                "verdict_reason": "无 reflection_result 字段（老 v4.1 数据兼容）",
            }
        if isinstance(data, dict):
            data = dict(data)  # 不修改入参；防上游复用同一个 dict
            # 防御 None / 缺字段二种场景：setdefault 只处理「缺」，还要单独
            # 兑底 None 这种情况。存在 = None 的字段不归 setdefault 管。
            for key, default in (
                ("claims", []),
                ("evidence_pool", []),
                ("summary", ""),
                ("verdict", "n/a"),
                ("verdict_reason", ""),
                ("scores", {}),
            ):
                if data.get(key) is None:
                    data[key] = default
            # scores 非 dict（None / 列表 / 字符串 / 乱数据） → 兑底为空结构，
            # 让 Pydantic 的 default_factory=ReflectionScores 填充全零。
            # 完整子字段已由 ReflectionScores 自身的 default=0.0 兑底。
            if not isinstance(data.get("scores"), dict):
                data["scores"] = {}
        return data


class ReflectionListResponse(BaseModel):
    """GET /api/depth/reflection/list 响应。"""

    items: list[dict] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class ReflectionUploadRequest(BaseModel):
    """POST /api/depth/reflection/text 请求体（不走 PDF 解析，面向感悟/读后/复现报告）。"""

    title: str = Field(..., min_length=1, description="报告标题")
    content: str = Field(..., min_length=10, description="报告全文")
    authors: list[str] = Field(default_factory=list, description="报告作者列表")
    year: int | None = Field(None, ge=1900, le=2100)
    sourcePaperId: str | None = Field(
        None,
        description="若是对某篇论文的读后感/复现，可指定被读的 paper_id；纯独立报告留空",
    )


class SourcePaperInfo(BaseModel):
    """感悟报告上传后，系统自动识别/导入原论文的结果摘要。"""

    status: Literal[
        "exists", "imported", "rate_limited", "no_match", "import_failed", "no_title", "manual"
    ]
    paperId: str | None = None
    title: str | None = None
    source: Literal["arxiv", "semantic_scholar"] | None = None


class ReflectionCreateResponse(BaseModel):
    """POST /api/depth/reflection/text 响应。"""

    paperId: str
    taskId: str | None = None
    documentType: DocumentType = "report"
    message: str = ""
    sourcePaper: SourcePaperInfo | None = None


class ReflectionFileBatchItem(BaseModel):
    """批量文件上传中的单文件处理结果。

    字段语义：
    - status: 'accepted' 成功提交 reflection 任务 / 'skipped' 跳过（去重命中） / 'failed' 失败
    - paperId: 仅 accepted/skipped 时有值；failed 时为 None
    - taskId: 仅 accepted 时有值；其他情况为 None
    - error: failed 时为错误信息（HTTPException detail 或异常 message）
    - bytes: 文件大小（字节），方便前端展示
    """

    filename: str
    paperId: str | None = None
    taskId: str | None = None
    status: Literal["accepted", "skipped", "failed"] = "accepted"
    error: str = ""
    bytes: int = 0
    sourcePaper: SourcePaperInfo | None = None


class ReflectionFileBatchResponse(BaseModel):
    """POST /api/depth/reflection/files 响应（多文件批量上传）。

    与 POST /api/depth/reflection/file 单文件版的区别：
    - 单文件版成功时直接返回 paperId + taskId
    - 批量版每个文件独立处理，单文件失败不影响其他文件，逐项返回结果

    客户端可对每个文件独立订阅 SSE：
      /api/tasks/{item.taskId}/stream
    """

    items: list[ReflectionFileBatchItem]
    submitted: int = 0
    failed: int = 0
    total: int = 0
    message: str = ""


class UnifiedReviewListResponse(BaseModel):
    """GET /api/depth/unified/list 响应（按 kind 过滤）。"""

    items: list[dict] = Field(default_factory=list)
    total: int = 0
    kind: str = ""
    limit: int = 20
    offset: int = 0


# ==================== 算力模式（Compute Mode） ====================


class ComputeModeInfo(BaseModel):
    """单个算力模式的元数据。"""

    id: str  # speed / deep
    name: str
    description: str
    temperature: float
    max_tokens: int
    parallel: bool
    llm_config_id: str | None = None


class ComputeModeResponse(BaseModel):
    """GET /api/compute/modes 响应。"""

    current: str
    modes: list[ComputeModeInfo]


class SwitchComputeModeRequest(BaseModel):
    """POST /api/compute/mode 请求体。"""

    mode: str  # speed / deep


class SwitchComputeModeResponse(BaseModel):
    """POST /api/compute/mode 响应。"""

    success: bool
    current: str
    name: str
    message: str = ""


# ==================== PDF 翻译历史（WP-2.7 增强） ====================


class TranslationHistoryItem(BaseModel):
    """PDF translation history item."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    paperId: str = Field(..., alias="paper_id")
    originalText: str = Field(..., alias="original_text")
    translatedText: str = Field(..., alias="translated_text")
    targetLanguage: str = Field(..., alias="target_language")
    createdAt: str = Field(..., alias="created_at")


class TranslationHistoryList(BaseModel):
    """GET /api/papers/{id}/translation-history 响应。"""

    items: list[TranslationHistoryItem]


class TranslationHistoryCreate(BaseModel):
    """POST /api/papers/{id}/translation-history 请求体。"""

    original_text: str = Field(..., min_length=1, description="原文")
    translated_text: str = Field(..., min_length=1, description="译文")
    target_language: str = Field(default="zh-CN", description="目标语言")


class TranslationHistoryCreateResponse(BaseModel):
    """POST /api/papers/{id}/translation-history 响应。"""

    id: str
    success: bool = True


# ==================== 通用异步任务系统 ====================


class TaskCreate(BaseModel):
    """POST /api/tasks 请求体 —— 提交新任务。"""

    type: str = Field(
        ...,
        min_length=1,
        description="任务类型: depth_batch / depth_review / export / import / batch_delete",
    )
    params: dict = Field(default_factory=dict, description="任务参数")


class TaskResponse(BaseModel):
    """GET /api/tasks/{id} 响应 —— 单个任务状态。"""

    id: str
    type: str
    status: str
    progress: int = 0
    progressMessage: str = ""
    params: dict | None = None
    result: dict | None = None
    error: str | None = None
    createdAt: str = ""
    updatedAt: str = ""
    completedAt: str | None = None


class TaskListResponse(BaseModel):
    """GET /api/tasks 响应 —— 任务列表。"""

    items: list[TaskResponse]
    total: int


class TaskProgressEvent(BaseModel):
    """SSE 进度事件格式。"""

    taskId: str
    status: str
    progress: int = 0
    progressMessage: str = ""
    result: dict | None = None
    error: str | None = None


# ==================== 本地文件夹导出 ====================


class FolderExportRequest(BaseModel):
    """POST /api/writing/projects/{id}/export-folder 请求体。"""

    outputDir: str = Field(..., min_length=1, description="用户指定的输出目录绝对路径")
    includeVscode: bool = Field(default=True, description="是否生成 .vscode 配置文件")
    includeObsidian: bool = Field(default=False, description="是否生成 Obsidian 配置文件")

    @field_validator("outputDir")
    @classmethod
    def _strip_and_validate_output_dir(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("outputDir 不能为空")
        return v


class FolderExportResponse(BaseModel):
    """POST /api/writing/projects/{id}/export-folder 响应。"""

    success: bool
    outputDir: str
    files: list[str] = Field(default_factory=list, description="生成的文件列表（相对路径）")


# ==================== 多 API Key 管理（Layer 1） ====================


# 合法 scope 集合：read=GET，write=POST/PUT/DELETE，admin=/api/admin/* + /api/system/*
ApiKeyScope = Literal["read", "write", "admin"]


class ApiKeyCreate(BaseModel):
    """POST /api/system/api-keys 请求体 —— 创建 API Key。"""

    name: str = Field(..., min_length=1, max_length=128, description="调用方名称")
    description: str = Field(default="", max_length=512, description="用途说明")
    scopes: list[ApiKeyScope] = Field(default_factory=lambda: ["read"], description="权限范围")
    rateLimitPerMin: int = Field(
        default=0, ge=0, le=10000, description="每分钟请求上限（0=用系统默认）"
    )

    @field_validator("scopes")
    @classmethod
    def _validate_scopes(cls, v: list[str]) -> list[str]:
        if not v:
            return ["read"]
        # 去重 + 校验合法值
        valid = {"read", "write", "admin"}
        normalized = []
        for s in v:
            if s not in valid:
                raise ValueError(f"非法 scope: {s}，合法值: {sorted(valid)}")
            if s not in normalized:
                normalized.append(s)
        return normalized


class ApiKeyCreateResponse(BaseModel):
    """创建 API Key 的响应 —— 明文 key 仅此一次返回。"""

    keyId: str
    key: str = Field(..., description="完整 API Key 明文，仅此一次返回，请妥善保存")
    name: str
    scopes: list[str]
    rateLimitPerMin: int
    createdAt: str


class ApiKeyListItem(BaseModel):
    """API Key 列表项（不含完整 key 明文）。"""

    id: int
    keyId: str
    keyPrefix: str
    name: str
    description: str
    scopes: list[str]
    rateLimitPerMin: int
    enabled: bool
    createdAt: str
    lastUsedAt: str | None = None


class ApiKeyUpdate(BaseModel):
    """PUT /api/system/api-keys/{key_id} 请求体 —— 更新 Key 配置。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    scopes: list[ApiKeyScope] | None = None
    rateLimitPerMin: int | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None

    @field_validator("scopes")
    @classmethod
    def _validate_scopes(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if not v:
            return ["read"]
        valid = {"read", "write", "admin"}
        normalized = []
        for s in v:
            if s not in valid:
                raise ValueError(f"非法 scope: {s}，合法值: {sorted(valid)}")
            if s not in normalized:
                normalized.append(s)
        return normalized


class ApiKeyUsageItem(BaseModel):
    """单 Key 用量统计项。"""

    keyId: str
    name: str
    totalCalls: int
    successCalls: int
    failedCalls: int
    avgDurationMs: float
    lastCalledAt: str | None = None


class ApiKeyUsageResponse(BaseModel):
    """用量统计响应。"""

    items: list[ApiKeyUsageItem]
    period: str
