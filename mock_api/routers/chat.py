"""AI 问答/生成/语义搜索（统一走 LLM 网关）。

PR5 抽取：从 main.py 搬迁 chat 路由 + 2 个 RAG helper。
依赖 common._sse_event（PR0 地基）提供 SSE 事件序列化。
"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import crud
from ..database import get_db
from ..llm import ChatMessage, ChatResult, LLMError, classify_llm_error, get_factory
from ..schemas import AskRequest, AskResponse, GenerateRequest, GenerateResponse, Paper
from .common import _sse_event

router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------------------
# RAG 上下文构建（chat 域专用 helper）
# ---------------------------------------------------------------------------
def _build_rag_context(papers: list[Paper]) -> str:
    """将检索到的论文拼成 LLM 上下文。"""
    if not papers:
        return "（未检索到相关论文）"
    parts = []
    for i, p in enumerate(papers, 1):
        parts.append(
            f"[{i}] {p.title} ({p.year})\n作者: {', '.join(p.authors)}\n摘要: {p.abstract[:400]}"
        )
    return "\n\n".join(parts)


def _build_ask_messages(question: str, context: str) -> list[ChatMessage]:
    """构造问答 LLM 消息（ask_paper / ask_stream 共用，统一系统提示）。"""
    return [
        ChatMessage(
            role="system",
            content=(
                "你是学术研究助手。基于以下论文片段回答用户问题，"
                "回答末尾用 [1][2] 形式标注引用。若论文片段不足以回答，请如实说明。\n\n"
                f"论文片段：\n{context}"
            ),
        ),
        ChatMessage(role="user", content=question),
    ]


# ---------------------------------------------------------------------------
# 问答 / 生成 / 语义搜索
# ---------------------------------------------------------------------------
@router.post("/api/ask", response_model=AskResponse)
def ask_paper(req: AskRequest, db: Session = Depends(get_db)) -> AskResponse:
    """论文问答（RAG 检索 + LLM 生成答案）。"""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")

    # 1. RAG 检索
    papers = crud.retrieve_context(db, req.question, req.paperIds, top_k=3)
    context = _build_rag_context(papers)
    references = [
        {"id": p.id, "title": p.title, "year": p.year, "authors": p.authors} for p in papers
    ]

    # 2. 调用 LLM（get_provider 会抛出 RuntimeError：未配置任何模型）
    factory = get_factory()
    messages = _build_ask_messages(req.question, context)
    try:
        provider = factory.get_provider()
        result = provider.chat(messages, temperature=0.3)
    except Exception as e:
        raise LLMError(e, factory.current_label()) from e

    return AskResponse(answer=result.content, references=references, model=factory.current_label())


@router.post("/api/ask/stream")
def ask_stream(req: AskRequest, db: Session = Depends(get_db)) -> StreamingResponse:
    """流式问答（SSE 打字机效果）。"""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")

    papers = crud.retrieve_context(db, req.question, req.paperIds, top_k=3)
    context = _build_rag_context(papers)
    factory = get_factory()
    messages = _build_ask_messages(req.question, context)

    # get_provider 会抛出 RuntimeError（未配置任何模型 → 直接返回 502 友好提示）
    try:
        provider = factory.get_provider()
    except Exception as e:
        raise LLMError(e, factory.current_label()) from e

    def event_stream():
        refs = json.dumps([{"id": p.id, "title": p.title} for p in papers], ensure_ascii=False)
        yield _sse_event({"type": "refs", "data": refs})
        current_label = factory.current_label()
        try:
            for chunk in provider.chat_stream(messages, temperature=0.3):
                yield _sse_event({"type": "token", "data": chunk})
        except Exception as e:
            friendly = classify_llm_error(e, current_label)
            yield _sse_event({"type": "error", "data": friendly})
            return
        yield _sse_event({"type": "done", "data": current_label})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/api/generate", response_model=GenerateResponse)
def generate_draft(req: GenerateRequest, db: Session = Depends(get_db)) -> GenerateResponse:
    """综述生成：根据主题和参考论文，由 LLM 生成 Markdown 草稿。"""
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="生成主题不能为空")

    papers = crud.retrieve_context(db, req.topic, req.paperIds, top_k=5)
    context = _build_rag_context(papers)
    factory = get_factory()
    messages = [
        ChatMessage(
            role="system",
            content=(
                "你是学术综述写作助手。根据用户提供的主题和参考文献，"
                "撰写一篇结构化的学术综述（Markdown 格式）。包含以下部分："
                "标题、引言、方法综述、实验对比、挑战与展望、参考文献。"
                "引用处用 [1][2] 标注。\n\n"
                f"参考文献：\n{context}"
            ),
        ),
        ChatMessage(role="user", content=f"请综述以下主题：{req.topic}"),
    ]

    try:
        provider = factory.get_provider()
        result = provider.chat(messages, temperature=0.5, max_tokens=2048)
    except Exception as e:
        raise LLMError(e, factory.current_label()) from e

    references = [
        {"id": p.id, "title": p.title, "year": p.year, "authors": p.authors} for p in papers
    ]
    return GenerateResponse(
        content=result.content,
        references=references,
        model=factory.current_label(),
    )


@router.post("/api/search/semantic", response_model=list[Paper])
def semantic_search(req: AskRequest, db: Session = Depends(get_db)) -> list[Paper]:
    """语义搜索（混合检索：FTS5 + 向量 + RRF 融合）。

    流程：
    1. 混合检索（crud.hybrid_search_papers）：
       - FTS5 关键词检索（title/abstract/authors），取 Top 20；
       - 若 fastembed 向量库非空，以查询文本为向量 → 余弦相似度 Top 20；
       - RRF（k=60）融合两路结果 → Top 10。
    2. 若混合检索有结果，直接返回。
    3. 若无结果（如 FTS5 无命中），则走 LLM 关键词扩展 + 关键词检索。
    """
    if not req.question.strip():
        return []

    # 1. 混合检索（fastembed 未装/未就绪时自动退化为纯 FTS5）
    results = crud.hybrid_search_papers(db, req.question, top_k=10, paper_ids=req.paperIds)
    if results:
        # 过滤掉感悟报告，排除在论文搜索结果外
        return [r for r in results if r.category != "report"]

    # 2. 降级：LLM 关键词扩展 + FTS5/关键词检索
    #    适用于混合检索零结果时（如自然语言查询与 FTS5 词库不匹配）
    factory = get_factory()
    messages = [
        ChatMessage(
            role="system",
            content=(
                "你是学术搜索助手。将用户的自然语言查询扩展为 3-5 个英文关键词，"
                "用空格分隔，只输出关键词，不要解释。"
            ),
        ),
        ChatMessage(role="user", content=req.question),
    ]
    try:
        provider = factory.get_provider()
        llm_result = provider.chat(messages, temperature=0.0)
    except Exception:
        # LLM 未配置/调用失败时，降级为原始查询
        llm_result = ChatResult(content=req.question, model="", provider="")

    # 用扩展后的关键词在库内检索，取前 10 篇
    keywords = [k.strip() for k in re.split(r"[\s,，]+", llm_result.content) if k.strip()]
    seen: dict[str, Paper] = {}
    for kw in keywords[:5]:
        for p in crud.retrieve_context(db, kw, req.paperIds, top_k=10):
            if p.id not in seen and p.category != "report":
                seen[p.id] = p
    return list(seen.values())[:10]
