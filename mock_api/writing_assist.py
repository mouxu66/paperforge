"""写作辅助模块 —— 智能续写 + 结构建议 + 全文改写。

复用 crud.get_chapter_context 获取章节上下文，构建 Prompt 调用 LLM 网关：
1. continue_writing：基于当前章节内容 + 用户续写方向，流式生成续写文本。
2. suggest_structure：分析当前章节内容与项目大纲，返回结构化调整建议（JSON）。
3. rewrite_text：基于章节上下文润色/改写选中的文本片段（非流式）。

降级处理：LLM 不可用（未配置模型）时抛出 LLMError，路由层捕获后返回友好提示。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from .llm import ChatMessage, LLMError, classify_llm_error, get_factory

logger = logging.getLogger(__name__)


def continue_writing(
    context: dict,
    direction: str,
) -> Iterator[str]:
    """智能续写：流式生成续写文本。

    Args:
        context: crud.get_chapter_context 返回的上下文字典。
        direction: 用户输入的续写方向（如「接下来讨论实验设置」）。

    Yields:
        续写文本片段（逐 token 流式输出）。

    Raises:
        LLMError: LLM 调用失败（未配置/网络错误等）。
    """
    title = context.get("title", "")
    content = context.get("content", "")
    # 取最近 1000 字符作为上下文，避免 token 超限
    recent = content[-1000:] if len(content) > 1000 else content

    system_prompt = (
        "你是学术写作助手。请根据当前章节的已有内容和用户的续写方向，"
        "自然地续写下去。要求：\n"
        "1. 保持与已有内容的风格和语气一致\n"
        "2. 使用 Markdown 格式\n"
        "3. 可在适当位置使用 [@论文ID] 标记引用\n"
        "4. 直接输出续写内容，不要重复已有内容，不要添加解释性前缀"
    )
    user_prompt = (
        f"章节标题：{title}\n\n"
        f"已有内容（末尾部分）：\n{recent}\n\n"
        f"续写方向：{direction or '请自然延续当前内容'}\n\n"
        "请续写："
    )
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]

    factory = get_factory()
    try:
        provider = factory.get_provider()
    except Exception as e:  # noqa: BLE001 - LLM 流式输出 - chunk 异常应中断而不是崩溃
        raise LLMError(e, factory.current_label()) from e

    current_label = factory.current_label()
    try:
        for chunk in provider.chat_stream(messages, temperature=0.5, max_tokens=1024):
            yield chunk
    except Exception as e:  # noqa: BLE001 - LLM 流式输出 - chunk 异常应中断而不是崩溃
        # 流式中途出错：抛出友好错误，由路由层处理
        raise LLMError(e, current_label) from e


def suggest_structure(context: dict) -> dict:
    """结构建议：分析当前章节内容与大纲，返回结构化调整建议。

    Args:
        context: crud.get_chapter_context 返回的上下文字典。

    Returns:
        {
            "suggestion": "建议移动到「实验设计」章节下",
            "target_chapter": "实验设计",
            "reason": "当前内容主要讨论实验配置，与实验设计章节主题更匹配...",
            "confidence": 0.85
        }

    Raises:
        LLMError: LLM 调用失败。
    """
    title = context.get("title", "")
    content = context.get("content", "")
    outline = context.get("outline", "")

    system_prompt = (
        "你是学术写作的结构分析助手。请分析当前章节的内容，判断它更适合放在"
        "项目大纲的哪个位置，并给出调整建议。\n"
        "返回严格的 JSON 格式（不要 Markdown 代码块标记），包含以下字段：\n"
        '{"suggestion": "简短建议描述", '
        '"target_chapter": "目标章节名称（从大纲中选取，若无更合适位置则为当前章节名）", '
        '"reason": "详细理由（50-150字）", '
        '"confidence": 0.0到1.0之间的置信度}'
    )
    user_prompt = (
        f"当前章节标题：{title}\n\n"
        f"当前章节内容：\n{content}\n\n"
        f"项目完整大纲：\n{outline}\n\n"
        "请分析当前内容最适合放在哪个章节下，返回 JSON："
    )
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]

    factory = get_factory()
    try:
        provider = factory.get_provider()
    except Exception as e:  # noqa: BLE001 - LLM 流式输出 - chunk 异常应中断而不是崩溃
        raise LLMError(e, factory.current_label()) from e

    current_label = factory.current_label()
    try:
        result = provider.chat(messages, temperature=0.3, max_tokens=512)
    except Exception as e:  # noqa: BLE001 - LLM 流式输出 - chunk 异常应中断而不是崩溃
        raise LLMError(e, current_label) from e

    # 解析 LLM 返回的 JSON（防御性处理：去除可能的 Markdown 代码块标记）
    raw = result.content.strip()
    # 去除 ```json ... ``` 包裹
    if raw.startswith("```"):
        lines = raw.split("\n")
        # 去掉首尾的 ``` 行
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        parsed = json.loads(raw)
        # 确保必要字段存在
        return {
            "suggestion": str(parsed.get("suggestion", "")),
            "target_chapter": str(parsed.get("target_chapter", "")),
            "reason": str(parsed.get("reason", "")),
            "confidence": float(parsed.get("confidence", 0.5)),
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        # JSON 解析失败：返回原始文本作为建议
        return {
            "suggestion": raw[:200] if raw else "无法解析建议",
            "target_chapter": "",
            "reason": "LLM 返回格式异常，请重试",
            "confidence": 0.0,
        }


def rewrite_text(context: dict, text: str) -> str:
    """全文改写：基于章节上下文润色/改写选中的文本片段。

    Args:
        context: crud.get_chapter_context 返回的上下文字典。
        text: 用户选中的原始文本。

    Returns:
        改写后的文本（不含任何额外解释）。

    Raises:
        LLMError: LLM 调用失败。
    """
    title = context.get("title", "")
    content = context.get("content", "")

    system_prompt = (
        "你是学术写作润色助手。请根据上下文逻辑，润色/改写选中的文本，"
        "保持学术风格，不改变原意。\n"
        "要求：\n"
        "1. 仅返回改写后的文本，不要包含任何额外解释或前缀\n"
        "2. 保持原文的 Markdown 格式（若有）\n"
        "3. 保留原文中的 [@论文ID] 引用标记\n"
        "4. 改写后文本长度应与原文相近"
    )
    user_prompt = (
        f"章节标题：{title}\n\n"
        f"当前章节完整内容（作为上下文）：\n{content}\n\n"
        f"需要改写的选中文本：\n{text}\n\n"
        "请返回改写后的文本："
    )
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]

    factory = get_factory()
    try:
        provider = factory.get_provider()
    except Exception as e:  # noqa: BLE001 - LLM 流式输出 - chunk 异常应中断而不是崩溃
        raise LLMError(e, factory.current_label()) from e

    current_label = factory.current_label()
    try:
        result = provider.chat(messages, temperature=0.4, max_tokens=1024)
    except Exception as e:  # noqa: BLE001 - LLM 流式输出 - chunk 异常应中断而不是崩溃
        raise LLMError(e, current_label) from e

    rewritten = result.content.strip()
    # 防御性：去除可能的 Markdown 代码块包裹
    if rewritten.startswith("```"):
        lines = rewritten.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        rewritten = "\n".join(lines).strip()
    return rewritten


def generate_outline_stream(
    topic: str,
    keywords: list[str],
) -> Iterator[dict]:
    """自动生成大纲：流式生成增量内容，最终返回解析后的多层级大纲。

    P1：基于用户输入的主题和关键词，调用 LLM 生成完整的学术论文大纲。

    Args:
        topic: 论文主题（如「低秩适应方法在LLM微调中的应用」）。
        keywords: 关键词列表（如 ["LoRA", "QLoRA"]）。

    Yields:
        {"type": "chunk", "content": "..."} — 生成过程中的增量内容片段
        {"type": "done", "outline": [...]} — 生成完成，返回解析后的完整大纲
        {"type": "error", "message": "..."} — 错误信息（如 JSON 解析失败）

    Raises:
        LLMError: LLM 不可用（未配置模型）。路由层捕获后返回 503。

    大纲结构（OutlineNode）：
        [{"title": "引言", "children": [{"title": "研究背景", "children": []}]}, ...]
    """
    kw_text = "、".join(keywords) if keywords else "无"
    system_prompt = (
        "你是一位学术论文写作专家。请根据以下主题和关键词，生成一份完整的学术论文大纲。\n"
        "要求：\n"
        "1. 大纲应包含引言、相关工作、方法、实验、结论等标准学术章节。\n"
        "2. 每个章节包含标题和层级深度（1-3级），3级子章节可适当展开。\n"
        "3. 输出格式为 JSON 数组，每个元素包含 title 和 children（子章节数组）。\n"
        "4. 只输出 JSON，不要包含任何额外解释或 Markdown 代码块标记。"
    )
    user_prompt = (
        f"主题：{topic}\n"
        f"关键词：{kw_text}\n\n"
        "请生成大纲 JSON 数组，格式示例：\n"
        "[\n"
        '  {"title": "引言", "children": [\n'
        '    {"title": "研究背景", "children": []},\n'
        '    {"title": "研究动机", "children": []}\n'
        "  ]},\n"
        '  {"title": "相关工作", "children": []}\n'
        "]"
    )
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]

    factory = get_factory()
    try:
        provider = factory.get_provider()
    except Exception as e:  # noqa: BLE001 - LLM 流式输出 - chunk 异常应中断而不是崩溃
        raise LLMError(e, factory.current_label()) from e

    current_label = factory.current_label()
    accumulated: list[str] = []
    try:
        for chunk in provider.chat_stream(messages, temperature=0.4, max_tokens=2048):
            accumulated.append(chunk)
            yield {"type": "chunk", "content": chunk}
    except Exception as e:  # noqa: BLE001 - LLM 流式输出 - chunk 异常应中断而不是崩溃
        friendly = classify_llm_error(e, current_label)
        yield {"type": "error", "message": friendly}
        return

    raw = "".join(accumulated).strip()
    # 防御性：剥离可能的 ```json ... ``` 代码块包裹
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    # 尝试定位最外层的 JSON 数组（LLM 可能在 JSON 前后加多余文字）
    if not raw.startswith("["):
        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            raw = raw[start : end + 1]

    try:
        outline = json.loads(raw)
        if not isinstance(outline, list):
            raise ValueError("大纲应为 JSON 数组")
        # 规范化每个节点：确保 title 为字符串、children 为列表
        normalized = _normalize_outline_nodes(outline)
        yield {"type": "done", "outline": normalized}
    except (json.JSONDecodeError, TypeError, ValueError):
        yield {
            "type": "error",
            "message": "LLM 返回的大纲格式无法解析为 JSON，请重试或调整主题描述。",
        }


def _normalize_outline_nodes(nodes: list) -> list[dict]:
    """递归规范化大纲节点：确保 title 为字符串、children 为列表。

    防御 LLM 返回的结构不规范（如缺少 children、title 为非字符串）。
    """
    result: list[dict] = []
    for node in nodes:
        if isinstance(node, dict):
            title = str(node.get("title", "")).strip()
            if not title:
                continue
            children = node.get("children", []) or []
            result.append(
                {
                    "title": title,
                    "children": _normalize_outline_nodes(children)
                    if isinstance(children, list)
                    else [],
                }
            )
    return result
