"""PeerRead 云端 GLM-4.7-Flash 打分 —— 与本地 rescore 并行的云端侧（双模型校准融合实验）。

背景（融合实验设计，见对话记录）：
  同一批 500 篇 PeerRead 分层样本需要「两套分数」：
    - 本地 Ornstein-V2：scripts/calibration/peerread_rescore.py run（DEPTH v4 全文深度模式）
    - 云端 GLM-4.7-Flash：本脚本（second_opinion 式轻量 prompt：摘要 + 全文头部）

产出：deliverables/peerread_cloud_rescore_500.jsonl
  {stem, title, venue, human_accepted, recommendation_score,
   cloud_score, cloud_verdict, cloud_reason, model, attempts, input_chars, ts, error?}

设计要点：
  1. 文本同源：复用 peerread_rescore.extract_full_text（同一 parsed_path 全文），
     与本地 rescore 输入完全一致，保证两套分数可比。
  2. 输入口径：abstract + 全文头部 6000 字符 —— 与系统 second_opinion 影子分
     （cloud_shadow_score）同款 prompt（mock_api/second_opinion.py _PAPER_PROMPT）。
  3. ⚠️ glm-4.7-flash 默认开 thinking（实测 content 为空、推理在 reasoning_content），
     必须传 thinking={"type":"disabled"} 关掉（--no-thinking 默认开）。
     关不掉（content 仍空）视为失败 → 记录 error 并重试，不静默跳过。
  4. 429/5xx/网络错误 → 指数退避重试（base×2^n，上限 120s，默认 6 次）。
     免费档 1 并发：默认 --max-concur 1，不并发（避免 429 加剧）。
  5. 断点续跑：按 stem 跳过已完成行（jsonl 追加写），失败行写 error 留待下次重试。

运行：
  python scripts/calibration/peerread_cloud_rescore.py --limit 5     # 试跑
  python scripts/calibration/peerread_cloud_rescore.py              # 全量（后台 nohup）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# 脚本位于 scripts/calibration/ 下，仓库根 = 上三层
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "calibration"))

from peerread_rescore import extract_full_text  # noqa: E402  复用同源全文抽取

DEFAULT_SAMPLE = ROOT / "deliverables" / "peerread_sample_500_20260818_135008.json"
DEFAULT_OUT = ROOT / "deliverables" / "peerread_cloud_rescore_500.jsonl"
DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

# 与 mock_api/second_opinion.py _MAX_INPUT_CHARS 一致：云端只读头部
MAX_INPUT_CHARS = 6000

# second_opinion._PAPER_PROMPT 同款（保持 cloud_shadow_score 口径，结果可对账）
_PAPER_PROMPT = """你是一位独立的期刊审稿人，正在对一篇论文做匿名评审。
以下是论文的摘要与引言（节选）。请【独立】给出你的判断，不要参考任何外部评分。

论文：
{text}

【要求】
- 综合质量分：综合考察创新性、严谨性、影响力、可复现性后给出 0~1 的一个小数。
- verdict 只能是 accept / minor_revision / major_revision / reject 之一。
- 请严格按以下格式输出（每行一个字段，key: value）：
score: <0~1 的小数，需根据论文质量在全程分布，不要集中在某一值>
verdict: major_revision
reason: 一句话理由，不超过60字"""


def load_key() -> str:
    """从 .env 读 PAPERFORGE_GLM_VISION_API_KEY（智谱 key，4.7-flash 免费可用）。"""
    env_path = ROOT / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("PAPERFORGE_GLM_VISION_API_KEY="):
            key = line.split("=", 1)[1].strip().strip('"').strip("'")
            if key:
                return key
    raise SystemExit("[cloud] .env 缺少 PAPERFORGE_GLM_VISION_API_KEY")


def supports_thinking_param(model: str) -> bool:
    """仅对支持 thinking 参数的模型系列传 thinking 字段（4.5/4.6/4.7/5.x 系列）。"""
    m = model.lower()
    return any(t in m for t in ("glm-4.5", "glm-4.6", "glm-4.7", "glm-5"))


def parse_output(raw: str) -> dict:
    """从 LLM 输出解析 score/verdict/reason（宽松正则，与 second_opinion._parse_output 同款）。"""
    out: dict = {}
    m = re.search(r"(?m)^score\s*[:：]\s*([0-9]*\.?[0-9]+)", raw)
    if m:
        try:
            out["score"] = max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            pass
    m = re.search(r"(?m)^verdict\s*[:：]\s*([A-Za-z_]+)", raw)
    if m:
        out["verdict"] = m.group(1).strip().lower()
    m = re.search(r"(?m)^reason\s*[:：]\s*(.+)$", raw)
    if m:
        out["reason"] = m.group(1).strip()[:200]
    return out


def call_glm(key: str, model: str, prompt: str, *, no_thinking: bool,
             max_tokens: int = 512, timeout: int = 120,
             max_attempts: int = 6, retry_base: float = 8.0,
             base_url: str = DEFAULT_BASE_URL) -> tuple[str, str, int]:
    """调用 GLM chat completions，429/5xx/网络错误指数退避重试。

    Returns: (raw_content, model_used, attempts)。content 为空视为失败并重试。
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if no_thinking and supports_thinking_param(model):
        body["thinking"] = {"type": "disabled"}
    attempts = 0
    while True:
        attempts += 1
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
            if r.status_code == 200:
                d = r.json()
                msg = d["choices"][0]["message"]
                content = (msg.get("content") or "").strip()
                if content:
                    return content, d.get("model") or model, attempts
                # content 为空：thinking 未关掉（推理在 reasoning_content）→ 重试
                print(f"    [cloud] {model} 返回空 content（thinking 未关），重试 {attempts}/{max_attempts}")
            else:
                err = r.json().get("error", {}).get("message", r.text[:100]) if r.headers.get("content-type", "").startswith("application/json") else r.text[:100]
                print(f"    [cloud] HTTP {r.status_code}: {err} （重试 {attempts}/{max_attempts}）")
                if r.status_code in (400, 401, 403):
                    raise RuntimeError(f"HTTP {r.status_code}: {err}")  # 永久错误不重试
        except requests.RequestException as e:
            print(f"    [cloud] 网络错误: {e} （重试 {attempts}/{max_attempts}）")
        if attempts >= max_attempts:
            raise RuntimeError(f"重试 {max_attempts} 次仍失败（最后一次: {locals().get('err', '空 content')}）")
        time.sleep(min(retry_base * (2 ** (attempts - 1)), 120.0))


def build_input(full: str, abstract: str, head_chars: int = MAX_INPUT_CHARS) -> str:
    """摘要 + 全文头部（second_opinion 口径：摘要与引言节选）。"""
    parts = []
    if abstract:
        parts.append(abstract.strip())
    head = (full or "").strip()[:head_chars]
    if head:
        parts.append(head)
    return "\n\n".join(parts)


def main():
    ap = argparse.ArgumentParser(description="PeerRead 云端 GLM-4.7-Flash 打分（与本地 rescore 并行）")
    ap.add_argument("--sample", type=str, default=str(DEFAULT_SAMPLE))
    ap.add_argument("--out", type=str, default=str(DEFAULT_OUT))
    ap.add_argument("--model", type=str, default="glm-4.7-flash")
    ap.add_argument("--no-thinking", action="store_true", default=True,
                    help="传 thinking={'type':'disabled'} 关闭思考（4.7 系列默认开，默认启用）")
    ap.add_argument("--thinking", dest="no_thinking", action="store_false",
                    help="不传 thinking 参数（模型不支持时用）")
    ap.add_argument("--head-chars", type=int, default=MAX_INPUT_CHARS)
    ap.add_argument("--max-attempts", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 篇（试跑用，0=全量）")
    args = ap.parse_args()

    sp = Path(args.sample)
    if not sp.exists():
        raise SystemExit(f"[cloud] 样本不存在: {sp}")
    sel = json.loads(sp.read_text(encoding="utf-8"))
    if isinstance(sel, dict) and isinstance(sel.get("papers"), list):
        sel = sel["papers"]
    if args.limit > 0:
        sel = sel[: args.limit]

    out_path = Path(args.out)
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rec = json.loads(line)
                    if not rec.get("error") and rec.get("cloud_score") is not None:
                        done.add(rec["stem"])
                except Exception:
                    pass

    key = load_key()
    f = open(out_path, "a", encoding="utf-8")
    total = len(sel)
    t0 = time.time()
    ok = fail = skip = 0
    try:
        for i, j in enumerate(sel, 1):
            stem = j["stem"]
            if stem in done:
                skip += 1
                print(f"  [{i}/{total}] skip {stem}（已完成）")
                continue
            try:
                full, abstract = extract_full_text(j["parsed_path"])
            except Exception as e:
                print(f"  [{i}/{total}] {stem} 全文提取失败: {e}")
                fail += 1
                continue
            text = build_input(full, abstract, args.head_chars)
            rec = {
                "stem": stem,
                "title": j.get("title", ""),
                "venue": j.get("venue", ""),
                "human_accepted": j.get("accepted"),
                "recommendation_score": j.get("recommendation_score"),
                "input_chars": len(text),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            try:
                raw, model_used, attempts = call_glm(
                    key, args.model, _PAPER_PROMPT.format(text=text),
                    no_thinking=args.no_thinking, max_attempts=args.max_attempts,
                )
                parsed = parse_output(raw)
                if not parsed.get("score") and not parsed.get("verdict"):
                    raise RuntimeError(f"解析失败，原始输出: {raw[:200]!r}")
                rec.update({
                    "model": model_used,
                    "attempts": attempts,
                    "cloud_score": parsed.get("score"),
                    "cloud_verdict": parsed.get("verdict"),
                    "cloud_reason": parsed.get("reason", ""),
                })
                ok += 1
                print(f"  [{i}/{total}] {stem} score={rec['cloud_score']} verdict={rec['cloud_verdict']} "
                      f"human_accept={j.get('accepted')} attempts={attempts}")
            except Exception as e:
                rec["error"] = str(e)[:300]
                fail += 1
                print(f"  [{i}/{total}] {stem} FAILED: {e}")
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
    finally:
        f.close()
    dt = time.time() - t0
    print(f"[cloud] 完成：ok={ok} fail={fail} skip={skip} 耗时={dt:.0f}s "
          f"（{dt / max(1, ok):.1f}s/篇） 结果 -> {out_path}")


if __name__ == "__main__":
    main()
