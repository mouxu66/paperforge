#!/usr/bin/env python3
"""Compare DEPTH v4.2 scores with a direct single-prompt local model evaluation.

Loads saved DEPTH review JSON files, fetches the corresponding arXiv PDFs,
calls the local llama-server directly with an open-ended review prompt, and
prints a side-by-side comparison.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests

from mock_api.llm.openai_provider import ChatMessage, OpenAIProvider  # noqa: E402
from scripts.arxiv_depth_test import download_pdf, extract_text  # noqa: E402


def build_direct_prompt(title: str, full_text: str) -> str:
    """Build a single prompt asking the local model to act as a peer reviewer."""
    text_sample = full_text[:6000].strip().replace("{", "{{").replace("}", "}}")
    return (
        "You are an expert academic peer reviewer. Evaluate the following paper "
        "and provide scores from 0.0 (poor) to 1.0 (excellent) for:\n"
        "- novelty\n"
        "- rigor\n"
        "- influence\n"
        "- reproducibility\n\n"
        "Also provide an overall verdict chosen from exactly one of: "
        "accept, minor_revision, major_revision, reject.\n\n"
        "Respond ONLY with a JSON object in this exact format, with no markdown "
        "fences and no extra commentary outside the JSON:\n"
        '{"novelty": 0.0, "rigor": 0.0, "influence": 0.0, "reproducibility": 0.0, '
        '"verdict": "accept", "reasoning": "one brief sentence"}\n\n'
        f"Paper Title: {title}\n\n"
        f"Paper Text:\n{text_sample}"
    )


def _find_json_block(text: str) -> str:
    """Find the largest JSON object in the response that contains expected keys."""
    # First try: look for fenced code blocks
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fenced:
        candidate = fenced.group(1).strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            return candidate

    # Second try: find all top-level JSON objects and pick the one with expected keys
    candidates = re.findall(r"\{[\s\S]*?\}", text)
    best: str | None = None
    for candidate in candidates:
        lowered = candidate.lower()
        if all(k in lowered for k in ("novelty", "rigor", "influence", "reproducibility")):
            if best is None or len(candidate) > len(best):
                best = candidate
    if best:
        return best

    # Fallback: the largest JSON-looking block
    if candidates:
        return max(candidates, key=len)
    raise ValueError(f"No JSON object found in response: {text[:500]}")


def parse_direct_response(text: str) -> dict:
    """Extract and validate the JSON block from a possibly chatty local model response."""
    json_str = _find_json_block(text)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON: {json_str[:500]}") from exc

    scores = {
        "novelty": _clamp01(_parse_float(data.get("novelty"))),
        "rigor": _clamp01(_parse_float(data.get("rigor"))),
        "influence": _clamp01(_parse_float(data.get("influence"))),
        "reproducibility": _clamp01(_parse_float(data.get("reproducibility"))),
        "verdict": _normalize_verdict(data.get("verdict", "")),
        "reasoning": str(data.get("reasoning", "")),
    }
    return scores


def _parse_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.5


def _clamp01(val: float) -> float:
    return max(0.0, min(1.0, val))


def _normalize_verdict(val) -> str:
    v = str(val).strip().lower()
    allowed = {"accept", "minor_revision", "major_revision", "reject"}
    return v if v in allowed else ""


def call_local_model(prompt: str, base_url: str, model: str) -> str:
    """Call the local OpenAI-compatible endpoint directly."""
    provider = OpenAIProvider(
        api_key="sk-local",
        model=model,
        base_url=base_url,
    )
    messages = [ChatMessage(role="user", content=prompt)]
    result = provider.chat(messages, temperature=0.1, max_tokens=1024)
    return result.content or ""


def check_server_reachable(base_url: str, timeout: int = 5) -> bool:
    try:
        resp = requests.get(base_url.replace("/v1", "/health") if "/v1" in base_url else base_url, timeout=timeout)
        return resp.status_code == 200
    except Exception:
        pass
    try:
        resp = requests.get(base_url + "/models", timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare DEPTH v4.2 scores with direct local model evaluation"
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8080/v1", help="Local llama-server OpenAI endpoint")
    parser.add_argument("--model", default="D:/Qwen3.5-9B-Q3_K_M.gguf", help="Model name for local endpoint")
    args = parser.parse_args()

    if not check_server_reachable(args.base_url):
        print(f"[错误] 无法连接到本地模型服务端点 {args.base_url}")
        return 1

    # Find saved DEPTH review JSON files (exclude deep/speed variants)
    scripts_dir = Path("scripts")
    json_files = sorted(
        p for p in scripts_dir.glob("depth_review_*.json")
        if "_deep" not in p.stem and "_speed" not in p.stem
    )
    if not json_files:
        print("未找到 DEPTH 评分 JSON 文件")
        return 1

    print(f"找到 {len(json_files)} 篇已评审论文的 DEPTH 结果")

    for json_path in json_files:
        depth = json.loads(json_path.read_text(encoding="utf-8"))
        arxiv_id = depth["paper_id"]
        title = depth["title"]
        print(f"\n{'=' * 70}")
        print(f"[论文] {arxiv_id}: {title}")

        # Fetch paper text
        try:
            pdf_bytes = download_pdf(arxiv_id)
            full_text = extract_text(pdf_bytes)
            print(f"[文本] 提取了 {len(full_text)} 字符")
        except Exception as exc:
            print(f"[警告] 获取/解析 PDF 失败: {exc}，跳过")
            continue

        # Direct local model evaluation
        prompt = build_direct_prompt(title, full_text)
        start = time.time()
        try:
            raw_response = call_local_model(prompt, args.base_url, args.model)
            elapsed = time.time() - start
            print(f"[本地模型] 响应耗时 {elapsed:.1f}s")
            print(f"[本地模型原始回复]\n{raw_response[:500]}\n")
            local = parse_direct_response(raw_response)
        except Exception as exc:
            print(f"[错误] 本地模型调用/解析失败: {exc}")
            continue

        # Compare side by side
        print("\n[对比] DEPTH v4.2 (系统) vs 本地模型 (直接单提示)")
        print(f"{'指标':<18} {'DEPTH':>10} {'Local':>10} {'Diff':>10}")
        print("-" * 52)
        for key in ["novelty", "rigor", "influence", "reproducibility"]:
            d = depth.get(f"{key}_score", depth.get(key, 0.0))
            l = local.get(key, 0.0)
            print(f"{key:<18} {d:>10.4f} {l:>10.4f} {l - d:>+10.4f}")

        print(f"\n{'verdict':<18} {depth.get('final_verdict'):>10} {local.get('verdict'):>10}")
        print(f"[DEPTH calibrated_score] {depth.get('calibrated_score')}")
        print(f"[本地模型 reasoning] {local.get('reasoning')}")

        # Save comparison including raw response
        comparison = {
            "arxiv_id": arxiv_id,
            "title": title,
            "depth": {
                "novelty": depth.get("novelty_score"),
                "rigor": depth.get("rigor_score"),
                "influence": depth.get("influence_score"),
                "reproducibility": depth.get("reproducibility_score"),
                "verdict": depth.get("final_verdict"),
                "calibrated_score": depth.get("calibrated_score"),
            },
            "local": local,
            "local_raw": raw_response,
        }
        out_path = scripts_dir / f"local_model_comparison_{arxiv_id}.json"
        out_path.write_text(json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[输出] 对比结果保存到 {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
