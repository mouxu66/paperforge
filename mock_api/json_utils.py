"""PaperForge 统一 JSON 安全解析工具。

合并自 depth_eval.py 与 depth_eval_reflection.py 两处独立实现：
- 基础：Markdown 代码块剥离、花括号/方括号提取、strict=False 兜底
- 增强：全角引号归一化（U+201C/D 等）、裸控制字符转义状态机、
  内嵌 ASCII 双引号转义、超长输入智能截断、pos+context 诊断日志

所有模块统一从此导入，避免两份 safe_json_parse 实现漂移。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 内嵌 ASCII 双引号转义（修复 LLM 输出的中文嵌套引号 JSON 风暴）
#   - 仅转义被非空白/非JSON结构字符夹住的裸双引号
#   - lookbehind: 不能跟在空白、花括号、方括号、逗号、冒号、引号、反斜杠后
#   - lookahead:  不能在这些字符前面
# ---------------------------------------------------------------------------
_INNER_QUOTE_RE = re.compile(r'(?<![\s{}\[\],:"\\])(?<=.)"(?=.)(?![\s{}\[\],:"\\])')
_INNER_QUOTE_PAIR_RE = re.compile(r'(?<![\s{}\[\],:"\\])""(?![\s{}\[\],:"\\])')


def _escape_control_chars_in_strings(s: str) -> str:
    """状态机：仅转义 JSON 字符串值内的裸控制字符。

    已转义序列（\\n）与字符串外部的控制字符原样保留。
    对 \\n / \\r / \\t / \\b / \\f 使用短转义，其他控制字符走 \\uXXXX。
    """
    out: list[str] = []
    i = 0
    n = len(s)
    in_string = False
    while i < n:
        ch = s[i]
        if in_string:
            if ch == "\\":
                if i + 1 < n:
                    out.append(ch)
                    out.append(s[i + 1])
                    i += 2
                    continue
                out.append(ch)
                i += 1
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
                i += 1
                continue
            code = ord(ch)
            if code < 0x20:
                if ch == "\n":
                    out.append("\\n")
                elif ch == "\r":
                    out.append("\\r")
                elif ch == "\t":
                    out.append("\\t")
                elif ch == "\b":
                    out.append("\\b")
                elif ch == "\f":
                    out.append("\\f")
                else:
                    out.append(f"\\u{code:04x}")
                i += 1
                continue
            out.append(ch)
            i += 1
        else:
            if ch == '"':
                in_string = True
                out.append(ch)
                i += 1
                continue
            out.append(ch)
            i += 1
    return "".join(out)


def safe_json_parse(text: str, logger_instance: logging.Logger | None = None) -> dict[str, Any]:
    """从 LLM 输出中安全提取 JSON 对象。

    处理：Markdown 代码块、全角引号归一化、裸控制字符转义、
    内嵌中文双引号修复、超长输入截断、多次兜底解析。

    Args:
        text: LLM 原始输出字符串。
        logger_instance: 可选外部 logger，默认使用本模块 logger。

    Returns:
        解析成功的 dict；彻底失败时返回 {}（不抛异常）。
    """
    log = logger_instance or logger
    if not text:
        return {}

    def _log_decode_error(staged_text: str, e: ValueError) -> None:
        pos = max(0, getattr(e, "pos", 0))
        ctx_start = max(0, pos - 15)
        ctx_end = min(len(staged_text), pos + 15)
        ctx = staged_text[ctx_start:ctx_end]
        log.warning(
            "DEPTH safe_json_parse JSONDecodeError @pos=%d/%d: %s | context=%r",
            pos,
            len(staged_text),
            getattr(e, "msg", str(e)),
            ctx,
        )

    MAX_PARSE_CHARS = 120_000
    if len(text) > MAX_PARSE_CHARS:
        log.warning(
            "DEPTH safe_json_parse 输入超长 (%d chars) → 智能截断到 <= %d",
            len(text),
            MAX_PARSE_CHARS,
        )
        first_brace = text.find("{")
        if first_brace > 0:
            text = text[first_brace:]
        if len(text) > MAX_PARSE_CHARS:
            head = text[:MAX_PARSE_CHARS]
            last_brace = head.rfind("}")
            if last_brace != -1:
                text = head[: last_brace + 1]
            else:
                half = MAX_PARSE_CHARS // 2
                text = text[:half] + "\n[...已截断...]\n" + text[-half:]
    s = text.strip()

    # 全角引号归一化
    _QUOTE_PAIRS = (
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\uff02", '"'),
        ("\uff07", "'"),
    )
    for src, dst in _QUOTE_PAIRS:
        s = s.replace(src, dst)

    # Markdown 代码块剥离
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        if s.endswith("```"):
            s = s[:-3].strip()

    # 控制字符转义
    s = _escape_control_chars_in_strings(s)

    # 内嵌 ASCII 双引号转义
    s, n_inner_quotes = _INNER_QUOTE_RE.subn('\\"', s)
    if n_inner_quotes > 0:
        log.info("DEPTH safe_json_parse 内嵌 ASCII 双引号转义: %d 处", n_inner_quotes)

    s, n_pair_quotes = _INNER_QUOTE_PAIR_RE.subn('\\"\\"', s)
    if n_pair_quotes > 0:
        log.info("DEPTH safe_json_parse 连续裸双引号转义: %d 处", n_pair_quotes)

    # 直接解析
    try:
        return json.loads(s)
    except (ValueError, json.JSONDecodeError) as e:
        _log_decode_error(s, e)

    # 提取 { ... }
    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except (ValueError, json.JSONDecodeError) as e:
            _log_decode_error(s[start : end + 1], e)

    # 提取 [ ... ]
    start = s.find("[")
    end = s.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return {"items": json.loads(s[start : end + 1])}
        except (ValueError, json.JSONDecodeError) as e:
            _log_decode_error(s[start : end + 1], e)

    # strict=False 兜底
    try:
        return json.loads(s, strict=False)
    except (ValueError, json.JSONDecodeError) as e:
        _log_decode_error(s, e)

    log.warning("DEPTH safe_json_parse 无法解析（前 300 字符）: %r...", s[:300])
    return {}
