"""论文写作工作台路由（模板 / 项目 / 章节 / AI 续写 / 导出 / 统计）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import crud
from ..database import DATA_DIR, get_db
from ..export_tasks import get_export_task, start_export_task
from ..llm import ChatMessage, LLMError, get_factory
from ..models import Chapter as ChapterORM
from ..schemas import (
    AdoptStructureRequest,
    ChapterCreate,
    ChapterGenerateResponse,
    ChapterMove,
    ChapterResponse,
    ChapterRestoreResponse,
    ChapterTreeNode,
    ChapterUpdate,
    ChapterVersionDetail,
    ChapterVersionResponse,
    ContinuationRequest,
    ExportReference,
    ExportTaskCreateResponse,
    ExportTaskProgressResponse,
    FolderExportRequest,
    GenerateOutlineRequest,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    RecommendCitationsRequest,
    RecommendCitationsResponse,
    RecommendedPaper,
    RewriteRequest,
    SaveContinuationRequest,
    TemplateResponse,
    UserTemplateCreate,
    UserTemplateResponse,
    ValidateCitationsRequest,
    WordCountResponse,
    WritingStatsResponse,
)

# 跨域共享 helper 已迁至 routers/common.py（PR0 地基）
from .common import _sse_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["writing"])


# 论文写作工作台（模板 + 项目 CRUD + 章节 CRUD + 字数统计 + 导出）
# ---------------------------------------------------------------------------
@router.get("/api/writing/templates", response_model=list[TemplateResponse])
def list_writing_templates() -> list[TemplateResponse]:
    """列出所有写作模板（预设大纲结构）。"""
    return crud.get_templates()


@router.get("/api/writing/projects", response_model=list[ProjectResponse])
def list_writing_projects(db: Session = Depends(get_db)) -> list[ProjectResponse]:
    """列出所有写作项目。"""
    return crud.get_projects(db)


@router.post("/api/writing/projects", response_model=ProjectResponse, status_code=201)
def create_writing_project(
    payload: ProjectCreate, db: Session = Depends(get_db)
) -> ProjectResponse:
    """新建写作项目。

    - 无 templateId：自动创建「引言」章节。
    - 指定 templateId：按模板结构递归创建完整大纲。
    """
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="项目标题不能为空")
    return crud.create_project(db, payload)


@router.get("/api/writing/projects/{project_id}", response_model=ProjectResponse)
def get_writing_project(project_id: int, db: Session = Depends(get_db)) -> ProjectResponse:
    """获取单个写作项目。"""
    project = crud.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.put("/api/writing/projects/{project_id}", response_model=ProjectResponse)
def update_writing_project(
    project_id: int, payload: ProjectUpdate, db: Session = Depends(get_db)
) -> ProjectResponse:
    """更新写作项目元数据。"""
    project = crud.update_project(db, project_id, payload)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@router.delete("/api/writing/projects/{project_id}")
def delete_writing_project(project_id: int, db: Session = Depends(get_db)) -> dict:
    """删除项目及其所有章节。"""
    if not crud.delete_project(db, project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"success": True}


@router.get(
    "/api/writing/projects/{project_id}/chapters",
    response_model=list[ChapterTreeNode],
)
def list_chapter_tree(project_id: int, db: Session = Depends(get_db)) -> list[ChapterTreeNode]:
    """获取项目章节大纲树（递归结构，按 order 排序）。"""
    if not crud.get_project(db, project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    return crud.get_chapter_tree(db, project_id)


@router.post(
    "/api/writing/projects/{project_id}/chapters",
    response_model=ChapterResponse,
    status_code=201,
)
def create_chapter(
    project_id: int,
    payload: ChapterCreate,
    db: Session = Depends(get_db),
) -> ChapterResponse:
    """新增章节（parentId 指定时挂到对应父章节下，否则作为根章节）。"""
    if not payload.title.strip():
        raise HTTPException(status_code=400, detail="章节标题不能为空")
    try:
        return crud.create_chapter(db, project_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/api/writing/chapters/{chapter_id}", response_model=ChapterResponse)
def update_chapter(
    chapter_id: int, payload: ChapterUpdate, db: Session = Depends(get_db)
) -> ChapterResponse:
    """更新章节（标题 / 内容 / 顺序）。"""
    chapter = crud.update_chapter(db, chapter_id, payload)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


@router.put("/api/writing/chapters/{chapter_id}/move", response_model=ChapterResponse)
def move_chapter(
    chapter_id: int, payload: ChapterMove, db: Session = Depends(get_db)
) -> ChapterResponse:
    """移动 / 拖拽重排章节：更新 parent_id 与 order。"""
    try:
        chapter = crud.move_chapter(db, chapter_id, payload.parentId, payload.order)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return chapter


@router.delete("/api/writing/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, db: Session = Depends(get_db)) -> dict:
    """删除章节及其所有子孙节点。"""
    if not crud.delete_chapter(db, chapter_id):
        raise HTTPException(status_code=404, detail="章节不存在")
    return {"success": True}


@router.post(
    "/api/writing/projects/{project_id}/chapters/{chapter_id}/generate",
    response_model=ChapterGenerateResponse,
)
def generate_chapter_content(
    project_id: int, chapter_id: int, db: Session = Depends(get_db)
) -> ChapterGenerateResponse:
    """AI 生成章节内容：基于大纲标题 + 项目上下文调用 LLM 生成初稿并自动保存。"""
    project = crud.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    chapter = crud.get_chapter(db, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(status_code=404, detail="章节不存在")

    # 构建大纲上下文（缩进标题树）
    outline = crud.build_outline_context(db, project_id)

    factory = get_factory()
    keywords = ", ".join(project.keywords) if project.keywords else "未指定"
    system_prompt = (
        "你是学术写作助手。请根据以下大纲标题与项目信息撰写该章节的内容。"
        "要求：内容专业、结构清晰、使用 Markdown 格式，"
        "可在适当位置使用 [@论文ID] 标记引用相关论文（ID 需来自论文库）。"
        "直接输出章节正文，不要重复章节标题。"
    )
    user_prompt = (
        f"论文标题：{project.title}\n"
        f"关键词：{keywords}\n"
        f"目标期刊：{project.targetJournal or '未指定'}\n\n"
        f"完整大纲：\n{outline}\n\n"
        f"请为「{chapter.title}」这一章节撰写内容。"
    )
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]

    try:
        provider = factory.get_provider()
        result = provider.chat(messages, temperature=0.5, max_tokens=2048)
    except Exception as e:
        raise LLMError(e, factory.current_label()) from e

    generated = result.content.strip()
    # 生成后自动保存到章节
    crud.update_chapter(db, chapter_id, ChapterUpdate(content=generated))

    return ChapterGenerateResponse(
        chapterId=chapter_id,
        content=generated,
        model=factory.current_label(),
    )


@router.post("/api/writing/chapters/{chapter_id}/continue")
def continue_writing_chapter(
    chapter_id: int,
    payload: ContinuationRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """智能续写：流式生成续写文本（SSE）。

    请求体：{ "direction": "接下来讨论实验设置" }
    返回 text/event-stream，每行 data: {"type": "token", "data": "..."}。
    """
    chapter = crud.get_chapter(db, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    direction = payload.direction.strip()
    context = crud.get_chapter_context(db, chapter_id)
    if not context:
        raise HTTPException(status_code=404, detail="章节不存在")

    from .. import writing_assist

    def event_stream():
        try:
            for chunk in writing_assist.continue_writing(context, direction):
                yield _sse_event({"type": "token", "data": chunk})
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except LLMError as e:
            yield _sse_event({"type": "error", "data": str(e)})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/writing/generate-outline")
def generate_outline(
    payload: GenerateOutlineRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """P1：自动生成大纲 —— 基于主题和关键词，流式生成多层级学术论文大纲。

    请求体：{ "projectId": 1, "topic": "...", "keywords": ["..."] }
    返回 text/event-stream：
        data: {"type": "chunk", "content": "..."} — 生成增量
        data: {"type": "done", "outline": [...]} — 完成，返回大纲
        data: {"type": "error", "message": "..."} — 错误

    LLM 不可用 → HTTP 503；JSON 解析失败 → SSE error 事件。
    """
    # 校验目标项目存在
    project = crud.get_project(db, payload.projectId)
    if not project:
        raise HTTPException(status_code=404, detail="目标项目不存在")

    from .. import writing_assist

    # LLM 可用性预检：不可用直接返回 503（流式响应已发送后无法改状态码）
    try:
        writing_assist.get_factory().get_provider()
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="LLM 服务不可用，请先在「模型管理」中配置并启用模型。",
        ) from None

    def event_stream():
        try:
            for event in writing_assist.generate_outline_stream(payload.topic, payload.keywords):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except LLMError as e:
            error_payload = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/writing/chapters/{chapter_id}/suggest-structure")
def suggest_chapter_structure(
    chapter_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """结构建议：分析当前章节内容与大纲，返回结构化调整建议（JSON）。

    返回 { "suggestion", "target_chapter", "target_chapter_id", "reason", "confidence" }。
    C3：在原返回基础上补充 target_chapter_id（按名称在项目大纲中查找）。
    """
    context = crud.get_chapter_context(db, chapter_id)
    if not context:
        raise HTTPException(status_code=404, detail="章节不存在")

    from .. import writing_assist

    try:
        result = writing_assist.suggest_structure(context)
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    # C3：按 target_chapter 名称查找对应章节 ID，便于前端「一键采纳」
    target_name = result.get("target_chapter", "")
    target_id = None
    if target_name:
        project_id = context["project_id"]
        match = (
            db.query(ChapterORM)
            .filter(
                ChapterORM.project_id == project_id,
                ChapterORM.title == target_name,
                ChapterORM.id != chapter_id,
            )
            .first()
        )
        if match:
            target_id = match.id
    result["target_chapter_id"] = target_id
    return result


@router.post("/api/writing/chapters/{chapter_id}/rewrite")
def rewrite_chapter_text(
    chapter_id: int,
    payload: RewriteRequest,
    db: Session = Depends(get_db),
) -> dict:
    """C1：全文改写 —— 基于章节上下文润色选中的文本片段（非流式）。

    请求体：{ "text": "选中的原始文本" }
    返回：{ "rewritten": "改写后的文本" }
    LLM 不可用时返回 503 降级提示。
    """
    chapter = crud.get_chapter(db, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    text = payload.text.strip()

    context = crud.get_chapter_context(db, chapter_id)
    if not context:
        raise HTTPException(status_code=404, detail="章节不存在")

    from .. import writing_assist

    try:
        rewritten = writing_assist.rewrite_text(context, text)
    except LLMError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    # 学术诚信 provenance：改写由 AI 生成，服务端自动盖戳 "rewrite"
    crud.create_continuation(db, chapter_id, rewritten, direction="", kind="rewrite")

    return {"rewritten": rewritten}


@router.get("/api/writing/chapters/{chapter_id}/continuations")
def list_chapter_continuations(chapter_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """C2：获取章节的续写历史列表（按时间倒序，最多 10 条）。"""
    return crud.list_continuations(db, chapter_id)


@router.post("/api/writing/chapters/{chapter_id}/continuations")
def save_chapter_continuation(
    chapter_id: int,
    payload: SaveContinuationRequest,
    db: Session = Depends(get_db),
) -> dict:
    """C2：保存一条续写历史记录（续写成功后由前端自动调用）。

    请求体：{ "content": "续写生成的内容", "direction": "续写方向（可空）" }
    provenance 类型（kind）由服务端固定为 "continue"，防止客户端伪造。
    """
    content = payload.content.strip()
    direction = payload.direction.strip()
    rec = crud.create_continuation(db, chapter_id, content, direction, kind="continue")
    if rec is None:
        raise HTTPException(status_code=404, detail="章节不存在")
    return rec


@router.get("/api/writing/chapters/{chapter_id}/validate-citations")
def validate_chapter_citations_route(chapter_id: int, db: Session = Depends(get_db)) -> dict:
    """学术诚信护栏 #2：校验章节正文 [@paper_id] 引用是否命中真实论文库。

    返回疑似伪造（库中不存在）的 id 列表，供前端在 AI 生成后/导出前高亮提示。
    """
    chapter = crud.get_chapter(db, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return crud.validate_chapter_citations(db, chapter_id)


@router.post("/api/writing/chapters/{chapter_id}/validate-citations")
def validate_chapter_citations_text_route(
    chapter_id: int, payload: ValidateCitationsRequest, db: Session = Depends(get_db)
) -> dict:
    """学术诚信护栏 #2：校验传入文本（而非服务端落库内容）的引用真实性。

    用于 AI 生成（续写/改写）完成后即时拦截伪造引用——此时文本可能尚未保存，
    直接传前端实时内容即可精准校验。
    """
    chapter = crud.get_chapter(db, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return crud.validate_chapter_citations(db, chapter_id, content=payload.content)


@router.get("/api/writing/projects/{project_id}/ai-usage")
def get_project_ai_usage_route(project_id: int, db: Session = Depends(get_db)) -> dict:
    """学术诚信护栏 #1：返回项目 AI 辅助使用量，供导出前预览「AI 使用声明」。"""
    project = crud.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return crud.get_project_ai_usage(db, project_id)


@router.post("/api/writing/chapters/{chapter_id}/apply-suggestion")
def apply_structure_suggestion(
    chapter_id: int,
    payload: AdoptStructureRequest,
    db: Session = Depends(get_db),
) -> dict:
    """C3：采纳结构建议 —— 将当前章节移动到目标章节下（作为最后一个子节点）。

    请求体：{ "target_chapter_id": 123 }
    返回更新后的章节信息。目标章节是当前章节子孙时返回 400。
    """
    target_id = payload.target_chapter_id

    chapter = crud.get_chapter(db, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    target = db.query(ChapterORM).filter(ChapterORM.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="目标章节不存在")
    if target.project_id != chapter.project_id:
        raise HTTPException(status_code=400, detail="目标章节不属于当前项目")
    if target_id == chapter_id:
        raise HTTPException(status_code=400, detail="不能将章节移动到自身下")

    # 计算目标章节下现有子节点数，作为新插入的 index（追加为最后一个子节点）
    existing_children = db.query(ChapterORM).filter(ChapterORM.parent_id == target_id).count()
    try:
        updated = crud.move_chapter(db, chapter_id, target_id, existing_children)
    except ValueError as e:
        # move_chapter 已做环检测，此处兜底转 400
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not updated:
        raise HTTPException(status_code=500, detail="移动失败")
    return {
        "chapter": updated,
        "message": f"章节已移动到「{target.title}」下",
    }


@router.post(
    "/api/writing/chapters/{chapter_id}/recommend-citations",
    response_model=RecommendCitationsResponse,
)
def recommend_citations(
    chapter_id: int,
    payload: RecommendCitationsRequest,
    db: Session = Depends(get_db),
) -> RecommendCitationsResponse:
    """引用智能推荐：基于章节内容从论文库检索相关论文。

    检索逻辑（crud.recommend_papers_by_content）：
    1. 从内容中提取关键词（停用词过滤 + 频率排序）
    2. FTS5 全文检索（关键词 OR 组合，Top 20）
    3. 向量语义检索（内容末 500 字符编码，Top 20）
    4. RRF 融合两路结果，返回 Top 10

    降级策略：向量检索不可用时仅使用 FTS5 关键词检索。

    返回 {"papers": [{id, title, authors, year, score}, ...]}，按 score 降序。
    """
    # 校验章节存在
    chapter = crud.get_chapter(db, chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")

    content = (payload.content or "").strip()
    if not content:
        return RecommendCitationsResponse(papers=[])

    papers = crud.recommend_papers_by_content(db, content, top_k=10)
    return RecommendCitationsResponse(papers=[RecommendedPaper(**p) for p in papers])


@router.post(
    "/api/writing/projects/{project_id}/export",
    response_model=ExportTaskCreateResponse,
    status_code=202,
)
def export_writing_project(project_id: int) -> ExportTaskCreateResponse:
    """启动异步导出任务，立即返回 task_id。

    客户端轮询 GET /api/writing/export/{task_id}/progress 获取进度，
    status=done 后从响应的 content/filename/references 字段获取导出结果。
    """
    task_id = start_export_task(project_id)
    return ExportTaskCreateResponse(taskId=task_id, status="pending")


@router.get("/api/writing/projects/{project_id}/export/csl")
def export_writing_project_csl(project_id: int, db: Session = Depends(get_db)) -> list[dict]:
    """Export project references as CSL-JSON for citation rendering."""

    result = crud.writing.export_project_csl(db, project_id)

    if result is None:
        raise HTTPException(status_code=404, detail=f"项目 {project_id} 不存在")

    return result


@router.get(
    "/api/writing/export/{task_id}/progress",
    response_model=ExportTaskProgressResponse,
)
def get_export_progress(task_id: str) -> ExportTaskProgressResponse:
    """查询导出任务进度。任务不存在返回 404。"""
    task = get_export_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="导出任务不存在或已过期")
    return ExportTaskProgressResponse(
        taskId=task.task_id,
        progress=task.progress,
        status=task.status,
        content=task.content if task.status == "done" else "",
        filename=task.filename if task.status == "done" else "",
        references=[ExportReference(**r) for r in task.references] if task.status == "done" else [],
        error=task.error,
        # P2-2: 分块进度字段
        currentChapter=task.current_chapter,
        totalChapters=task.total_chapters,
    )


@router.post("/api/writing/projects/{project_id}/export-folder")
def export_project_to_folder_route(
    project_id: int,
    payload: FolderExportRequest,
    db: Session = Depends(get_db),
) -> dict:
    """将写作项目导出为本地工程文件夹。

    请求体：
        {
            "outputDir": "/path/to/output",
            "includeVscode": true,
            "includeObsidian": false
        }

    生成结构：
        outputDir/
        ├── main.md
        ├── refs.bib
        ├── images/（预留）
        ├── .vscode/settings.json（可选）
        └── .obsidian/app.json（可选）
    """
    output_dir = payload.outputDir.strip()
    # 安全护栏：导出目录必须位于 DATA_DIR/exports 沙箱内（与 crud 层双重校验）
    _exports_base = DATA_DIR / "exports"
    _cand = Path(output_dir)
    _out = _cand.resolve() if _cand.is_absolute() else (_exports_base / _cand).resolve()
    if not _out.is_relative_to(_exports_base):
        raise HTTPException(
            status_code=400, detail="导出目录必须位于数据目录的 exports/ 下，禁止路径穿越"
        )
    include_vscode = payload.includeVscode
    include_obsidian = payload.includeObsidian

    # 校验项目存在
    project = crud.get_project(db, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        result = crud.export_project_to_folder(
            db,
            project_id,
            output_dir,
            include_vscode=include_vscode,
            include_obsidian=include_obsidian,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"写入文件失败: {e}") from e


@router.get(
    "/api/writing/projects/{project_id}/word-count",
    response_model=WordCountResponse,
)
def get_project_word_count(project_id: int, db: Session = Depends(get_db)) -> WordCountResponse:
    """统计项目总字数与各章节字数（排除 Markdown 标记）。

    同时记录今日字数快照（用于统计看板趋势图）。
    """
    result = crud.get_word_count(db, project_id)
    if result is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    # 记录每日字数快照
    crud.record_daily_snapshot(db, project_id, result.total)
    return result


# ---------------------------------------------------------------------------
# 用户自定义模板
# ---------------------------------------------------------------------------
@router.get("/api/writing/templates/user", response_model=list[UserTemplateResponse])
def list_user_templates(db: Session = Depends(get_db)) -> list[UserTemplateResponse]:
    """列出所有用户自定义模板。"""
    return crud.get_user_templates(db)


@router.post(
    "/api/writing/templates/user",
    response_model=UserTemplateResponse,
    status_code=201,
)
def create_user_template_api(
    payload: UserTemplateCreate, db: Session = Depends(get_db)
) -> UserTemplateResponse:
    """新建用户自定义模板。"""
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="模板名称不能为空")
    return crud.create_user_template(db, payload)


@router.post(
    "/api/writing/projects/{project_id}/save-as-template",
    response_model=UserTemplateResponse,
)
def save_project_as_template_api(
    project_id: int,
    payload: UserTemplateCreate,
    db: Session = Depends(get_db),
) -> UserTemplateResponse:
    """将当前项目的大纲结构保存为自定义模板。"""
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="模板名称不能为空")
    result = crud.save_project_as_template(db, project_id, payload.name, payload.description)
    if result is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return result


@router.delete("/api/writing/templates/user/{template_id}")
def delete_user_template_api(template_id: str, db: Session = Depends(get_db)) -> dict:
    """删除用户自定义模板。"""
    if not crud.delete_user_template(db, template_id):
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"success": True}


# ---------------------------------------------------------------------------
# 写作统计看板
# ---------------------------------------------------------------------------
@router.get("/api/writing/stats", response_model=WritingStatsResponse)
def get_writing_stats_api(db: Session = Depends(get_db)) -> WritingStatsResponse:
    """获取写作统计：总项目数、总字数、今日新增、近 7 天趋势。"""
    return crud.get_writing_stats(db)


# ---------------------------------------------------------------------------
# 章节历史版本
# ---------------------------------------------------------------------------
@router.get(
    "/api/writing/chapters/{chapter_id}/versions",
    response_model=list[ChapterVersionResponse],
)
def list_chapter_versions(
    chapter_id: int, db: Session = Depends(get_db)
) -> list[ChapterVersionResponse]:
    """获取章节历史版本列表（按时间倒序，最多 10 个）。"""
    return crud.get_chapter_versions(db, chapter_id)


@router.get(
    "/api/writing/chapters/{chapter_id}/versions/{version_id}",
    response_model=ChapterVersionDetail,
)
def get_chapter_version_api(
    chapter_id: int, version_id: str, db: Session = Depends(get_db)
) -> ChapterVersionDetail:
    """获取某个历史版本的完整内容。"""
    detail = crud.get_chapter_version(db, version_id)
    if detail is None or detail.chapterId != chapter_id:
        raise HTTPException(status_code=404, detail="版本不存在")
    return detail


@router.post(
    "/api/writing/chapters/{chapter_id}/restore/{version_id}",
    response_model=ChapterRestoreResponse,
)
def restore_chapter_version_api(
    chapter_id: int, version_id: str, db: Session = Depends(get_db)
) -> ChapterRestoreResponse:
    """恢复章节到指定历史版本（恢复前自动保存当前内容为新版本）。"""
    result = crud.restore_chapter_version(db, chapter_id, version_id)
    if result is None:
        raise HTTPException(status_code=404, detail="版本或章节不存在")
    return result


# ---------------------------------------------------------------------------
