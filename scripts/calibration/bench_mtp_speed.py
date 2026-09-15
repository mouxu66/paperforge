import json, time, sys, statistics
import requests

URL = "http://127.0.0.1:8080/v1/completions"

# 真实长论文作 prefill 负载（截断到 ~26000 字符，约数千 token，贴近 rescore 实际）
with open("calib_papers/2606.18205.txt", "r", encoding="utf-8", errors="ignore") as f:
    paper = f.read()[:26000]

PROMPT = (
    "You are an expert academic peer reviewer. Read the following paper and output a JSON object "
    "with keys: score (float 0-1), verdict (one of accept/weak_accept/minor_revision/major_revision/reject), "
    "human_accept (bool), rationale (string, <=120 words). Respond with JSON only.\n\n"
    "=== PAPER ===\n" + paper + "\n=== END ==="
)

MAX_TOKENS = 512
TEMP = 0.1
N = 4  # 正式轮数
WARMUP = 1

def run_once():
    t0 = time.perf_counter()
    r = requests.post(URL, json={
        "prompt": PROMPT, "max_tokens": MAX_TOKENS, "temperature": TEMP,
        "top_p": 1.0, "n": 1, "stream": False,
    }, timeout=300)
    el = time.perf_counter() - t0
    j = r.json()
    usage = j.get("usage", {})
    comp = usage.get("completion_tokens", 0)
    timings = j.get("timings", {}) or {}
    return {
        "wall_s": el,
        "comp_tokens": comp,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "pred_per_s": timings.get("predicted_per_second"),
        "prompt_per_s": timings.get("prompt_per_second"),
        "draft_accept": timings.get("draft_accepted_tokens"),
        "draft_total": timings.get("draft_n_tokens"),
        "n_draft": timings.get("n_draft"),
    }

print(f"warmup...", flush=True)
run_once()
rows = []
for i in range(N):
    r = run_once()
    rows.append(r)
    print(f"  run{i+1}: comp={r['comp_tokens']} wall={r['wall_s']:.1f}s "
          f"decode={r['pred_per_s']:.2f} tok/s prefill={r['prompt_per_s']:.1f} tok/s "
          f"accept={r['draft_accept']}/{r['draft_total']}", flush=True)

def med(xs):
    return statistics.median(xs) if xs else None

decode = [r["pred_per_s"] for r in rows if r["pred_per_s"]]
pf = [r["prompt_per_s"] for r in rows if r["prompt_per_s"]]
wall = [r["comp_tokens"]/r["wall_s"] for r in rows]
acc = [r["draft_accept"] for r in rows if r["draft_accept"] is not None]
tot = [r["draft_total"] for r in rows if r["draft_total"]]

print("\n=== 汇总 ===", flush=True)
print(f"decode tok/s (median): {med(decode):.2f}" if decode else "decode tok/s: n/a")
print(f"prefill tok/s (median): {med(pf):.1f}" if pf else "prefill tok/s: n/a")
print(f"overall tok/s (comp/wall, median): {med(wall):.2f}")
if acc and tot:
    print(f"MTP draft accept: {sum(acc)}/{sum(tot)} = {100*sum(acc)/max(sum(tot),1):.1f}%")
print(f"prompt_tokens≈{rows[0]['prompt_tokens']} comp_tokens≈{rows[0]['comp_tokens']}", flush=True)
