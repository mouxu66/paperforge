"""回填 ornith_reports_41 中缺失的报告（stdin 被吞 + 429 重试）。

- 读 ornith_reports_41_ids.txt 的 41 个 report id
- 从 ornith_reports_41.log 已落盘的 JSON 行判定已完成（llm_failed=false）
- 对未完成/失败的 report，用 run_evidence_once.py 跑（stdin=DEVNULL），429/失败指数退避重试
- 产出 deliverables/ornith_reports_41_results.json（41 条全量）并追加 JSON 行到 log
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
RUNNER = str(ROOT / "scripts" / "calibration" / "run_evidence_once.py")
IDS_FILE = ROOT / "deliverables" / "ornith_reports_41_ids.txt"
LOG_FILE = ROOT / "deliverables" / "ornith_reports_41.log"
OUT_FILE = ROOT / "deliverables" / "ornith_reports_41_results.json"

EVIDENCE = "1"
THINKING = "0"
MAX_RETRY = 6
BACKOFF = [8, 16, 32, 48, 64, 90]


def load_ids() -> list[str]:
    return [ln.strip() for ln in IDS_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]


def completed_from_log() -> dict[str, dict]:
    """report_id -> parsed json（仅取 llm_failed=false 的）"""
    done: dict[str, dict] = {}
    if not LOG_FILE.exists():
        return done
    for line in LOG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = obj.get("report")
        if rid and obj.get("llm_failed") is False and obj.get("average") is not None:
            done[rid] = obj
    return done


def run_one(rid: str) -> dict | None:
    """跑单篇，带 429/失败重试。返回解析后的 json 或 None。"""
    for attempt in range(1, MAX_RETRY + 1):
        try:
            proc = subprocess.run(
                [PY, RUNNER, "--report", rid, "--evidence", EVIDENCE, "--thinking", THINKING],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            print(f"[retry {rid}] attempt {attempt}: TIMEOUT", flush=True)
            if attempt < MAX_RETRY:
                time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
            continue
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        # 取最后一行 json（CoT 落盘 warn 可能在 stderr）
        json_line = ""
        for l in reversed(out.splitlines()):
            l = l.strip()
            if l.startswith("{"):
                json_line = l
                break
        if not json_line:
            print(f"[retry {rid}] attempt {attempt}: no json; stderr={err[:200]}", flush=True)
            if attempt < MAX_RETRY:
                time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
            continue
        try:
            obj = json.loads(json_line)
        except json.JSONDecodeError:
            print(f"[retry {rid}] attempt {attempt}: bad json", flush=True)
            if attempt < MAX_RETRY:
                time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
            continue
        if obj.get("llm_failed") is True or "error" in obj:
            is_429 = ("429" in err) or ("429" in out) or ("rate" in err.lower())
            print(f"[retry {rid}] attempt {attempt}: llm_failed={obj.get('llm_failed')} 429={is_429} err={err[:160]}", flush=True)
            if attempt < MAX_RETRY:
                time.sleep(BACKOFF[min(attempt - 1, len(BACKOFF) - 1)])
            continue
        obj["report"] = rid
        return obj
    return None


def main() -> int:
    ids = load_ids()
    done = completed_from_log()
    print(f"[backfill] total={len(ids)} already_done={len(done)}", flush=True)
    log_fh = LOG_FILE.open("a", encoding="utf-8")
    try:
        for rid in ids:
            if rid in done:
                print(f"[skip] {rid} already complete", flush=True)
                continue
            print(f"[run] {rid} ...", flush=True)
            t0 = time.time()
            obj = run_one(rid)
            if obj is None:
                print(f"[FAIL] {rid} 所有重试失败，跳过", flush=True)
                continue
            dt = round(time.time() - t0, 1)
            print(f"[ok] {rid} avg={obj.get('average')} {dt}s", flush=True)
            log_fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
            log_fh.flush()
            done[rid] = obj
    finally:
        log_fh.close()
    # 全量写出
    all_results = [done[r] for r in ids if r in done]
    OUT_FILE.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] wrote {len(all_results)}/{len(ids)} -> {OUT_FILE.name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
