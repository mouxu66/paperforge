"""论文笔记 CRUD。"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..models import Paper as PaperORM
from ..models import PaperNote as PaperNoteORM
from ..schemas import NoteCreate, NoteResponse, NoteUpdate
from ._shared import _fmt_dt


def _note_to_response(n: PaperNoteORM) -> NoteResponse:
    """ORM 行 → camelCase 响应。"""
    return NoteResponse(
        id=n.id,
        paperId=n.paper_id,
        projectId=n.project_id,
        content=n.content or "",
        createdAt=_fmt_dt(n.created_at),
        updatedAt=_fmt_dt(n.updated_at),
    )


def get_notes_by_paper(db: Session, paper_id: str) -> list[NoteResponse]:
    """获取某篇论文的所有笔记（按更新时间倒序）。"""
    rows = (
        db.query(PaperNoteORM)
        .filter(PaperNoteORM.paper_id == paper_id)
        .order_by(PaperNoteORM.updated_at.desc())
        .all()
    )
    return [_note_to_response(n) for n in rows]


def get_notes_by_project(db: Session, project_id: int) -> list[NoteResponse]:
    """获取某写作项目关联的所有笔记。"""
    rows = (
        db.query(PaperNoteORM)
        .filter(PaperNoteORM.project_id == project_id)
        .order_by(PaperNoteORM.updated_at.desc())
        .all()
    )
    return [_note_to_response(n) for n in rows]


def create_note(db: Session, payload: NoteCreate) -> NoteResponse | None:
    """新建笔记。论文不存在时返回 None。"""
    paper = db.query(PaperORM).filter(PaperORM.id == payload.paperId).first()
    if not paper:
        return None
    note = PaperNoteORM(
        id=str(uuid.uuid4()),
        paper_id=payload.paperId,
        project_id=payload.projectId,
        content=payload.content or "",
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return _note_to_response(note)


def update_note(db: Session, note_id: str, payload: NoteUpdate) -> NoteResponse | None:
    """更新笔记内容。不存在返回 None。"""
    note = db.query(PaperNoteORM).filter(PaperNoteORM.id == note_id).first()
    if not note:
        return None
    note.content = payload.content
    db.commit()
    db.refresh(note)
    return _note_to_response(note)


def delete_note(db: Session, note_id: str) -> bool:
    """删除笔记。不存在返回 False。"""
    note = db.query(PaperNoteORM).filter(PaperNoteORM.id == note_id).first()
    if not note:
        return False
    db.delete(note)
    db.commit()
    return True
