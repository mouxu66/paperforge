"""Speculative-decoding benchmark for a llama-server instance.

Usage:
    python scripts/_spec_bench.py [URL] [LABEL]

Measures generation throughput (tokens/sec) from the server's own `timings`
field, which is the metric speculative decoding directly accelerates.
Runs a warmup + N timed passes over a few prompt archetypes (code / structured
review / prose) and prints per-prompt and aggregate tok/s.
"""

import json
import sys
import time
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080/v1/chat/completions"
LABEL = sys.argv[2] if len(sys.argv) > 2 else "baseline"
N_TIMED = 2  # timed passes per prompt (after 1 warmup)
MAX_TOKENS = 400

PROMPTS = [
    (
        "code",
        "Write a Python function that implements binary search on a sorted list, "
        "with a docstring and type hints. Output only the code, no explanation.",
    ),
    (
        "review",
        "You are a strict paper reviewer. Output EXACTLY these lines and nothing else:\n"
        "novelty_score: <a number 0 to 1>\n"
        "rigor_score: <a number 0 to 1>\n"
        "influence_score: <a number 0 to 1>\n"
        "evidence_id: <one of E1 E2 E3 or none>\n"
        "verdict: <one of accept minor_revision major_revision reject>\n"
        "reasoning: <one concise sentence>",
    ),
    (
        "prose",
        "Explain in about 150 words how self-attention works in a transformer model.",
    ),
]


def one_call(prompt: str) -> dict:
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "cache_prompt": False,
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        URL, data=data, headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.load(r)
    wall = time.time() - t0
    tim = resp.get("timings", {}) or {}
    return {
        "pred_n": tim.get("predicted_n"),
        "pred_tps": tim.get("predicted_per_second"),
        "prompt_tps": tim.get("prompt_per_second"),
        "wall": wall,
    }


def main() -> None:
    print(f"### BENCH label={LABEL} url={URL}")
    all_tps = []
    for name, prompt in PROMPTS:
        # warmup (discarded)
        try:
            one_call(prompt)
        except Exception as e:  # noqa: BLE001
            print(f"  [{name}] WARMUP FAILED: {type(e).__name__}: {e}")
            continue
        samples = []
        for _ in range(N_TIMED):
            try:
                samples.append(one_call(prompt))
            except Exception as e:  # noqa: BLE001
                print(f"  [{name}] RUN FAILED: {type(e).__name__}: {e}")
        if not samples:
            continue
        avg_tps = sum(s["pred_tps"] for s in samples if s["pred_tps"]) / len(samples)
        avg_n = sum(s["pred_n"] for s in samples if s["pred_n"]) / len(samples)
        all_tps.append(avg_tps)
        print(
            f"  [{name:6s}] gen {avg_tps:6.2f} tok/s  (~{avg_n:.0f} tokens/call, "
            f"n={len(samples)})"
        )
    if all_tps:
        print(f"  >>> MEAN gen throughput = {sum(all_tps)/len(all_tps):6.2f} tok/s")


if __name__ == "__main__":
    main()
