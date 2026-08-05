"""单篇感悟报告评审诊断器：直连 ReflectionReviewer.review()，抓原始 LLM 响应 + 证据裁决轨迹。

用途
====
当某篇报告 4 维全是 0.3（即 R1「有效证据 < 2 → CAP 0.3」触发）时，
把「为什么触发」拆开，判定根因属于哪一类：

  A. LLM 超时 / 空返回       → raw 为空，耗时 ≈ 超时上限
  B. JSON 解析失败           → raw 非空但 safe_json_parse 提不出可用字段
  C. snippet 未逐字命中      → evidence 被 _snippet_exists 判伪（6-gram 命中率 < 0.6）
  D. 交叉引用断裂            → snippet 真实，但 claim.evidence_id / evidence.claim_ref 对不上
  E. LLM 真给低分            → 证据充足、R1 未触发，分数本来就低（属正常评分）

与批量脚本的区别：批量 CSV 只落 4 维分数，effective_evidence / hardcoded_overrides /
parse_failed 都没往外传（reflection_pipeline.analyze_reflection_file 未回填），
所以从 CSV 看不出 R1 触发原因。本脚本绕开 pipeline 直接调 reviewer 并全量打印。

用法
====
    .venv/Scripts/python.exe scripts/diagnose_reflection_one.py --sid 999900000011
    .venv/Scripts/python.exe scripts/diagnose_reflection_one.py --sid 999900000011 --timeout 400
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"
_OUT_DIR = ROOT / "deliverables" / "diag"


def _line(ch: str = "-", n: int = 78) -> None:
    print(ch * n, flush=True)


def load_binding(sid: str) -> str | None:
    """从 depth_reviews_v4 读回该学号的历史论文绑定。"""
    import sqlite3

    if not _DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        row = conn.execute(
            "select reflection_result from depth_reviews_v4 where paper_id = ?",
            (f"reflection_{sid}",),
        ).fetchone()
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    try:
        d = json.loads(row[0])
    except Exception:  # noqa: BLE001
        return None
    return d.get("source_arxiv_id") or d.get("bound_paper_id")


def load_paper_text(paper_id: str | None) -> str:
    if not paper_id:
        return ""
    import sqlite3

    conn = sqlite3.connect(str(_DB_PATH))
    try:
        row = conn.execute("select full_text from papers where id = ?", (paper_id,)).fetchone()
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] 读论文全文失败: {e}")
        return ""
    finally:
        conn.close()
    return (row[0] or "") if row else ""


def containment(snippet: str, full_text: str, n: int = 6) -> float:
    """snippet 的字符 n-gram 在报告全文中的命中率（与 _snippet_exists 同口径）。"""
    s = re.sub(r"\s+", "", snippet or "")
    t = re.sub(r"\s+", "", full_text or "")
    if not s:
        return 0.0
    if len(s) < n:
        return 1.0 if s in t else 0.0
    grams = {s[i : i + n] for i in range(len(s) - n + 1)}
    return sum(1 for g in grams if g in t) / len(grams)


def audit_evidence(data: dict, full_text: str) -> None:
    """逐条打印 evidence 的真实性与交叉引用状态，定位 R1 触发点。"""
    from mock_api.depth_eval_reflection import _SNIPPET_CONTAINMENT_THR

    claims = data.get("claims") or []
    pool = data.get("evidence_pool") or []
    print(f"  claims={len(claims)}  evidence_pool={len(pool)}")
    if not claims and not pool:
        print("  ⚠ LLM 未产出任何 claims/evidence → R1 必然触发")
        return

    valid_eids: set[str] = set()
    print("\n  [evidence 真实性检查] 阈值 6-gram 命中率 >= %.2f" % _SNIPPET_CONTAINMENT_THR)
    for ev in pool:
        eid = (ev.get("id") or "").strip()
        snip = ev.get("snippet", "") or ""
        ratio = containment(snip, full_text)
        ok = ratio >= _SNIPPET_CONTAINMENT_THR
        if eid and ok:
            valid_eids.add(eid)
        flag = "✔真实" if ok else "✘判伪"
        print(
            f"    {eid or '(无id)':<6} {flag} 命中率={ratio:.2f} "
            f"claim_ref={ev.get('claim_ref') or '(无)'!s:<6} snippet={snip[:46]!r}"
        )

    valid_cids = {(c.get("id") or "").strip() for c in claims if c.get("id")}
    print("\n  [交叉引用检查]")
    forward = 0
    for c in claims:
        cid = (c.get("id") or "").strip()
        eid = (c.get("evidence_id") or "").strip()
        hit = eid in valid_eids
        forward += 1 if hit else 0
        print(
            f"    claim {cid or '(无id)':<6} → evidence_id={eid or '(无)'!s:<6} "
            f"{'✔命中' if hit else '✘悬空'}"
        )
    backward = sum(
        1
        for ev in pool
        if (ev.get("claim_ref") or "").strip() in valid_cids
        and (ev.get("id") or "").strip() in valid_eids
    )
    effective = max(forward, backward)
    print(f"\n  正向(claim→evidence)={forward}  反向(evidence→claim)={backward}")
    print(f"  effective_evidence_count = max = {effective}  (R1 阈值 = 2)")
    if effective < 2:
        if not valid_eids and pool:
            print("  ▶ 根因 C：LLM 给了 evidence，但 snippet 未逐字命中报告原文（判伪）")
        elif valid_eids:
            print("  ▶ 根因 D：snippet 真实，但 claim/evidence 的 id 引用对不上")
        else:
            print("  ▶ 根因 A/B：LLM 未产出有效 evidence")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sid", required=True, help="12 位学号")
    ap.add_argument(
        "--source-dir", type=Path, default=Path.home() / "Desktop" / "Word文档"
    )
    ap.add_argument("--timeout", type=int, default=0, help="覆盖本机 LLM 超时上限（秒）")
    args = ap.parse_args()

    if args.timeout:
        os.environ["PAPERFORGE_LOCAL_TIMEOUT_CAP"] = str(args.timeout)
        os.environ["PAPERFORGE_LLM_WATCHDOG_TIMEOUT"] = str(args.timeout + 50)
    os.environ.setdefault("PAPERFORGE_REFLECTION_SKIP_CROSSVAL", "1")

    sid = args.sid
    matches = sorted(args.source_dir.expanduser().glob(f"*{sid}*.docx"))
    if not matches:
        print(f"未找到 {sid} 的 docx（目录 {args.source_dir}）")
        return 2
    path = matches[0]

    from mock_api.reflection_docx_parser import parse_docx_from_bytes

    parse = parse_docx_from_bytes(path.read_bytes(), filename=str(path))
    report_text = parse.raw_text or ""
    bound = load_binding(sid)
    paper_text = load_paper_text(bound)

    _line("=")
    print(f"诊断对象: {path.name}")
    print(f"  学号={parse.student_id or sid}  姓名={parse.name or '(未识别)'}")
    print(f"  报告字数={len(report_text)}  绑定论文={bound}  论文全文字数={len(paper_text)}")
    print(f"  报告题目字段={str(parse.paper_title)[:60]!r}")
    print(
        f"  超时配置: LOCAL_TIMEOUT_CAP={os.environ.get('PAPERFORGE_LOCAL_TIMEOUT_CAP', '100')}s "
        f"WATCHDOG={os.environ.get('PAPERFORGE_LLM_WATCHDOG_TIMEOUT', '120')}s"
    )
    _line("=")

    # ---- 带埋点的 LLM 包装 ----
    from mock_api.depth_eval_reflection import ReflectionReviewer, call_llm

    calls: list[dict] = []

    def traced_llm(prompt: str, system_prompt: str = "") -> str:
        idx = len(calls) + 1
        print(f"\n[LLM #{idx}] 发送 prompt {len(prompt)} 字符 …", flush=True)
        t0 = time.time()
        try:
            raw = call_llm(prompt, system_prompt)
            err = ""
        except Exception as e:  # noqa: BLE001
            raw, err = "", f"{type(e).__name__}: {e}"
        dt = time.time() - t0
        print(
            f"[LLM #{idx}] 耗时 {dt:.1f}s  返回 {len(raw or '')} 字符"
            + (f"  异常={err}" if err else ""),
            flush=True,
        )
        calls.append({"idx": idx, "prompt_chars": len(prompt), "sec": dt, "raw": raw, "err": err})
        return raw

    reviewer = ReflectionReviewer(llm_func=traced_llm)
    t_all = time.time()
    res = reviewer.review(
        f"diag_{sid}",
        parse.paper_title or "报告",
        report_text,
        student_id=parse.student_id or "",
        paper_text=paper_text,
    )
    total = time.time() - t_all

    # ---- 结果 ----
    _line("=")
    print(f"review() 总耗时 {total:.1f}s，LLM 调用 {len(calls)} 次")
    print(f"  parse_failed        = {res.parse_failed}")
    print(f"  truncated           = {res.truncated}")
    print(f"  effective_evidence  = {res.effective_evidence_count}")
    print(f"  verdict             = {res.verdict}")
    print(f"  scores              = {json.dumps(res.scores, ensure_ascii=False)}")
    print("  hardcoded_overrides =")
    for ov in res.hardcoded_overrides or ["(无)"]:
        print(f"    - {ov}")
    print("\n  node_logs:")
    for lg in res.node_logs:
        print(f"    {lg}")

    # ---- 原始响应落盘 + 逐条审计 ----
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    from mock_api.json_utils import safe_json_parse

    for c in calls:
        f = _OUT_DIR / f"{sid}_llm_{c['idx']}.txt"
        f.write_text(c["raw"] or f"(空返回) err={c['err']}", encoding="utf-8")
        _line()
        print(f"[LLM #{c['idx']}] raw 已存 → {f}")
        if not c["raw"]:
            print("  ▶ 根因 A：LLM 返回空（超时 / 连接异常）")
            continue
        data = safe_json_parse(c["raw"], None)
        useful = bool(
            data.get("claims")
            or data.get("evidence_pool")
            or data.get("summary")
            or ("understanding_accuracy" in data)
        )
        if not useful:
            print(f"  ▶ 根因 B：raw 有 {len(c['raw'])} 字符但 JSON 解析不出可用字段")
            print(f"  raw 前 300 字: {c['raw'][:300]!r}")
            continue
        print(
            "  LLM 自评 4 维: "
            + ", ".join(
                f"{k}={data.get(k)}"
                for k in (
                    "understanding_accuracy",
                    "analysis_depth",
                    "innovative_insights",
                    "evidence_support",
                )
            )
        )
        audit_evidence(data, report_text)

    _line("=")
    summary_path = _OUT_DIR / f"{sid}_diag.json"
    summary_path.write_text(
        json.dumps(
            {
                "sid": sid,
                "file": path.name,
                "report_chars": len(report_text),
                "bound_paper_id": bound,
                "paper_chars": len(paper_text),
                "total_sec": round(total, 1),
                "llm_calls": [
                    {k: v for k, v in c.items() if k != "raw"} | {"raw_chars": len(c["raw"] or "")}
                    for c in calls
                ],
                "parse_failed": res.parse_failed,
                "truncated": res.truncated,
                "effective_evidence": res.effective_evidence_count,
                "verdict": res.verdict,
                "scores": res.scores,
                "overrides": res.hardcoded_overrides,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"诊断摘要 → {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
