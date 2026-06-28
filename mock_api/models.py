"""PaperForge mock 后端 SQLAlchemy ORM 模型。

注意：本文件原为 Pydantic schema，现改为 ORM 模型；
Pydantic 响应模型已迁移到 schemas.py。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import relationship

from .database import Base


class Paper(Base):
    """论文表。

    authors / tags 使用 JSON 列存储列表。
    为保留「按引用数/文本块数排序」与前端字段，额外包含 citations /
    chunk_count / index_size / source 列；journal 为本次新增列。
    """

    __tablename__ = "papers"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False)
    authors = Column(JSON, default=list, nullable=False)
    abstract = Column(Text, default="", nullable=False)
    category = Column(String, default="all", nullable=False)
    tags = Column(JSON, default=list, nullable=False)
    year = Column(Integer, default=0, nullable=False)
    journal = Column(String, default="", nullable=False)
    pdf_url = Column(String, default="", nullable=False)
    citations = Column(Integer, default=0, nullable=False)
    chunk_count = Column(Integer, default=0, nullable=False)
    index_size = Column(Integer, default=0, nullable=False)
    source = Column(String, default="arxiv", nullable=False)
    # B1: PDF 全文内容（可空，提取失败或未回填时为 NULL）
    full_text = Column(Text, nullable=True)
    # B2: Semantic Scholar 富化字段（可空，未富化时为 NULL）
    influential_citations = Column(Integer, nullable=True)  # 有影响力引用数
    fields_of_study = Column(JSON, nullable=True)  # 研究领域标签，如 ["Computer Science"]

    favorites = relationship(
        "Favorite",
        back_populates="paper",
        cascade="all, delete-orphan",
    )


class Favorite(Base):
    """收藏表（为后续扩展预留，例如记录收藏时间、用户等）。"""

    __tablename__ = "favorites"

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    paper = relationship("Paper", back_populates="favorites")


class LLMConfig(Base):
    """大模型配置表 —— 存储用户自定义添加的 LLM 连接信息。

    所有 AI 能力（问答 / 综述生成 / 语义搜索）均通过 LLMFactory 从此表
    加载配置，实现运行时动态切换。Provider 类型由 api_url 关键词自动识别，
    不匹配时默认使用 OpenAI 兼容协议。
    """

    __tablename__ = "llm_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    display_name = Column(String, nullable=False)  # 显示名称，如 "我的 GLM-4"
    api_url = Column(String, nullable=False)  # API 地址，如 https://open.bigmodel.cn/api/paas/v4
    api_key = Column(String, default="", nullable=False)  # API Key（可选，本地模型可留空）
    model_id = Column(String, nullable=False)  # 模型 ID，如 glm-4、gpt-4o、deepseek-chat
    enabled = Column(Boolean, default=True, nullable=False)  # 启用/禁用
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class WritingProject(Base):
    """论文写作项目表。

    一个项目对应一篇正在撰写的论文，包含标题、关键词、目标期刊等元数据。
    项目下挂载一棵章节大纲树（Chapter 通过 project_id + parent_id 递归关联）。
    """

    __tablename__ = "writing_projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String, nullable=False)
    keywords = Column(JSON, default=list, nullable=False)  # 关键词列表
    target_journal = Column(String, default="", nullable=False)  # 目标期刊
    target_word_count = Column(Integer, default=0, nullable=False)  # 目标字数（0=未设定）
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer,
        ForeignKey("writing_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    parent_id = Column(
        Integer,
        ForeignKey("writing_chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )  # NULL 表示根章节
    title = Column(String, nullable=False)
    content = Column(Text, default="", nullable=False)  # Markdown 内容
    order = Column(Integer, default=0, nullable=False)  # 同级排序
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

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

    id = Column(String(36), primary_key=True)  # UUID
    chapter_id = Column(
        Integer,
        ForeignKey("writing_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = Column(Text, nullable=False)  # 该轮续写生成的内容
    direction = Column(String, default="", nullable=False)  # 续写方向，可空
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    chapter = relationship("Chapter", back_populates="continuations")


class PaperNote(Base):
    """论文笔记表 —— 用户为每篇论文添加的个人笔记（可与写作项目关联）。

    project_id 为可选外键：为 NULL 表示普通笔记，非 NULL 表示与某写作项目关联。
    """

    __tablename__ = "paper_notes"

    id = Column(String, primary_key=True)  # UUID
    paper_id = Column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id = Column(
        Integer,
        ForeignKey("writing_projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    content = Column(Text, default="", nullable=False)  # Markdown 内容
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class UserTemplate(Base):
    """用户自定义写作模板 —— 由当前项目大纲保存而来，可在新建项目时选择。"""

    __tablename__ = "user_templates"

    id = Column(String, primary_key=True)  # UUID
    name = Column(String, nullable=False)
    description = Column(String, default="", nullable=False)
    chapters = Column(JSON, default=list, nullable=False)  # 大纲结构（格式同系统模板）
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WritingSnapshot(Base):
    """写作字数每日快照 —— 记录每个项目每日的字数，用于趋势图。

    每日首次打开项目时自动记录一条（project_id + date 唯一）。
    """

    __tablename__ = "writing_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer,
        ForeignKey("writing_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date = Column(String, nullable=False)  # 'YYYY-MM-DD'
    word_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ChapterVersion(Base):
    """章节历史版本 —— 每次保存章节内容时自动记录一个快照（最多保留 10 个）。"""

    __tablename__ = "chapter_versions"

    id = Column(String, primary_key=True)  # UUID
    chapter_id = Column(
        Integer,
        ForeignKey("writing_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = Column(Text, default="", nullable=False)  # 该版本的章节内容
    word_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PaperEmbedding(Base):
    """论文向量嵌入表 —— 存储「标题+摘要」的向量表示（384 维，BAAI/bge-small-en-v1.5）。

    混合检索（FTS5 + 向量 + RRF）时，向量检索计算用户查询向量与所有论文向量的
    余弦相似度，返回 Top N。若本表为空或 fastembed 未安装/未联网，降级为纯 FTS5。
    embedding 以 JSON 数组字符串存储（避免引入 numpy 二进制列依赖）。
    """

    __tablename__ = "paper_embeddings"

    paper_id = Column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding = Column(Text, nullable=False)  # JSON 序列化的 384 维向量
    dim = Column(Integer, default=384, nullable=False)  # 维度（便于校验）
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class PdfAnnotation(Base):
    """PDF 批注/高亮表 —— 存储用户在 PDF 预览中创建的文本高亮与批注。

    B4：支持在 PDF 预览中选中文本并高亮，添加批注。
    - 高亮数据主要存储在 localStorage（离线优先），此表为可选同步目标。
    - quadpoints 存储 PDF 选区的四边形坐标（JSON 数组），用于精确还原高亮位置。
    - color 为高亮颜色（如 #FFEB3B 黄色）。
    - note 为可选的批注文字。
    """

    __tablename__ = "pdf_annotations"

    id = Column(String(36), primary_key=True)  # UUID
    paper_id = Column(
        String,
        ForeignKey("papers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    page = Column(Integer, nullable=False)  # 页码（从 1 开始）
    quadpoints = Column(JSON, nullable=False)  # 四边形坐标数组
    color = Column(String, default="#FFEB3B", nullable=False)  # 高亮颜色
    note = Column(Text, default="", nullable=False)  # 批注文字（可选）
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
