"""PaperForge mock 后端 SQLAlchemy ORM 模型。

注意：本文件原为 Pydantic schema，现改为 ORM 模型；
Pydantic 响应模型已迁移到 schemas.py。
"""

from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _gen_uuid() -> str:
    return str(_uuid.uuid4())


class Paper(Base):
    """论文表。

    authors / tags 使用 JSON 列存储列表。
    为保留「按引用数/文本块数排序」与前端字段，额外包含 citations /
    chunk_count / index_size / source 列；journal 为本次新增列。
    """

    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False, index=True)
    authors: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String, default="all", nullable=False, index=True)
    tags: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    year: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    journal: Mapped[str] = mapped_column(String, default="", nullable=False)
    pdf_url: Mapped[str] = mapped_column(String, default="", nullable=False)
    citations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    index_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source: Mapped[str] = mapped_column(String, default="arxiv", nullable=False, index=True)
    # B1: PDF 全文内容（可空，提取失败或未回填时为 NULL）
    full_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # WP-1.2: OCR 状态机（pending/done/failed）与扫描件标识
    ocr_status: Mapped[str | None] = mapped_column(String, nullable=True)
    is_scanned: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
    # B2: Semantic Scholar 富化字段（可空，未富化时为 NULL）
    influential_citations: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 有影响力引用数
    fields_of_study: Mapped[Any | None] = mapped_column(
        JSON, nullable=True
    )  # 研究领域标签，如 ["Computer Science"]
    # WP-5.1: DOI（从 PDF 全文或元数据提取）
    doi: Mapped[str | None] = mapped_column(String, nullable=True)
    # WP-2.2: 引用情感/关系图 MVP（基于标题+摘要的关键词情感，-1~1）
    # ⚠️ 已弃用：这些字段代表「论文自述情感」，学术价值有限。
    #    保留以兼容旧数据；新实现使用 citation_sentiments 表存储「被引情感」。
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1 负面 ~ 1 正面
    sentiment_label: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # positive / neutral / negative
    sentiment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0~1
    # Reflection 自动识别：报告所引用的原论文 paper_id（自引用 papers.id）
    source_paper_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("papers.id"), nullable=True, index=True
    )
    # Reflection 自动识别状态：记录 resolver 中间结果，供补识别任务筛选
    source_paper_status: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # 报告原始 docx 存储路径及 reflection 解析结果/得分/fidelity/五维度合成
    reflection_docx_path: Mapped[str | None] = mapped_column(String, nullable=True)

    favorites = relationship(
        "Favorite",
        back_populates="paper",
        cascade="all, delete-orphan",
    )

    # DEPTH v4.2 审稿结果（一对多，级联删除）
    depth_reviews_v4 = relationship(
        "DepthReviewV4",
        back_populates="paper",
        cascade="all, delete-orphan",
    )

    # WP-2.2: 被引情感（其他论文对本文的引用情感，一对多，级联删除）
    citation_sentiments = relationship(
        "CitationSentiment",
        back_populates="target_paper",
        cascade="all, delete-orphan",
        foreign_keys="CitationSentiment.target_paper_id",
    )
    # WP-2.2: 本文对其他论文的引用情感（发出方）
    outgoing_citation_sentiments = relationship(
        "CitationSentiment",
        back_populates="source_paper",
        cascade="all, delete-orphan",
        foreign_keys="CitationSentiment.source_paper_id",
    )


class Favorite(Base):
    """收藏表（为后续扩展预留，例如记录收藏时间、用户等）。"""

    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    paper = relationship("Paper", back_populates="favorites")


class CitationSentiment(Base):
    """被引情感表：记录其他论文对目标论文的引用情感。

    - source_paper_id: 引用方（其他论文）
    - target_paper_id: 被引用方（目标论文）
    - sentiment_label: support（支持） / criticize（批评） / background（背景引用）
    - confidence: LLM 置信度 0~1
    - context_snippet: 引用上下文原文片段
    """

    __tablename__ = "citation_sentiments"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_gen_uuid)
    source_paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sentiment_label: Mapped[str] = mapped_column(String, default="background", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    context_snippet: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
        nullable=False,
    )

    source_paper = relationship(
        "Paper",
        back_populates="outgoing_citation_sentiments",
        foreign_keys=[source_paper_id],
    )
    target_paper = relationship(
        "Paper",
        back_populates="citation_sentiments",
        foreign_keys=[target_paper_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "source_paper_id", "target_paper_id", name="uq_citation_sentiment_source_target"
        ),
    )


class LLMConfig(Base):
    """大模型配置表 —— 存储用户自定义添加的 LLM 连接信息。

    所有 AI 能力（问答 / 综述生成 / 语义搜索）均通过 LLMFactory 从此表
    加载配置，实现运行时动态切换。Provider 类型由 api_url 关键词自动识别，
    不匹配时默认使用 OpenAI 兼容协议。
    """

    __tablename__ = "llm_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)  # 显示名称，如 "我的 GLM-4"
    api_url: Mapped[str] = mapped_column(
        String, nullable=False
    )  # API 地址，如 https://open.bigmodel.cn/api/paas/v4
    api_key: Mapped[str] = mapped_column(
        String, default="", nullable=False
    )  # API Key（可选，本地模型可留空）
    model_id: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 模型 ID，如 glm-4、gpt-4o、deepseek-chat
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # 启用/禁用
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    updated_at = mapped_column(
        DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now(), nullable=False
    )


class WritingProject(Base):
    """论文写作项目表。

    一个项目对应一篇正在撰写的论文，包含标题、关键词、目标期刊等元数据。
    项目下挂载一棵章节大纲树（Chapter 通过 project_id + parent_id 递归关联）。
    """

    __tablename__ = "writing_projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    keywords: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)  # 关键词列表
    target_journal: Mapped[str] = mapped_column(String, default="", nullable=False)  # 目标期刊
    target_word_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )  # 目标字数（0=未设定）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    updated_at = mapped_column(
        DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now(), nullable=False
    )

    chapters = relationship(
        "Chapter",
        back_populates="project",
        cascade="all, delete-orphan",
        foreign_keys="Chapter.project_id",
    )


class Chapter(Base):
    """写作章节表 —— 支持无限层级嵌套（parent_id 自引用递归）。

    每个章节归属一个 WritingProject，通过 parent_id 构成大纲树。
    content 字段以 Markdown 存储，order 决定同级章节的排列顺序。
    """

    __tablename__ = "writing_chapters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id = mapped_column(
        Integer,
        ForeignKey("writing_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id = mapped_column(
        Integer,
        ForeignKey("writing_chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )  # NULL 表示根章节
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)  # Markdown 内容
    order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 同级排序
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    updated_at = mapped_column(
        DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now(), nullable=False
    )

    project = relationship("WritingProject", back_populates="chapters", foreign_keys=[project_id])
    children = relationship(
        "Chapter",
        back_populates="parent",
        cascade="all, delete-orphan",
        foreign_keys="Chapter.parent_id",
    )
    parent = relationship(
        "Chapter",
        remote_side=[id],
        back_populates="children",
        foreign_keys=[parent_id],
    )
    # C2: 续写历史（一对多，章节删除时级联）
    continuations = relationship(
        "ChapterContinuation",
        back_populates="chapter",
        cascade="all, delete-orphan",
        order_by="ChapterContinuation.created_at.desc()",
    )


class ChapterContinuation(Base):
    """章节续写历史表 —— 记录每次智能续写的生成内容，供用户回退/恢复。

    每个章节最多保留 10 条（超出时删除最早的），按 created_at 倒序展示。
    direction 为用户输入的续写方向，可为空。
    """

    __tablename__ = "writing_chapter_continuations"

    id = mapped_column(String(36), primary_key=True)  # UUID
    chapter_id = mapped_column(
        Integer,
        ForeignKey("writing_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 该轮续写生成的内容
    direction: Mapped[str] = mapped_column(String, default="", nullable=False)  # 续写方向，可空
    # 学术诚信 provenance：记录该条历史由 AI 生成的类型。
    # 'continue' = 智能续写文本；'rewrite' = AI 改写选中文本。
    # 续写/改写落库即 provenance，导出时据此汇总「AI 使用声明」。
    kind: Mapped[str] = mapped_column(String, default="continue", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )

    chapter = relationship("Chapter", back_populates="continuations")


class PaperNote(Base):
    """论文笔记表 —— 用户为每篇论文添加的个人笔记（可与写作项目关联）。

    project_id 为可选外键：为 NULL 表示普通笔记，非 NULL 表示与某写作项目关联。
    """

    __tablename__ = "paper_notes"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = mapped_column(
        Integer,
        ForeignKey("writing_projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)  # Markdown 内容
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    updated_at = mapped_column(
        DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now(), nullable=False
    )


class UserTemplate(Base):
    """用户自定义写作模板 —— 由当前项目大纲保存而来，可在新建项目时选择。"""

    __tablename__ = "user_templates"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, default="", nullable=False)
    chapters: Mapped[Any] = mapped_column(
        JSON, default=list, nullable=False
    )  # 大纲结构（格式同系统模板）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )


class WritingSnapshot(Base):
    """写作字数每日快照 —— 记录每个项目每日的字数，用于趋势图。

    每日首次打开项目时自动记录一条（project_id + date 唯一）。
    """

    __tablename__ = "writing_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id = mapped_column(
        Integer,
        ForeignKey("writing_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[str] = mapped_column(String, nullable=False)  # 'YYYY-MM-DD'
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )


class ChapterVersion(Base):
    """章节历史版本 —— 每次保存章节内容时自动记录一个快照（最多保留 10 个）。"""

    __tablename__ = "chapter_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    chapter_id = mapped_column(
        Integer,
        ForeignKey("writing_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 该版本的章节内容
    word_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )


class HotspotConfig(Base):
    """学科热点词配置表 —— 支持按 scope 动态切换 Q2 热点词表。

    - scope: 学科/领域标识，如 'default'、'cs'、'ai'、'biomed'。
    - keywords: 热点词列表，JSON 数组。
    - is_default: 是否作为全局默认配置，当按 scope 查不到时回退到默认。
    """

    __tablename__ = "hotspot_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String, default="default", nullable=False, index=True)
    keywords: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at = mapped_column(
        DateTime,
        default=lambda: datetime.now(),
        nullable=False,
    )
    updated_at = mapped_column(
        DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
        nullable=False,
    )


class DepthFulltextCache(Base):
    """DEPTH 全文覆盖层缓存表（ADR-014 P9，漏洞 C 软件解法）。

    按 (paper_id, text_hash) 缓存分块摘要的产物，避免同篇论文重评时
    重复付出 N 次分块摘要 LLM 调用（N≈全文长度/1500）。
    访问一律 best-effort（表不存在/DB 异常时跳过缓存，不影响评审）。
    """

    __tablename__ = "depth_fulltext_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    text_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    global_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    chunk_summaries: Mapped[str] = mapped_column(
        Text, default="[]", nullable=False
    )  # JSON 字符串列表
    verbatim_chunks: Mapped[str] = mapped_column(
        Text, default="[]", nullable=False
    )  # JSON 列表 [{index,text,signal}]
    n_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at = mapped_column(
        DateTime,
        default=lambda: datetime.now(),
        nullable=False,
    )

    __table_args__ = (Index("ix_depth_fulltext_paper_hash", "paper_id", "text_hash", unique=True),)


class PaperEmbedding(Base):
    """论文向量嵌入表 —— 存储「标题+摘要」的向量表示（384 维，BAAI/bge-small-en-v1.5）。

    混合检索（FTS5 + 向量 + RRF）时，向量检索计算用户查询向量与所有论文向量的
    余弦相似度，返回 Top N。若本表为空或 fastembed 未安装/未联网，降级为纯 FTS5。
    embedding 以 JSON 数组字符串存储（避免引入 numpy 二进制列依赖）。
    """

    __tablename__ = "paper_embeddings"

    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 序列化的 384 维向量
    dim: Mapped[int] = mapped_column(Integer, default=384, nullable=False)  # 维度（便于校验）
    updated_at = mapped_column(
        DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
        nullable=False,
    )


class PaperFigure(Base):
    """论文内嵌实验图抽取结果表 —— figure 级向量检索（384 维，bge-small-en-v1.5）。

    与论文级 PaperEmbedding 不同，本表每张图独立一行，使「搜 accuracy curve」
    能定位到具体论文的具体那张图（figure_path + page）。OCR 文字进 RAG，
    embedding 存 JSON 字符串（与 PaperEmbedding 同维度，便于复用检索逻辑）。
    """

    __tablename__ = "paper_figures"

    id = mapped_column(Integer, primary_key=True, autoincrement=True)
    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page = mapped_column(Integer, nullable=False, default=0)
    figure_index = mapped_column(Integer, nullable=False, default=0)
    figure_path = mapped_column(Text, nullable=False)  # 磁盘相对路径，可访问原图
    ocr_text = mapped_column(Text, nullable=False, default="")  # 图中文字（坐标轴/图例/标注）
    embedding: Mapped[str] = mapped_column(Text, nullable=True)  # JSON 序列化 384 维；OCR 空时为空
    # Qwen / LLM 对图表的解读摘要
    qwen_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # M0/Rec4: 图注文本（作者亲笔 caption，零幻觉语义源）
    caption_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Rec2: 图号，用于真实图去重、计算 KPI（抵消 detector 过度分割）
    figure_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # M0: 图源标记（bitmap / vector），用于验证矢量渲染兜底
    source: Mapped[str] = mapped_column(String, default="bitmap", nullable=False)
    # M1: 图注硬匹配置信度与标签（hard/gray/unmatched）
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_label: Mapped[str | None] = mapped_column(String, nullable=True)
    # M2: VLM 路由决策（vlm / rule_only / skip）
    vlm_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    # P0: 正文中引用/描述该图的段落原文，用于 QF 对齐
    source_text_span: Mapped[str | None] = mapped_column(Text, nullable=True)
    # P0: 结构化轴信息（JSON: x_label, y_label, x_ticks, y_ticks, legend_items, caption_summary）
    axis_info: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    # P1: 数值断言与 axis_info 的校验结果（JSON: {claims, validated}）
    claim_validation: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    # P3: 曲线点接口预留（JSON，尚未实现 CV 提取）
    curve_points: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    created_at = mapped_column(DateTime, default=lambda: datetime.now(), nullable=False)

    __table_args__ = (
        Index(
            "ix_paper_figures_paper_id_figure_number",
            "paper_id",
            "figure_number",
        ),
    )


class DepthScore(Base):
    """DEPTH 论文多维评分结果表（v3 —— 八问 + 类型自适应 + 辩论式校准）。

    维度：novelty(β) / rigor(γ) / influence(δ) / reproducibility(ε)。
    所有分项存储为 0~1 浮点；final_score 为 0~100 百分制。
    """

    __tablename__ = "depth_scores"

    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    title: Mapped[str] = mapped_column(String, default="", nullable=False)
    type: Mapped[str] = mapped_column(
        String, default="B", nullable=False
    )  # A理论突破 B方法改进 C应用迁移 D综述
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)  # 0~1
    has_substance: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )  # Q0 是否有实质性贡献
    expectation: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)  # Q0 期待度 0~1
    obj_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0~1 客观指标
    novelty_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0~1 创新
    rigor_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0~1 严谨
    influence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0~1 影响力
    reproducibility_score: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )  # 0~1 可复现性
    calibrated_score: Mapped[float] = mapped_column(
        Float, default=0.0, nullable=False
    )  # 0~1 主席校准分
    final_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # 0~100 百分制
    verdict = mapped_column(
        String, default="major_revision", nullable=False
    )  # accept/minor_revision/major_revision/reject
    core_contribution: Mapped[str] = mapped_column(String, default="", nullable=False)
    keywords: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    missing_items: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    critique_points: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)  # Q5a 质疑点
    defense_points: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)  # Q5b 辩护点
    chair_reasoning: Mapped[str] = mapped_column(
        String, default="", nullable=False
    )  # Q5c 主席裁决理由
    content_source: Mapped[str] = mapped_column(String, default="title", nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )


class DepthReviewV4(Base):
    """DEPTH v4.2 / reflection 审稿结果表。

    通过 `kind` 列区分两种内容类型（避免引入第二张表的字段污染）：

    - kind='paper'  —— 学术论文，走 v4.2 九节点 DAG 流水线
      使用 q0~q5c_result、evidence_pool、final_verdict 等已有 JSON 列。
    - kind='report' —— 感悟/读后/复现报告，走 reflection 轻量 pipeline
      使用 reflection_result JSON 列（4 维评分 + 内嵌证据池 + summary + verdict）；
      已有 q*~evidence_pool 列在 report 类型下保持 NULL，确保不互相污染。

    每次审稿生成一条记录，同一篇论文可多次审稿（保留历史）。
    """

    __tablename__ = "depth_reviews_v4"

    id = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ── 内容类型：'paper'（v4.2 DAG）|'report'（reflection 轻量 pipeline） ──
    # server_default + default 双绑：旧 v4.2 记录在 ALTER TABLE ADD COLUMN 时被
    # SQLite 填充为 'paper'，新记录经 ORM 写入也为 'paper'。
    # index=True 便于 /api/depth/unified/list?kind=... 直接走索引
    kind: Mapped[str] = mapped_column(
        String, default="paper", server_default="paper", nullable=False, index=True
    )
    # ── 论文类节点结果（kind='paper' 时使用，kind='report' 时为 NULL） ──
    q0_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    q1_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    evidence_pool: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    q2_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    q3_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    q4_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    q5a_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    q5b_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    q5c_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    final_verdict: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    # ── 报告类专属结果（kind='report' 时使用，kind='paper' 时为 NULL） ──
    # JSON 结构：
    #   {
    #     "claims": [{"id": "C1", "text": "...", "evidence_id": "E1"}],
    #     "evidence_pool": [{"id": "E1", "snippet": "...原文片段...", "claim_ref": "C1"}],
    #     "scores": {
    #       "understanding_accuracy": 0.82,
    #       "analysis_depth": 0.78,
    #       "innovative_insights": 0.70,
    #       "evidence_support": 0.85
    #     },
    #     "summary": "...核心内容总结...",
    #     "verdict": "well_done|needs_evidence|needs_depth|rewrite_required",
    #     "verdict_reason": "...硬编码裁决原因..."
    #   }
    reflection_result: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    # ── 通用字段 ──
    # 版本标记（v4.0 / v4.2，默认 v4.2，用于 v4 列表过滤）
    version: Mapped[str] = mapped_column(String, default="v4.2", nullable=False)
    # 任务状态
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False
    )  # pending/running/completed/failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 算力模式（fast/speed/deep），记录本次审稿使用的计算模式
    compute_mode: Mapped[str | None] = mapped_column(String, default="deep", nullable=True)

    paper = relationship("Paper", back_populates="depth_reviews_v4")


class PdfAnnotation(Base):
    """PDF 批注/高亮表 —— 存储用户在 PDF 预览中创建的文本高亮与批注。

    B4：支持在 PDF 预览中选中文本并高亮，添加批注。
    - 高亮数据主要存储在 localStorage（离线优先），此表为可选同步目标。
    - quadpoints 存储 PDF 选区的四边形坐标（JSON 数组），用于精确还原高亮位置。
    - color 为高亮颜色（如 #FFEB3B 黄色）。
    - note 为可选的批注文字。
    """

    __tablename__ = "pdf_annotations"

    id = mapped_column(String(36), primary_key=True)  # UUID
    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)  # 页码（从 1 开始）
    quadpoints: Mapped[Any] = mapped_column(JSON, nullable=False)  # 四边形坐标数组
    color: Mapped[str] = mapped_column(String, default="#FFEB3B", nullable=False)  # 高亮颜色
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)  # 批注文字（可选）
    # WP-5.1: 批注来源（auto=自动从 PDF 提取；manual=用户手动创建）
    source: Mapped[str] = mapped_column(String, default="manual", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )


class TranslationHistory(Base):
    """PDF 翻译历史表 —— 按论文保存最近 N 条翻译记录。

    WP-2.7 增强：为 PDF 实时翻译增加历史记录，避免刷新后丢失。
    - paper_id: 论文 ID
    - original_text: 原文
    - translated_text: 译文
    - target_language: 目标语言
    - created_at: 创建时间
    """

    __tablename__ = "translation_history"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_language: Mapped[str] = mapped_column(String, default="zh-CN", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )


class Task(Base):
    """通用异步任务表 —— 所有耗时后台任务（DEPTH审稿、导出、导入等）统一管理。

    设计要点：
    - 基于 SQLite 持久化，任务在服务重启后仍可查询历史
    - status: pending → running → completed / failed
    - progress: 0-100 整数百分比
    - result: JSON 列，存储任务完成后的结果数据
    - SSE 实时推送通过 asyncio.Queue 实现（内存桥），不依赖 DB 轮询
    - 定期清理：保留最近 500 条已完成/失败任务，超出按 created_at FIFO 淘汰
    """

    __tablename__ = "tasks"

    id = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    type = mapped_column(
        String, nullable=False, index=True
    )  # depth_batch | depth_review | export | import | batch_delete | ...
    status = mapped_column(
        String, default="pending", nullable=False
    )  # pending | running | completed | failed
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0-100
    progress_message = mapped_column(
        String, default="", nullable=False
    )  # 可读进度描述，如 "正在审稿 2/5..."
    params: Mapped[Any | None] = mapped_column(JSON, nullable=True)  # 任务参数快照（便于查询历史）
    result: Mapped[Any | None] = mapped_column(JSON, nullable=True)  # 任务结果（完成时填充）
    error: Mapped[str | None] = mapped_column(String, nullable=True)  # 错误信息（失败时填充）
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    updated_at = mapped_column(
        DateTime, default=lambda: datetime.now(), onupdate=lambda: datetime.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ── 多 API Key 鉴权（Layer 1）+ 调用审计（Layer 2）────────────────
class ApiKey(Base):
    """API Key 表（多调用方鉴权）。

    - key_id: 短 ID（ak_ 开头），用于管理端点引用，不暴露完整 key
    - key_hash: sha256(full_key) hex digest；明文仅创建时返回一次
    - key_prefix: full_key 前 12 位，用于管理界面显示（如 pf_live_ab12）
    - scopes: JSON 列，存储权限范围（如 ["read"] / ["read","write"] / ["admin"]）
    - rate_limit_per_min: per-key 限流上限（0=用系统默认）
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    scopes: Mapped[Any] = mapped_column(JSON, default=lambda: ["read"], nullable=False)
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)


class ExperimentAudit(Base):
    """论文实验审计记录表（CS Paper Experiment Auditor P0）。

    每次审计生成一条记录，同一篇论文可多次审计（保留历史，同 DepthReviewV4）。
    - findings: JSON 数组，每条 Finding 严格遵循
      experiment_audit/schemas.py 的 Finding schema（finding_id/type/severity/
      title/page/bbox/claim/computed/tolerance/method/evidence_sources/
      normal_explanation/needs_human_review）。
    - checks_run: JSON 数组，各检测项运行摘要（check 名 / status / 耗时 /
      跳过原因），单项检测失败不拖垮整体（fail-open）。
    - source_pdf_hash: 审计时刻 PDF 的 SHA-256，用于证据固定。
    """

    __tablename__ = "experiment_audits"

    id = mapped_column(String(36), primary_key=True, default=_gen_uuid)
    paper_id = mapped_column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_pdf_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    findings: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    checks_run: Mapped[Any] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False
    )  # pending/running/completed/failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ApiCallLog(Base):
    """API 调用审计日志（Layer 2：调用计量与异常分析）。

    - 异步线程写入，不阻塞请求
    - key_id: ApiKey.key_id / "global"（全局 token）/ "loopback"（本机免鉴权）
    - status_code: HTTP 状态码（< 400 视为成功）
    - duration_ms: 端到端耗时（含中间件）
    """

    __tablename__ = "api_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(32), default="global", nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    client_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(), nullable=False, index=True
    )
