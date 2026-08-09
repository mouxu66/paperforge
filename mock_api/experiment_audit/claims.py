"""实验论断抽取（指南 Step 3）。

zero-shot 走本地文本 Qwen（8080，vram_guard("text") 仲裁）；
LLM 不可用/输出非法 JSON 时回退到规则抽取（figure_claims 数值论断
+ Figure/Table 引用正则），保证语义层检测在离线降级下仍有基本输入。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..figure_claims import extract_claims_from_text
from ..vram_scheduler import vram_guard

logger = logging.getLogger(__name__)

CLAIM_PROMPT = """从以下段落中提取所有实验论断。每条论断包含：
- claim_text: 原句
- claim_type: improvement/comparison/ablation/efficiency/sota
- related_metrics: 涉及的指标名
- related_figures: 涉及的图表编号
- related_tables: 涉及的表格编号

段落：
{paragraph}

只输出 JSON 数组，不要任何解释：
"""

_VALID_CLAIM_TYPES = {"improvement", "comparison", "ablation", "efficiency", "sota"}

_FIG_TABLE_REF_RE = re.compile(r"\b(Figures?|Fig\.|Tables?)\s+(\d+)\b", re.IGNORECASE)


def _text_qwen_chat(prompt: str, timeout: int = 120) -> str:
    """向本地文本 Qwen（8080）发 chat 请求；失败返回空串（fail-open）。"""
    import requests

    from ..settings import get_settings

    s = get_settings()
    host = (s.llama_server_host or "localhost").strip() or "localhost"
    base_url = f"http://{host}:{s.llama_server_port}"
    payload = {
        "model": "qwen",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 1024,
        "stream": False,
    }
    try:
        with vram_guard("text"):
            resp = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001 - LLM 不可用属正常降级
        logger.warning("[audit] 文本 Qwen 调用失败: %s", exc)
        return ""


def _parse_json_array(text: str) -> list[Any]:
    """容错解析 LLM 输出的 JSON 数组（markdown 围栏 / 前后噪声）。"""
    if not text:
        return []
    s = text.strip()
    # 去 markdown 围栏
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        data = json.loads(s)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, ValueError):
        pass
    # 兜底：截取首个 [ 到末尾 ] 的子串
    start, end = s.find("["), s.rfind("]")
    if start != -1 and end > start:
        try:
            data = json.loads(s[start : end + 1])
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, ValueError):
            pass
    return []


def _parse_json_object(text: str) -> dict[str, Any]:
    """容错解析 LLM 输出的 JSON 对象（围栏/前后噪声/单元素数组均可）。"""
    arr = _parse_json_array(text)
    if arr and isinstance(arr[0], dict):
        return arr[0]
    s = (text or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(s[start : end + 1])
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _normalize_claim(item: dict[str, Any]) -> dict[str, Any] | None:
    """规范化单条 LLM 论断；缺 claim_text 的丢弃。"""
    text = str(item.get("claim_text") or "").strip()
    if not text:
        return None
    ctype = str(item.get("claim_type") or "comparison").strip().lower()
    return {
        "claim_text": text,
        "claim_type": ctype if ctype in _VALID_CLAIM_TYPES else "comparison",
        "related_metrics": [str(m) for m in (item.get("related_metrics") or [])],
        "related_figures": [str(f) for f in (item.get("related_figures") or [])],
        "related_tables": [str(t) for t in (item.get("related_tables") or [])],
    }


def extract_claims_llm(paragraph: str, timeout: int = 120) -> list[dict[str, Any]]:
    """LLM 抽取论断；失败返回空列表（由调用方决定是否回退）。"""
    raw = _text_qwen_chat(CLAIM_PROMPT.format(paragraph=paragraph[:6000]), timeout)
    claims = [c for c in map(_normalize_claim, _parse_json_array(raw)) if c]
    return claims


def extract_claims_rule_based(paragraph: str) -> list[dict[str, Any]]:
    """规则回退：数值论断 + Figure/Table 引用。"""
    refs: dict[str, list[str]] = {"figures": [], "tables": []}
    for m in _FIG_TABLE_REF_RE.finditer(paragraph):
        kind = "figures" if m.group(1).lower().startswith("fig") else "tables"
        num = m.group(2)
        if num not in refs[kind]:
            refs[kind].append(num)
    numeric = extract_claims_from_text(paragraph)
    if not numeric and not refs["figures"] and not refs["tables"]:
        return []
    return [
        {
            "claim_text": paragraph.strip()[:800],
            "claim_type": "comparison",
            "related_metrics": sorted({c.get("metric", "") for c in numeric if c.get("metric")}),
            "related_figures": refs["figures"],
            "related_tables": refs["tables"],
        }
    ]


def extract_claims(paragraph: str, *, allow_llm: bool = True) -> list[dict[str, Any]]:
    """论断抽取入口：LLM 优先，空结果回退规则抽取。"""
    if not (paragraph or "").strip():
        return []
    if allow_llm:
        claims = extract_claims_llm(paragraph)
        if claims:
            return claims
    return extract_claims_rule_based(paragraph)
