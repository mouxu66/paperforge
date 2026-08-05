"""P0-1 mini-pilot 诊断脚本：对 PeerRead 200 篇跑 extract_text_figure_evidence +
_score_text_figure_consistency，统计分布与失败模式。

完全离线：不依赖 GPU / DB / API，只用 .peerread_cache 里的 parsed JSON。
用于回答："为何 baseline 198 篇 figure_consistency_score 全是 0.5？"
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ── 把 mock_api.pdf_parser 里的常量与函数复制过来，避免触发 fastapi 导入链 ──
_TEXT_FIG_CAPTION_RE = re.compile(r"(?im)^(?:figure|fig\.?)\s*(\d+)\s*[:.\-]?\s*(.*)$")
_TEXT_NEWFIG_RE = re.compile(r"(?im)^(?:figure|fig\.?|table|tab\.?)\s*\d+\b")
_TEXT_SECTION_RE = re.compile(r"(?im)^[0-9]+(?:\.[0-9]+)*\s+[A-Z][A-Za-z]")
_METRIC_RE = re.compile(
    r"(?i)\b(accuracy|f1|auc|precision|recall|bleu|rouge(?:[-_ ]?[a-z])?|"
    r"loss|error|score|map|ap|iou|dice|perplexity|throughput|speedup|"
    r"improvement|gain|top[-\s]?1|top[-\s]?5|params?|flops?)\b"
)
_NUMBER_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3})?\s*%|\b\d{1,3}\.\d{1,4}\b|\b\d{2,4}\b")


def extract_full_text(parsed_path: str) -> tuple[str, str]:
    """从 parsed PDF JSON 抽 full_text（复制自 peerread_rescore.extract_full_text）。"""
    o = json.loads(Path(parsed_path).read_text(encoding="utf-8"))
    md = o.get("metadata", o)
    sections = md.get("sections", []) or []
    parts = []
    for s in sections:
        h = s.get("heading", "") or ""
        t = s.get("text", "") or ""
        if h:
            parts.append(f"\n## {h}\n")
        if t:
            parts.append(t)
    full = "\n".join(parts).strip()
    abstract = (md.get("abstractText") or "").strip()
    return full, abstract


def extract_text_figure_evidence(text: str, max_figures: int = 12) -> list[dict]:
    """从 full_text 抽取图注 + 数值断言（复制自 mock_api.pdf_parser，避免触发导入链）。"""
    if not text or not text.strip():
        return []
    lines = text.splitlines()
    n = len(lines)
    offsets: list[int] = []
    acc = 0
    for line in lines:
        offsets.append(acc)
        acc += len(line) + 1
    evidences: list[dict] = []
    i = 0
    while i < n and len(evidences) < max_figures:
        m = _TEXT_FIG_CAPTION_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        fig_num = int(m.group(1))
        caption_parts = [m.group(2)] if m.group(2).strip() else []
        j = i + 1
        while j < n and len(caption_parts) < 6:
            nxt = lines[j].strip()
            if not nxt:
                break
            if _TEXT_NEWFIG_RE.match(nxt):
                break
            if _TEXT_SECTION_RE.match(nxt):
                break
            caption_parts.append(nxt)
            j += 1
        caption = " ".join(p for p in caption_parts if p).strip()
        if len(caption) < 8:
            i = j if j > i else i + 1
            continue
        caption_numbers: list[dict] = []
        for num_m in _NUMBER_RE.finditer(caption):
            val = num_m.group(0).strip()
            ctx = caption[max(0, num_m.start() - 25):num_m.end() + 5]
            metric = _METRIC_RE.search(ctx)
            caption_numbers.append(
                {"value": val, "metric": metric.group(1).lower() if metric else None}
            )
        if not caption_numbers:
            i = j if j > i else i + 1
            continue
        cap_start = offsets[i]
        cap_end = (offsets[j - 1] + len(lines[j - 1])) if j - 1 < n else cap_start
        evidences.append({
            "figure_number": fig_num,
            "caption_text": caption[:400],
            "caption_numbers": caption_numbers,
            "_cap_start": cap_start,
            "_cap_end": cap_end,
        })
        i = j if j > i else i + 1
    for ev in evidences:
        cap_start = ev.pop("_cap_start", 0)
        cap_end = ev.pop("_cap_end", 0)
        matches: list[dict] = []
        for cn in ev["caption_numbers"]:
            val = cn["value"]
            found = False
            body_span = ""
            try:
                for fm in re.finditer(re.escape(val), text):
                    s, e = fm.span()
                    if cap_start <= s < cap_end:
                        continue
                    found = True
                    body_span = text[max(0, s - 35):e + 35].replace("\n", " ")
                    break
            except Exception:
                pass
            matches.append({
                "value": val,
                "metric": cn["metric"],
                "found": found,
                "spans": [body_span] if body_span else [],
            })
        ev["body_matches"] = matches
    return evidences


def _score_text_figure_consistency(evidences: list[dict]) -> tuple[float, str, list[str]]:
    """复制自 depth_eval_v4._score_text_figure_consistency。"""
    per_caption: list[float] = []
    flags: list[str] = []
    total_nums = 0
    for ev in evidences:
        nums = ev.get("body_matches") or []
        if not nums:
            continue
        total_nums += len(nums)
        found = sum(1 for x in nums if x.get("found"))
        ratio = found / len(nums)
        per_caption.append(ratio)
        missing = [str(x["value"]) for x in nums if not x.get("found")]
        if missing:
            flags.append(
                f"cap{ev.get('figure_number')}_num_missing:{','.join(missing[:3])}"
            )
    if not per_caption:
        return 0.5, "无可用图注数值断言", []
    avg = sum(per_caption) / len(per_caption)
    score = 0.4 + 0.55 * avg
    found_total = sum(1 for ev in evidences for x in ev.get("body_matches", []) if x.get("found"))
    reasoning = (
        f"文本层图文一致性（无渲染图）：{len(evidences)} 个图注含 {total_nums} 个数值断言，"
        f"{found_total}/{total_nums} 在正文其余部分命中（按图注平均一致率 {avg:.0%}）"
    )
    return round(score, 3), reasoning, flags[:5]


SAMPLE = ROOT / "deliverables" / "peerread_sample.json"


def classify_failure(ev: dict) -> str:
    """给每条 evidence 打失败模式标签。"""
    nums = ev.get("caption_numbers") or []
    matches = ev.get("body_matches") or []
    if not nums:
        return "cap_no_number"
    if not matches:
        return "no_body_match_at_all"
    found = sum(1 for m in matches if m.get("found"))
    if found == 0:
        return "all_unfound"
    if found == len(matches):
        return "all_found"
    return "partial_found"


def run(n: int | None = None) -> int:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    if n:
        sample = sample[:n]

    rows = []
    for j in sample:
        stem = j["stem"]
        parsed = j["parsed_path"]
        try:
            full, _ = extract_full_text(parsed)
        except Exception as e:
            rows.append({"stem": stem, "ok": False, "err": f"extract_full_text: {e}"})
            continue
        if not full or len(full) < 50:
            rows.append({"stem": stem, "ok": False, "err": "full_text 空", "full_text_len": len(full)})
            continue
        evs = extract_text_figure_evidence(full)
        if not evs:
            rows.append({
                "stem": stem, "ok": True,
                "full_text_len": len(full),
                "evidences": 0, "score": 0.5, "reasoning": "无图注可识别",
                "flags": [],
            })
            continue
        score, reasoning, flags = _score_text_figure_consistency(evs)
        rows.append({
            "stem": stem, "ok": True,
            "full_text_len": len(full),
            "evidences": len(evs),
            "score": score,
            "reasoning": reasoning,
            "flags": flags,
            "failure_modes": Counter(classify_failure(ev) for ev in evs),
            "evidences_detail": [
                {
                    "figure_number": ev.get("figure_number"),
                    "caption_numbers": len(ev.get("caption_numbers") or []),
                    "body_matches": len(ev.get("body_matches") or []),
                    "modes": classify_failure(ev),
                }
                for ev in evs[:5]
            ],
        })

    # ── 统计 ──
    ok_rows = [r for r in rows if r.get("ok")]
    no_full = [r for r in rows if not r.get("ok")]
    no_evidences = [r for r in ok_rows if r.get("evidences", 0) == 0]
    has_evidences = [r for r in ok_rows if r.get("evidences", 0) > 0]
    scores = [r["score"] for r in ok_rows]
    score_dist = Counter(
        "0.5" if abs(s - 0.5) < 1e-3 else
        ("<0.5" if s < 0.5 else ">0.5")
        for s in scores
    )

    print("=" * 70)
    print(f"诊断结果（n={len(rows)}）")
    print("=" * 70)
    print(f"  full_text 提取失败:        {len(no_full)}")
    print(f"  full_text 成功:            {len(ok_rows)}")
    print(f"    └─ 无图注可识别:          {len(no_evidences)}  ({len(no_evidences) / max(len(ok_rows), 1) * 100:.1f}%)")
    print(f"    └─ 有图注:                 {len(has_evidences)}  ({len(has_evidences) / max(len(ok_rows), 1) * 100:.1f}%)")
    print()
    print("  score 分布:")
    print(f"    = 0.5（中性）:            {score_dist.get('0.5', 0)}")
    print(f"    < 0.5（不一致倾向）:      {score_dist.get('<0.5', 0)}")
    print(f"    > 0.5（一致倾向）:        {score_dist.get('>0.5', 0)}")
    if scores:
        print(f"    mean={sum(scores)/len(scores):.3f}  min={min(scores):.3f}  max={max(scores):.3f}")
    print()

    # 失败模式聚合
    all_modes: Counter = Counter()
    for r in has_evidences:
        all_modes.update(r.get("failure_modes", Counter()))
    print("  evidence 级失败模式分布:")
    for mode, cnt in all_modes.most_common():
        print(f"    {mode:25s}  {cnt}")
    print()

    # 前 5 篇有 evidences 但 score=0.5 的样本（要详细看）
    weird = [r for r in has_evidences if abs(r["score"] - 0.5) < 1e-3][:5]
    print(f"  有图注但 score=0.5 的样本（前 5，便于深入排查）:")
    for r in weird:
        print(f"    {r['stem']}: ev={r['evidences']}, modes={dict(r['failure_modes'])}")
    print()

    # 前 5 篇 score != 0.5 的样本
    nonzero = [r for r in has_evidences if abs(r["score"] - 0.5) >= 1e-3][:5]
    print(f"  非 0.5 的样本（前 5）:")
    for r in nonzero:
        print(f"    {r['stem']}: score={r['score']}, ev={r['evidences']}, reasoning={r['reasoning'][:80]}")

    # 把详细结果写入 jsonl 方便后续排查
    out = ROOT / "deliverables" / "p0_text_evidence_diagnose.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in rows:
            # Counter 不可 JSON 序列化
            r2 = {k: (dict(v) if hasattr(v, "most_common") else v) for k, v in r.items()}
            f.write(json.dumps(r2, ensure_ascii=False) + "\n")
    print(f"\n  详细结果: {out}")
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else None
    sys.exit(run(n))
