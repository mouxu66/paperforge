"""测量 llama-server 的 prompt-cache / KV-reuse 对 DEPTH 节点的提速效果。

用法:
  .venv/Scripts/python.exe -X utf8 scripts/calibration/bench_cache_reuse.py [port]

三组探针:
  [grouped] 同一节点模板 + 不同论文 (连续同前缀) —— cache-reuse 最理想场景
  [chained] paper1 走 Q0→Q1→QE 不同前缀, 再 paper2 的 Q0 —— 模拟 DEPTH 真实逐篇派发
  [sanity]  完全相同 prompt 重复 —— 验证 exact-match 缓存

关键读取: 响应 timings.cache_n = 从缓存复用的前缀 token 数。
  cache_n>0 且 prompt_ms 下降 => 前缀复用生效
"""
import json
import time
import sys
import statistics
import requests

PORT = sys.argv[1] if len(sys.argv) > 1 else "8080"
URL = f"http://127.0.0.1:{PORT}/v1/chat/completions"
MODEL = "D:/ornstein-v2-Q4_K_M.gguf"

PROMPT_Q0 = """[CRITICAL: Output ONLY these 4 lines. NO thinking. NO JSON. Start immediately.]

你是顶会领域主席。阅读以下摘要+引言，给出整体判断。

论文：
{paper}

示例：
reasoning: 提出了全新的理论框架并给出了证明思路。
evidence: 首次将X与Y统一在一个框架中
has_substance: true
expectation: 0.75"""

PROMPT_Q1 = """[CRITICAL: Output ONLY these 5 lines. NO thinking. NO JSON. Start immediately.]

你是资深审稿人。判断论文主类型和辅类型。A=理论突破 B=方法改进 C=应用迁移 D=综述。secondary_type 填 A/B/C/D 或 none。

论文：
{paper}

示例：
reasoning: 提出新损失函数并从理论上证明了收敛性，同时应用于医学图像分割。
evidence: 证明了该损失的全局收敛性
type: A
secondary_type: C
confidence: 0.88"""

PROMPT_QE = """从以下论文中提取5-7条关键证据。每条证据单独一行，格式为"E编号: 证据内容"。

不要输出 JSON、不要输出思考过程、不要输出解释。直接列出证据。

论文：
{paper}

示例输出：
E1: 我们提出了一种基于注意力机制的新型网络架构Transformer。
E2: 该模型在WMT 2014英德翻译任务上达到28.4 BLEU分数。
E3: Transformer的训练时间远少于RNN/CNN模型。"""

BODY_FILES = [
    "calib_papers/2606.18205.txt",
    "calib_papers/2606.21847.txt",
    "calib_papers/2606.22429.txt",
    "calib_papers/2606.24099.txt",
]
PAPER_TRUNC = 4000  # DEPTH MAX_CHARS_SHORT
MAX_TOKENS = 120


def load_bodies():
    out = []
    for fn in BODY_FILES:
        try:
            with open(fn, encoding="utf-8", errors="ignore") as f:
                out.append(f.read()[:PAPER_TRUNC])
        except FileNotFoundError:
            pass
    return out


def chat(prompt):
    t0 = time.perf_counter()
    r = requests.post(
        URL,
        json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
              "max_tokens": MAX_TOKENS, "temperature": 0.1},
        timeout=300,
    )
    el = time.perf_counter() - t0
    j = r.json()
    t = j.get("timings", {}) or {}
    return {"wall": el, "prompt_n": t.get("prompt_n"), "cache_n": t.get("cache_n"),
            "prompt_ms": t.get("prompt_ms"), "prompt_per_s": t.get("prompt_per_second")}


def med(xs):
    return statistics.median(xs) if xs else None


print(f"=== bench_cache_reuse @ {URL} ===")
bodies = load_bodies()
print(f"loaded {len(bodies)} paper bodies (trunc={PAPER_TRUNC})\n")

# ---- [grouped] 同节点连续不同论文 ----
print("--- [grouped] 节点 Q0: 同模板连续不同论文 (cache-reuse 理想场景) ---")
cold = None
warm_cache, warm_speed = [], []
for i, b in enumerate(bodies):
    res = chat(PROMPT_Q0.format(paper=b))
    tag = "COLD" if i == 0 else "warm"
    print(f"  {tag} paper{i+1}: prompt_n={res['prompt_n']} cache_n={res['cache_n']} "
          f"prompt_ms={res['prompt_ms']:.1f} wall={res['wall']:.1f}s")
    if i == 0:
        cold = res
    else:
        if res["cache_n"]:
            warm_cache.append(res["cache_n"])
        if res["prompt_ms"] and cold["prompt_ms"]:
            warm_speed.append(cold["prompt_ms"] / res["prompt_ms"])
if cold and warm_cache:
    frac = med(warm_cache) / cold["prompt_n"]
    print(f"  >> 跨论文前缀复用 ≈ {med(warm_cache)}/{cold['prompt_n']} ({100*frac:.1f}% of prompt); "
          f"prompt 阶段提速 ≈ {100*(med(warm_speed)-1):.1f}%\n")
else:
    print("  >> 跨论文前缀复用: NONE (cache_n 始终为 0)\n")

# ---- [chained] 模拟 DEPTH 真实逐篇派发 ----
print("--- [chained] paper1: Q0→Q1→QE (不同前缀), 再 paper2 的 Q0 ---")
b1, b2 = bodies[0], bodies[1]
for nm, tmpl in [("Q0", PROMPT_Q0), ("Q1", PROMPT_Q1), ("QE", PROMPT_QE)]:
    r = chat(tmpl.format(paper=b1))
    print(f"  paper1 {nm}: prompt_n={r['prompt_n']} cache_n={r['cache_n']} prompt_ms={r['prompt_ms']:.1f}")
r2 = chat(PROMPT_Q0.format(paper=b2))
print(f"  paper2 Q0: prompt_n={r2['prompt_n']} cache_n={r2['cache_n']} prompt_ms={r2['prompt_ms']:.1f} "
      f"wall={r2['wall']:.1f}s")
print(f"  >> 真实逐篇派发下 paper2 的 Q0 前缀复用: {'YES ('+str(r2['cache_n'])+' tok)' if r2['cache_n'] else 'NONE'}\n")

# ---- [sanity] ----
print("--- [sanity] 完全相同 prompt 重复 ---")
p = PROMPT_Q0.format(paper=bodies[0])
r1, r2 = chat(p), chat(p)
print(f"  run1: prompt_n={r1['prompt_n']} cache_n={r1['cache_n']} prompt_ms={r1['prompt_ms']:.1f}")
print(f"  run2: prompt_n={r2['prompt_n']} cache_n={r2['cache_n']} prompt_ms={r2['prompt_ms']:.1f}")
print(f"  >> exact 复用: {'YES' if r2['cache_n'] and r2['cache_n'] >= (r2['prompt_n'] or 0) else 'NO'}")
