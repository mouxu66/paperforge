"""写作工作台 CRUD：项目/章节/模板/导出/字数统计/学术诚信（provenance + 引用校验）。

待拆分 god-file（~900 行）：建议按功能域拆为 writing_core / writing_export / writing_citations。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..author_utils import parse_authors
from ..data import TEMPLATES
from ..models import (
    Chapter as ChapterORM,
)
from ..models import (
    ChapterContinuation as ChapterContinuationORM,
)
from ..models import (
    ChapterVersion as ChapterVersionORM,
)
from ..models import (
    Paper as PaperORM,
)
from ..models import (
    UserTemplate as UserTemplateORM,
)
from ..models import (
    WritingProject as WritingProjectORM,
)
from ..schemas import (
    ChapterCreate,
    ChapterResponse,
    ChapterRestoreResponse,
    ChapterTreeNode,
    ChapterUpdate,
    ChapterVersionDetail,
    ChapterVersionResponse,
    ExportReference,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    TemplateChapter,
    TemplateResponse,
    UserTemplateCreate,
    UserTemplateResponse,
    WordCountItem,
    WordCountResponse,
)
from ._shared import _fmt_dt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 项目 CRUD
# ---------------------------------------------------------------------------
def _project_to_response(p: WritingProjectORM, chapter_count: int = 0) -> ProjectResponse:
    """ORM 行 → camelCase 响应。chapter_count 由调用方统计后传入（避免 N+1）。"""
    return ProjectResponse(
        id=p.id,
        title=p.title,
        keywords=list(p.keywords or []),
        targetJournal=p.target_journal or "",
        targetWordCount=p.target_word_count or 0,
        chapterCount=chapter_count,
        createdAt=_fmt_dt(p.created_at),
        updatedAt=_fmt_dt(p.updated_at),
    )


def create_project(db: Session, payload: ProjectCreate) -> ProjectResponse:
    """创建写作项目。

    - 若 payload.templateId 指定模板，按模板结构递归创建章节大纲。
    - 否则创建一个「引言」根章节作为初始内容。
    """
    project = WritingProjectORM(
        title=payload.title.strip(),
        keywords=list(payload.keywords or []),
        target_journal=(payload.targetJournal or "").strip(),
        target_word_count=0,
    )
    db.add(project)
    db.flush()  # 取 project.id

    tmpl = _find_template(payload.templateId) if payload.templateId else None
    if not tmpl and payload.templateId:
        # 系统模板未命中，尝试用户自定义模板
        tmpl = find_user_template(db, payload.templateId)
    if tmpl:
        _create_chapters_recursive(db, project.id, tmpl["chapters"], parent_id=None)
    else:
        # 无模板：自动创建「引言」章节作为初始大纲
        db.add(
            ChapterORM(
                project_id=project.id,
                parent_id=None,
                title="引言",
                content="",
                order=0,
            )
        )

    db.commit()
    db.refresh(project)
    cnt = db.query(ChapterORM).filter(ChapterORM.project_id == project.id).count()
    return _project_to_response(project, chapter_count=cnt)


def _find_template(template_id: str) -> dict | None:
    """按 id 查找模板字典，不存在返回 None。"""
    for t in TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


def _create_chapters_recursive(
    db: Session,
    project_id: int,
    chapters_data: list[dict],
    parent_id: int | None,
) -> None:
    """递归创建章节树（从模板数据构建）。

    chapters_data 为模板中的章节列表，每项含 title 与可选 children。
    """
    for i, ch in enumerate(chapters_data):
        chapter = ChapterORM(
            project_id=project_id,
            parent_id=parent_id,
            title=ch.get("title", "未命名章节"),
            content="",
            order=i,
        )
        db.add(chapter)
        db.flush()  # 取 chapter.id
        children = ch.get("children") or []
        if children:
            _create_chapters_recursive(db, project_id, children, parent_id=chapter.id)


def get_templates() -> list[TemplateResponse]:
    """返回所有写作模板（供前端选择）。"""
    return [
        TemplateResponse(
            id=t["id"],
            name=t["name"],
            description=t.get("description", ""),
            chapters=_dict_to_template_chapters(t.get("chapters", [])),
        )
        for t in TEMPLATES
    ]


def _dict_to_template_chapters(items: list[dict]) -> list[TemplateChapter]:
    """递归将模板字典列表转为 TemplateChapter schema 列表。"""
    result: list[TemplateChapter] = []
    for item in items:
        result.append(
            TemplateChapter(
                title=item.get("title", ""),
                children=_dict_to_template_chapters(item.get("children") or []),
            )
        )
    return result


def get_projects(db: Session) -> list[ProjectResponse]:
    """列出所有写作项目（按创建时间倒序，附带章节计数）。"""
    rows = db.query(WritingProjectORM).order_by(WritingProjectORM.id.desc()).all()
    if not rows:
        return []
    # 一次性统计所有项目的章节数，避免 N+1 查询
    counts: dict[int, int] = {
        r[0]: r[1]
        for r in db.query(ChapterORM.project_id, func.count(ChapterORM.id))
        .group_by(ChapterORM.project_id)
        .all()
    }
    return [_project_to_response(p, chapter_count=counts.get(p.id, 0)) for p in rows]


def get_project(db: Session, project_id: int) -> ProjectResponse | None:
    """获取单个项目（含章节计数）。"""
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None
    cnt = db.query(ChapterORM).filter(ChapterORM.project_id == project_id).count()
    return _project_to_response(p, chapter_count=cnt)


def update_project(db: Session, project_id: int, payload: ProjectUpdate) -> ProjectResponse | None:
    """更新项目元数据（仅更新非 None 字段）。"""
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None
    if payload.title is not None:
        p.title = payload.title.strip()
    if payload.keywords is not None:
        p.keywords = list(payload.keywords)
    if payload.targetJournal is not None:
        p.target_journal = payload.targetJournal.strip()
    if payload.targetWordCount is not None:
        p.target_word_count = payload.targetWordCount
    db.commit()
    db.refresh(p)
    cnt = db.query(ChapterORM).filter(ChapterORM.project_id == project_id).count()
    return _project_to_response(p, chapter_count=cnt)


def delete_project(db: Session, project_id: int) -> bool:
    """删除项目及其所有章节（通过 ORM cascade 级联删除）。"""
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return False
    db.delete(p)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# 章节 CRUD
# ---------------------------------------------------------------------------
def _chapter_to_response(c: ChapterORM) -> ChapterResponse:
    """ORM 行 → camelCase 响应（不递归子节点）。"""
    return ChapterResponse(
        id=c.id,
        projectId=c.project_id,
        parentId=c.parent_id,
        title=c.title,
        content=c.content or "",
        order=c.order,
        createdAt=_fmt_dt(c.created_at),
        updatedAt=_fmt_dt(c.updated_at),
    )


def _max_sibling_order(db: Session, project_id: int, parent_id: int | None) -> int:
    """返回当前同级章节中最大的 order 值（无则返回 -1，便于 +1 起步）。"""
    q = db.query(ChapterORM.order).filter(ChapterORM.project_id == project_id)
    if parent_id is None:
        q = q.filter(ChapterORM.parent_id.is_(None))
    else:
        q = q.filter(ChapterORM.parent_id == parent_id)
    row = q.order_by(ChapterORM.order.desc()).first()
    return row[0] if row else -1


def create_chapter(db: Session, project_id: int, payload: ChapterCreate) -> ChapterResponse:
    """在项目下创建章节。

    - parentId 指定时挂到对应父章节下；为 None 时作为根章节。
    - order 未显式指定（保持默认 0）时，自动追加到同级末尾。
    """
    # 校验项目存在
    project = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not project:
        raise ValueError(f"项目 {project_id} 不存在")

    parent_id = payload.parentId
    # 若指定父章节，校验其归属同一项目
    if parent_id is not None:
        parent = db.query(ChapterORM).filter(ChapterORM.id == parent_id).first()
        if not parent or parent.project_id != project_id:
            raise ValueError("父章节不存在或不属于当前项目")

    # order 默认追加到同级末尾
    base_order = _max_sibling_order(db, project_id, parent_id) + 1
    chapter = ChapterORM(
        project_id=project_id,
        parent_id=parent_id,
        title=payload.title.strip(),
        content=payload.content or "",
        order=payload.order if payload.order else base_order,
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return _chapter_to_response(chapter)


def update_chapter(db: Session, chapter_id: int, payload: ChapterUpdate) -> ChapterResponse | None:
    """更新章节（title/content/order，仅更新非 None 字段）。

    当 content 变更时，自动保存一个历史版本快照（最多保留 10 个）。
    """
    c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not c:
        return None
    if payload.title is not None:
        c.title = payload.title.strip()
    if payload.content is not None:
        # 内容变更时保存历史版本（仅当内容确实不同）
        if c.content != payload.content and (c.content or payload.content):
            create_chapter_version(db, chapter_id, c.content or "")
        c.content = payload.content
    if payload.order is not None:
        c.order = payload.order
    db.commit()
    db.refresh(c)
    return _chapter_to_response(c)


def get_chapter(db: Session, chapter_id: int) -> ChapterORM | None:
    """获取单个章节 ORM（供 AI 生成等场景使用）。"""
    return db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()


def move_chapter(
    db: Session, chapter_id: int, parent_id: int | None, index: int
) -> ChapterResponse | None:
    """移动 / 拖拽重排章节：挂到 parent_id 下并插入到指定 index 位置。

    - 校验目标父章节归属同一项目
    - 禁止将章节移到自身或其子孙节点下（防止环）
    - 插入后对同级章节重新连续编号 order（0,1,2…）
    - index 超出范围时自动 clamp 到 [0, 兄弟数]
    """
    c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not c:
        return None
    project_id = c.project_id

    # 校验父章节归属同一项目
    if parent_id is not None:
        parent = db.query(ChapterORM).filter(ChapterORM.id == parent_id).first()
        if not parent or parent.project_id != project_id:
            raise ValueError("目标父章节不存在或不属于当前项目")
        # 禁止移到自身或自身子孙下（防止环）
        if parent_id == chapter_id or _is_descendant(db, chapter_id, parent_id):
            raise ValueError("不能将章节移到自身或其子孙节点下")

    c.parent_id = parent_id
    db.flush()

    # 取新父节点下的兄弟（排除自身），按现有 order 排序
    q = db.query(ChapterORM).filter(
        ChapterORM.project_id == project_id,
        ChapterORM.id != chapter_id,
    )
    if parent_id is None:
        q = q.filter(ChapterORM.parent_id.is_(None))
    else:
        q = q.filter(ChapterORM.parent_id == parent_id)
    siblings = q.order_by(ChapterORM.order.asc(), ChapterORM.id.asc()).all()

    # 插入到目标 index（clamp）
    idx = max(0, min(index, len(siblings)))
    siblings.insert(idx, c)
    # 重新连续编号
    for i, s in enumerate(siblings):
        s.order = i
    db.commit()
    db.refresh(c)
    return _chapter_to_response(c)


def _is_descendant(db: Session, ancestor_id: int, candidate_id: int) -> bool:
    """判断 candidate_id 是否为 ancestor_id 的子孙（递归）。"""
    children = db.query(ChapterORM).filter(ChapterORM.parent_id == ancestor_id).all()
    for ch in children:
        if ch.id == candidate_id:
            return True
        if _is_descendant(db, ch.id, candidate_id):
            return True
    return False


def build_outline_context(db: Session, project_id: int) -> str:
    """构建项目大纲的缩进文本（供 AI 生成时提供结构上下文）。"""
    tree = get_chapter_tree(db, project_id)
    lines: list[str] = []

    def walk(nodes: list[ChapterTreeNode], depth: int) -> None:
        for n in nodes:
            lines.append(f"{'  ' * depth}- {n.title}")
            walk(n.children, depth + 1)

    walk(tree, 0)
    return "\n".join(lines)


def get_chapter_context(db: Session, chapter_id: int) -> dict | None:
    """获取章节上下文：标题、内容、所属项目 ID、完整大纲。

    供「智能续写」和「结构建议」两个 AI 辅助功能复用。
    返回 None 表示章节不存在。
    """
    c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not c:
        return None
    outline = build_outline_context(db, c.project_id)
    return {
        "chapter_id": c.id,
        "project_id": c.project_id,
        "title": c.title,
        "content": c.content or "",
        "outline": outline,
    }


def delete_chapter(db: Session, chapter_id: int) -> bool:
    """删除章节及其所有子孙节点（通过 ORM cascade 级联删除 children）。"""
    c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not c:
        return False
    db.delete(c)
    db.commit()
    return True


def get_chapter_tree(db: Session, project_id: int) -> list[ChapterTreeNode]:
    """获取项目的章节大纲树（按 order 递归构建）。

    一次性拉取项目所有章节，在 Python 侧按 parent_id 分组并递归组装，
    避免 N+1 查询。同级章节按 order 升序排列。
    """
    rows = (
        db.query(ChapterORM)
        .filter(ChapterORM.project_id == project_id)
        .order_by(ChapterORM.order.asc())
        .all()
    )
    if not rows:
        return []

    # 按 parent_id 分组（None → 根）
    by_parent: dict[int | None, list[ChapterORM]] = {}
    for r in rows:
        by_parent.setdefault(r.parent_id, []).append(r)

    def build(parent_id: int | None) -> list[ChapterTreeNode]:
        children = by_parent.get(parent_id, [])
        nodes: list[ChapterTreeNode] = []
        for c in children:
            node = ChapterTreeNode(
                **_chapter_to_response(c).model_dump(),
                children=build(c.id),
            )
            nodes.append(node)
        return nodes

    return build(None)


# ---------------------------------------------------------------------------
# C2: 续写历史
# ---------------------------------------------------------------------------
MAX_CONTINUATIONS_PER_CHAPTER = 10


def create_continuation(
    db: Session,
    chapter_id: int,
    content: str,
    direction: str = "",
    kind: str = "continue",
) -> dict | None:
    """保存一条续写/改写历史记录，并裁剪到最多 MAX 条。

    学术诚信 provenance：kind 标记该条由 AI 生成的类型
    （'continue' 智能续写 / 'rewrite' AI 改写），供导出时汇总「AI 使用声明」。

    Returns:
        新建记录的字典 {id, chapterId, content, direction, kind, createdAt}，
        章节不存在时返回 None。
    """
    chapter = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not chapter:
        return None
    rec = ChapterContinuationORM(
        id=str(uuid.uuid4()),
        chapter_id=chapter_id,
        content=content,
        direction=direction or "",
        kind=kind or "continue",
    )
    db.add(rec)
    db.flush()
    # 裁剪：超出 MAX 条时删除最早的
    all_recs = (
        db.query(ChapterContinuationORM)
        .filter(ChapterContinuationORM.chapter_id == chapter_id)
        .order_by(ChapterContinuationORM.created_at.desc())
        .all()
    )
    for old in all_recs[MAX_CONTINUATIONS_PER_CHAPTER:]:
        db.delete(old)
    db.commit()
    db.refresh(rec)
    return _continuation_to_dict(rec)


def list_continuations(db: Session, chapter_id: int) -> list[dict]:
    """获取章节的续写历史（按时间倒序，最多 MAX 条）。"""
    rows = (
        db.query(ChapterContinuationORM)
        .filter(ChapterContinuationORM.chapter_id == chapter_id)
        .order_by(ChapterContinuationORM.created_at.desc())
        .limit(MAX_CONTINUATIONS_PER_CHAPTER)
        .all()
    )
    return [_continuation_to_dict(r) for r in rows]


def _continuation_to_dict(r: ChapterContinuationORM) -> dict:
    """ORM 行转字典（供路由层直接返回）。"""
    return {
        "id": r.id,
        "chapterId": r.chapter_id,
        "content": r.content,
        "direction": r.direction or "",
        "kind": r.kind or "continue",
        "createdAt": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


def get_project_ai_usage(db: Session, project_id: int) -> dict:
    """汇总项目内 AI 辅助使用量（学术诚信 provenance）。

    统计项目所有章节的续写/改写历史：段数、改写次数、AI 生成总字数、
    涉及 AI 的章节数。供导出时生成「AI 使用声明」，前端也可用于导出前预览。
    """
    chapter_ids = [
        c.id for c in db.query(ChapterORM.id).filter(ChapterORM.project_id == project_id).all()
    ]
    if not chapter_ids:
        return {
            "has_ai": False,
            "continuation_count": 0,
            "rewrite_count": 0,
            "ai_chars": 0,
            "chapters_with_ai": 0,
        }
    recs = (
        db.query(ChapterContinuationORM)
        .filter(ChapterContinuationORM.chapter_id.in_(chapter_ids))
        .all()
    )
    continuation_count = 0
    rewrite_count = 0
    ai_chars = 0
    chapters_with_ai: set[int] = set()
    for r in recs:
        chapters_with_ai.add(r.chapter_id)
        ai_chars += len(r.content or "")
        if (r.kind or "continue") == "rewrite":
            rewrite_count += 1
        else:
            continuation_count += 1
    return {
        "has_ai": len(recs) > 0,
        "continuation_count": continuation_count,
        "rewrite_count": rewrite_count,
        "ai_chars": ai_chars,
        "chapters_with_ai": len(chapters_with_ai),
    }


# ---------------------------------------------------------------------------
# 字数统计
# ---------------------------------------------------------------------------
# 去除 Markdown 语法标记的正则（顺序敏感：先去代码块再去行内代码）
_MD_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_MD_INLINE_CODE = re.compile(r"`[^`]+`")
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_CITE = re.compile(r"\[@[^\]]+\]")
_MD_HEADER = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_LIST = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_ENUM = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MD_QUOTE = re.compile(r"^\s*>\s*", re.MULTILINE)
_MD_HR = re.compile(r"^\s*[-*]{3,}\s*$", re.MULTILINE)
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_ITALIC = re.compile(r"\*([^*]+)\*")
_MD_UNDERSCORE = re.compile(r"__([^_]+)__")
_MD_WHITESPACE = re.compile(r"\s+")


def _count_words(text: str) -> int:
    """统计纯文本字数（排除 Markdown 标记）。

    规则：依次去除代码块、行内代码、图片/链接语法（保留链接文本）、
    引用标记 [@id]、标题/列表/引用/水平线标记、粗体/斜体标记，
    最后统计非空白字符数（适用于中英文混合文本）。
    """
    if not text:
        return 0
    t = _MD_CODE_BLOCK.sub("", text)
    t = _MD_INLINE_CODE.sub("", t)
    t = _MD_IMAGE.sub(r"\1", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_CITE.sub("", t)
    t = _MD_HEADER.sub("", t)
    t = _MD_LIST.sub("", t)
    t = _MD_ENUM.sub("", t)
    t = _MD_QUOTE.sub("", t)
    t = _MD_HR.sub("", t)
    t = _MD_BOLD.sub(r"\1", t)
    t = _MD_ITALIC.sub(r"\1", t)
    t = _MD_UNDERSCORE.sub(r"\1", t)
    return len(_MD_WHITESPACE.sub("", t))


def get_word_count(db: Session, project_id: int) -> WordCountResponse | None:
    """统计项目所有章节的字数。

    返回 {total, chapters: [{id, title, wordCount}]}。
    """
    rows = (
        db.query(ChapterORM)
        .filter(ChapterORM.project_id == project_id)
        .order_by(ChapterORM.order.asc(), ChapterORM.id.asc())
        .all()
    )
    if not rows:
        # 项目可能存在但无章节
        proj = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
        if not proj:
            return None
        return WordCountResponse(total=0, chapters=[])

    items = [
        WordCountItem(id=r.id, title=r.title, wordCount=_count_words(r.content or "")) for r in rows
    ]
    total = sum(i.wordCount for i in items)
    return WordCountResponse(total=total, chapters=items)


# ---------------------------------------------------------------------------
# 项目导出（拼接所有章节 Markdown + 解析 [@id] 引用）
# ---------------------------------------------------------------------------
# 匹配章节内容中的 [@paper_id] 引用标记（paper_id 不含 ])
_CITE_RE = re.compile(r"\[@([^\]]+)\]")


def validate_chapter_citations(db: Session, chapter_id: int, content: str | None = None) -> dict:
    """校验章节正文中的 [@paper_id] 引用是否命中真实论文库（防伪造引用）。

    学术诚信护栏 #2：AI 续写/改写可能在提示下插入 [@论文ID]，若模型编造
    不存在的 id 即构成伪造引用。本函数扫描正文全部引用并比对论文库，
    返回疑似伪造（库中不存在）的 id 列表，供前端高亮 + 导出时显式标注。

    content 为 None 时读取服务端章节正文（用于导出前预览）；传入实时 UI
    文本时可直接校验 AI 刚插入、尚未落库的内容（生成时即时拦截）。

    Returns:
        {
            "chapter_id": int,
            "cited": [pid, ...],    # 去重后的所有引用 id
            "valid": [pid, ...],    # 库中存在的
            "invalid": [pid, ...],  # 库中不存在（疑似伪造）
            "has_invalid": bool,
        }
    """
    if content is None:
        c = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
        if not c:
            return {
                "chapter_id": chapter_id,
                "cited": [],
                "valid": [],
                "invalid": [],
                "has_invalid": False,
            }
        content = c.content or ""
    cited: list[str] = []
    seen: set[str] = set()
    for m in _CITE_RE.finditer(content):
        pid = m.group(1).strip()
        if pid and pid not in seen:
            seen.add(pid)
            cited.append(pid)
    if not cited:
        return {
            "chapter_id": chapter_id,
            "cited": [],
            "valid": [],
            "invalid": [],
            "has_invalid": False,
        }
    rows = db.query(PaperORM.id).filter(PaperORM.id.in_(cited)).all()
    valid_set = {r.id for r in rows}
    valid = [pid for pid in cited if pid in valid_set]
    invalid = [pid for pid in cited if pid not in valid_set]
    return {
        "chapter_id": chapter_id,
        "cited": cited,
        "valid": valid,
        "invalid": invalid,
        "has_invalid": len(invalid) > 0,
    }


def _append_ai_declaration(raw: str, db: Session, project_id: int) -> str:
    """在导出 Markdown 末尾追加「AI 使用声明」段（学术诚信护栏 #1）。

    声明基于本地 provenance 记录（ChapterContinuation）汇总，诚实区分
    「含 AI 辅助」与「独立完成」。导出四格式（md/doc/tex/pdf）均从本
    content 派生，故声明自动覆盖所有格式。
    """
    usage = get_project_ai_usage(db, project_id)
    lines = ["", "", "---", "", "## AI 使用声明", ""]
    if usage["has_ai"]:
        lines.append("本作品在写作过程中使用了 PaperForge 的 AI 辅助功能：")
        lines.append(f"- 智能续写：{usage['continuation_count']} 段（约 {usage['ai_chars']} 字）")
        if usage["rewrite_count"]:
            lines.append(f"- AI 改写：{usage['rewrite_count']} 次")
        lines.append(f"- 涉及 AI 辅助的章节：{usage['chapters_with_ai']} 个")
        lines.append("")
        lines.append("上述 AI 生成内容均由作者本人审阅、修改，并承担全部学术责任。")
        lines.append(
            "文中引用均来自作者本人管理的论文库，AI 未虚构参考文献；"
            "导出时已对库中不存在的引用显式标注为「未找到论文」。"
        )
    else:
        lines.append("本作品由作者独立完成，未使用任何 AI 生成辅助（基于本地 provenance 记录）。")
    lines.append("")
    lines.append(f"> 声明生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return raw + "\n".join(lines) + "\n"


def export_project_markdown(
    db: Session,
    project_id: int,
    progress_cb: Callable[[int, int], None] | None = None,
) -> tuple[str, str, list[ExportReference]] | None:
    """将项目所有章节按大纲顺序拼接为 Markdown，并解析 [@id] 引用。

    返回 (content, filename, references)：
    - content：拼接后的 Markdown 全文，[@id] 已替换为 [N] 编号
    - filename：建议的下载文件名（不含扩展名）
    - references：按首次出现顺序去重的参考文献列表

    P2-2 改进：
    - progress_cb(current, total)：每处理完一批章节后回调，用于精细化进度展示
    - 章节按深度优先展平后分批处理（每批 20 个），大文档导出不再长时间阻塞

    拼接规则：
    - 顶层标题「# 项目标题」
    - 每个章节按层级生成 Markdown 标题（根章节 ##，逐级 +1，封顶 6 级）
    - 章节内容中的 [@paper_id] 替换为 [1][2]… 顺序编号
    - 末尾追加「## 参考文献」列表（仅包含正文中实际引用的论文）
    """
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None

    tree = get_chapter_tree(db, project_id)

    lines: list[str] = [f"# {p.title}", ""]
    if p.keywords:
        lines.append(f"> 关键词：{', '.join(list(p.keywords))}")
        lines.append("")
    if p.target_journal:
        lines.append(f"> 目标期刊：{p.target_journal}")
        lines.append("")

    # P2-2: 将章节树深度优先展平为列表，便于分批处理
    flat: list[tuple[ChapterTreeNode, int]] = []

    def flatten(nodes: list[ChapterTreeNode], depth: int) -> None:
        for node in nodes:
            flat.append((node, depth))
            flatten(node.children, depth + 1)

    flatten(tree, 2)
    total = len(flat)

    # P2-2: 分批渲染（每批 20 章节），每批结束后通过 progress_cb 回调
    BATCH_SIZE = 20
    for i in range(0, total, BATCH_SIZE):
        batch = flat[i : i + BATCH_SIZE]
        for node, depth in batch:
            level = min(depth, 6)
            lines.append("")
            lines.append(f"{'#' * level} {node.title}")
            lines.append("")
            if node.content:
                lines.append(node.content)
        if progress_cb:
            progress_cb(min(i + BATCH_SIZE, total), total)

    raw = "\n".join(lines).strip() + "\n"

    # ---- 解析 [@id] 引用：按首次出现顺序去重 ----
    cited_ids: list[str] = []
    seen: set[str] = set()
    for m in _CITE_RE.finditer(raw):
        pid = m.group(1).strip()
        if pid and pid not in seen:
            seen.add(pid)
            cited_ids.append(pid)

    references: list[ExportReference] = []
    if cited_ids:
        # 一次性从 DB 拉取所有被引论文
        rows = db.query(PaperORM).filter(PaperORM.id.in_(cited_ids)).all()
        paper_map: dict[str, PaperORM] = {r.id: r for r in rows}
        for pid in cited_ids:
            paper = paper_map.get(pid)
            if paper:
                references.append(
                    ExportReference(
                        id=paper.id,
                        title=paper.title,
                        authors=parse_authors(paper.authors),
                        year=paper.year or 0,
                    )
                )
            else:
                # 论文不在库中，保留占位条目（便于用户发现失效引用）
                references.append(
                    ExportReference(id=pid, title=f"[未找到论文：{pid}]", authors=[], year=0)
                )

        # 构建 id → 编号 映射，替换 [@id] 为 [N]
        id_to_num = {pid: i + 1 for i, pid in enumerate(cited_ids)}

        def _replace(m: re.Match) -> str:
            return f"[{id_to_num.get(m.group(1).strip(), 0)}]"

        raw = _CITE_RE.sub(_replace, raw)

        # 追加参考文献列表
        raw += "\n\n## 参考文献\n\n"
        for i, ref in enumerate(references):
            parts = [f"[{i + 1}]", ref.title]
            if ref.authors:
                parts.append(", ".join(ref.authors))
            if ref.year:
                parts.append(str(ref.year))
            parts.append(f"arXiv:{ref.id}")
            raw += ". ".join(parts) + "\n"

    safe_title = re.sub(r"[\\/:*?\"<>|\s]+", "_", p.title).strip("_") or "untitled"
    filename = safe_title

    # 学术诚信护栏 #1：末尾追加「AI 使用声明」（四格式自动继承）
    raw = _append_ai_declaration(raw, db, project_id)

    return raw, filename, references


def get_project_updated_at(db: Session, project_id: int) -> str | None:
    """P2-2: 获取项目的 updated_at（ISO 字符串），用于导出缓存失效判断。

    项目不存在时返回 None。
    """
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None
    return p.updated_at.isoformat() if p.updated_at else ""


# CSL type heuristics
_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.IGNORECASE)
_ARXIV_RE = re.compile(r"^\d{4,5}\.\d{4,5}$")


def _looks_like_doi(text: str) -> bool:
    return bool(_DOI_RE.match(text.strip()))


def _looks_like_arxiv(text: str) -> bool:
    t = text.strip()
    return t.lower().startswith("arxiv") or bool(_ARXIV_RE.match(t))


def _arxiv_abs_url(text: str) -> str | None:
    t = text.strip()
    if t.lower().startswith("arxiv:"):
        return f"https://arxiv.org/abs/{t.split(':', 1)[1]}"
    if _ARXIV_RE.match(t):
        return f"https://arxiv.org/abs/{t}"
    return None


def _parse_csl_author(name: str) -> dict:
    """Parse a single author string into CSL given/family parts.

    Supports:
    - "Family, Given"  (e.g. "Vaswani, Ashish")
    - "Given Family"   (e.g. "Ashish Vaswani")
    - single token     (e.g. "Anonymous")
    """
    name = name.strip()
    if not name:
        return {"family": "", "given": ""}
    if "," in name:
        family, given = [p.strip() for p in name.split(",", 1)]
        return {"family": family, "given": given}
    parts = name.split()
    if len(parts) > 1:
        return {"family": parts[-1], "given": " ".join(parts[:-1])}
    return {"family": name, "given": ""}


def _detect_csl_type(ref_id: str, paper: PaperORM | None) -> str:
    """Infer CSL item type from available metadata.

    - journal present -> article-journal
    - arXiv / preprint -> article (preprint)
    - DOI -> article-journal
    - fallback -> document
    """
    if paper and paper.journal:
        return "article-journal"
    if _looks_like_arxiv(ref_id):
        return "article"
    if _looks_like_doi(ref_id):
        return "article-journal"
    return "document"


def export_project_csl(db: Session, project_id: int) -> list[dict] | None:
    """Export project references as CSL-JSON.

    Reuses export_project_markdown citation parsing, then enriches each
    reference with full Paper metadata (journal, DOI, URL, etc.).
    """
    result = export_project_markdown(db, project_id)
    if result is None:
        return None
    _, _, references = result

    paper_ids = [ref.id for ref in references]
    paper_map: dict[str, PaperORM] = {}
    if paper_ids:
        paper_map = {p.id: p for p in db.query(PaperORM).filter(PaperORM.id.in_(paper_ids)).all()}

    csl_items: list[dict] = []
    for ref in references:
        paper = paper_map.get(ref.id)
        authors = [_parse_csl_author(name) for name in (ref.authors or []) if name.strip()]

        item: dict = {
            "id": ref.id,
            "type": _detect_csl_type(ref.id, paper),
            "title": ref.title,
            "author": authors,
        }
        if ref.year:
            item["issued"] = {"date-parts": [[ref.year]]}

        # Enrich with full paper metadata when available
        if paper:
            if paper.journal:
                item["container-title"] = paper.journal
            if paper.pdf_url:
                item["URL"] = paper.pdf_url

        # DOI / URL fallbacks
        if _looks_like_doi(ref.id):
            item["DOI"] = ref.id
        elif _arxiv_abs_url(ref.id):
            item["URL"] = _arxiv_abs_url(ref.id)

        csl_items.append(item)
    return csl_items


# BibTeX 模板
_BIBTEX_TEMPLATE = """@article{{{key},
  title={{{title}}},
  author={{{authors}}},
  year={{{year}}},
  journal={{{journal}}},
  note={{{note}}}
}}"""


def export_project_to_folder(
    db: Session,
    project_id: int,
    output_dir: str,
    include_vscode: bool = True,
    include_obsidian: bool = False,
) -> dict:
    """将写作项目导出为本地工程文件夹。

    生成结构：
        outputDir/
        ├── main.md           # 拼接后的完整论文 Markdown
        ├── refs.bib          # 参考文献 BibTeX
        ├── images/           # 图片目录（预留）
        ├── .vscode/          # VS Code 配置（可选）
        │   └── settings.json
        └── .obsidian/        # Obsidian 配置（可选）
            └── app.json

    Args:
        db: 数据库会话。
        project_id: 写作项目 ID。
        output_dir: 输出目录绝对路径。
        include_vscode: 是否生成 .vscode 配置。
        include_obsidian: 是否生成 .obsidian 配置。

    Returns:
        {"success": True, "outputDir": "...", "files": ["main.md", ...]}

    Raises:
        ValueError: 项目不存在。
        OSError: 写入文件失败。
    """
    # 校验项目存在
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        raise ValueError(f"项目 {project_id} 不存在")

    from ..database import DATA_DIR  # 懒导入，避免循环依赖

    # 安全护栏：导出目录必须锁定在 DATA_DIR/exports 沙箱内，禁止路径穿越写任意位置
    _exports_base = DATA_DIR / "exports"
    _exports_base.mkdir(parents=True, exist_ok=True)
    _candidate = Path(output_dir)
    if _candidate.is_absolute():
        out = _candidate.resolve()
    else:
        out = (_exports_base / _candidate).resolve()
    if not out.is_relative_to(_exports_base):
        raise ValueError(f"导出目录必须位于 {_exports_base} 下（禁止路径穿越）")
    out.mkdir(parents=True, exist_ok=True)

    files_created: list[str] = []

    # 1. 导出 Markdown 主文件
    result = export_project_markdown(db, project_id)
    if result:
        content, filename, references = result
        md_path = out / "main.md"
        md_path.write_text(content, encoding="utf-8")
        files_created.append("main.md")

        # 2. 导出参考文献 BibTeX
        if references:
            bib_lines: list[str] = []
            for i, ref in enumerate(references):
                key = ref.id.replace(".", "_").replace("/", "_")
                authors_str = " and ".join(ref.authors) if ref.authors else "Unknown"
                bib = _BIBTEX_TEMPLATE.format(
                    key=key,
                    title=ref.title.replace("{", "\\{").replace("}", "\\}"),
                    authors=authors_str,
                    year=ref.year or "",
                    journal="",
                    note=f"arXiv:{ref.id}",
                )
                bib_lines.append(bib)
            bib_path = out / "refs.bib"
            bib_path.write_text("\n".join(bib_lines), encoding="utf-8")
            files_created.append("refs.bib")

    # 3. 创建 images/ 目录（预留）
    images_dir = out / "images"
    images_dir.mkdir(exist_ok=True)
    # 放置一个 .gitkeep 保持目录
    (images_dir / ".gitkeep").touch()
    files_created.append("images/")

    # 4. VS Code 配置
    if include_vscode:
        vscode_dir = out / ".vscode"
        vscode_dir.mkdir(exist_ok=True)
        settings = {
            "files.associations": {"*.md": "markdown"},
            "markdown.preview.breaks": True,
            "editor.wordWrap": "on",
            "editor.rulers": [80, 120],
            "cSpell.language": "en",
            "[markdown]": {
                "editor.quickSuggestions": {"comments": "off", "strings": "off", "other": "off"},
            },
        }
        (vscode_dir / "settings.json").write_text(
            json.dumps(settings, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        files_created.append(".vscode/settings.json")

    # 5. Obsidian 配置
    if include_obsidian:
        obsidian_dir = out / ".obsidian"
        obsidian_dir.mkdir(exist_ok=True)
        app_config = {
            "livePreview": True,
            "readableLineLength": False,
            "strictLineBreaks": False,
            "showFrontmatter": True,
            "showLineNumber": True,
            "spellcheck": True,
            "vimMode": False,
        }
        (obsidian_dir / "app.json").write_text(
            json.dumps(app_config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (obsidian_dir / "appearance.json").write_text(
            json.dumps({"theme": "moonstone", "cssTheme": ""}, indent=2),
            encoding="utf-8",
        )
        files_created.append(".obsidian/app.json")
        files_created.append(".obsidian/appearance.json")

    logger.info(
        "本地文件夹导出完成: project=%d → %s (%d files)",
        project_id,
        output_dir,
        len(files_created),
    )

    return {
        "success": True,
        "outputDir": str(out),
        "files": files_created,
    }


# ---------------------------------------------------------------------------
# 用户自定义模板 CRUD
# ---------------------------------------------------------------------------
def _user_template_to_response(t: UserTemplateORM) -> UserTemplateResponse:
    """ORM 行 → 响应。"""
    return UserTemplateResponse(
        id=t.id,
        name=t.name,
        description=t.description or "",
        chapters=_dict_to_template_chapters(t.chapters or []),
        createdAt=_fmt_dt(t.created_at),
    )


def get_user_templates(db: Session) -> list[UserTemplateResponse]:
    """列出所有用户自定义模板（按创建时间倒序）。"""
    rows = db.query(UserTemplateORM).order_by(UserTemplateORM.created_at.desc()).all()
    return [_user_template_to_response(t) for t in rows]


def create_user_template(db: Session, payload: UserTemplateCreate) -> UserTemplateResponse:
    """新建用户自定义模板。"""
    tmpl = UserTemplateORM(
        id=str(uuid.uuid4()),
        name=payload.name.strip(),
        description=(payload.description or "").strip(),
        chapters=[c.model_dump() for c in payload.chapters],
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return _user_template_to_response(tmpl)


def save_project_as_template(
    db: Session, project_id: int, name: str, description: str = ""
) -> UserTemplateResponse | None:
    """将当前项目的大纲结构保存为自定义模板。项目不存在返回 None。"""
    p = db.query(WritingProjectORM).filter(WritingProjectORM.id == project_id).first()
    if not p:
        return None
    tree = get_chapter_tree(db, project_id)

    def _tree_to_chapters(nodes: list[ChapterTreeNode]) -> list[TemplateChapter]:
        return [
            TemplateChapter(
                title=n.title,
                children=_tree_to_chapters(n.children),
            )
            for n in nodes
        ]

    chapters_data = [c.model_dump() for c in _tree_to_chapters(tree)]
    tmpl = UserTemplateORM(
        id=str(uuid.uuid4()),
        name=name.strip(),
        description=(description or "").strip(),
        chapters=chapters_data,
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)
    return _user_template_to_response(tmpl)


def delete_user_template(db: Session, template_id: str) -> bool:
    """删除用户自定义模板。不存在返回 False。"""
    tmpl = db.query(UserTemplateORM).filter(UserTemplateORM.id == template_id).first()
    if not tmpl:
        return False
    db.delete(tmpl)
    db.commit()
    return True


def find_user_template(db: Session, template_id: str) -> dict | None:
    """按 id 查找用户自定义模板，返回字典（供 create_project 复用）。
    不存在返回 None。
    """
    t = db.query(UserTemplateORM).filter(UserTemplateORM.id == template_id).first()
    if not t:
        return None
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description or "",
        "chapters": t.chapters or [],
    }


# PLACEHOLDER: TEMPLATE_FUNCTIONS


# ---------------------------------------------------------------------------
# 章节历史版本 CRUD
# ---------------------------------------------------------------------------
MAX_VERSIONS_PER_CHAPTER = 10


def _version_to_response(v: ChapterVersionORM) -> ChapterVersionResponse:
    """ORM 行 → 列表项响应（不含 content）。"""
    return ChapterVersionResponse(
        id=v.id,
        chapterId=v.chapter_id,
        wordCount=v.word_count,
        createdAt=_fmt_dt(v.created_at),
    )


def create_chapter_version(db: Session, chapter_id: int, content: str) -> None:
    """保存章节历史版本（超过上限时自动删除最旧的）。

    word_count 由 _count_words 计算。
    """
    version = ChapterVersionORM(
        id=str(uuid.uuid4()),
        chapter_id=chapter_id,
        content=content,
        word_count=_count_words(content),
    )
    db.add(version)
    db.flush()

    # 超过上限时删除最旧的版本
    count = db.query(ChapterVersionORM).filter(ChapterVersionORM.chapter_id == chapter_id).count()
    if count > MAX_VERSIONS_PER_CHAPTER:
        oldest = (
            db.query(ChapterVersionORM)
            .filter(ChapterVersionORM.chapter_id == chapter_id)
            .order_by(ChapterVersionORM.created_at.asc())
            .first()
        )
        if oldest:
            db.delete(oldest)
    db.commit()


def get_chapter_versions(db: Session, chapter_id: int) -> list[ChapterVersionResponse]:
    """获取章节历史版本列表（按时间倒序，不含 content）。"""
    rows = (
        db.query(ChapterVersionORM)
        .filter(ChapterVersionORM.chapter_id == chapter_id)
        .order_by(ChapterVersionORM.created_at.desc())
        .all()
    )
    return [_version_to_response(v) for v in rows]


def get_chapter_version(db: Session, version_id: str) -> ChapterVersionDetail | None:
    """获取某个历史版本详情（含 content）。不存在返回 None。"""
    v = db.query(ChapterVersionORM).filter(ChapterVersionORM.id == version_id).first()
    if not v:
        return None
    return ChapterVersionDetail(
        id=v.id,
        chapterId=v.chapter_id,
        content=v.content or "",
        wordCount=v.word_count,
        createdAt=_fmt_dt(v.created_at),
    )


def restore_chapter_version(
    db: Session, chapter_id: int, version_id: str
) -> ChapterRestoreResponse | None:
    """恢复章节到指定历史版本。

    - 将章节 content 替换为该版本内容
    - 恢复前先保存当前内容为新版本（便于撤销）
    - 返回恢复后的章节信息
    版本不存在或不属于该章节时返回 None。
    """
    v = (
        db.query(ChapterVersionORM)
        .filter(
            ChapterVersionORM.id == version_id,
            ChapterVersionORM.chapter_id == chapter_id,
        )
        .first()
    )
    if not v:
        return None

    chapter = db.query(ChapterORM).filter(ChapterORM.id == chapter_id).first()
    if not chapter:
        return None

    # 恢复前先保存当前内容为新版本（便于撤销）
    if chapter.content and chapter.content != v.content:
        create_chapter_version(db, chapter_id, chapter.content)

    # 恢复章节内容
    chapter.content = v.content
    db.commit()
    db.refresh(chapter)

    return ChapterRestoreResponse(
        chapterId=chapter_id,
        versionId=version_id,
        content=v.content,
        wordCount=v.word_count,
    )
