import time, json, urllib.request, sys, statistics

# 速度基准：固定长生成预算(600 token)隔离 prefill 固定开销，多轮取均值 + 中位数。
# 用法：python _bench_multi.py [N] [label]
#   第 1 次调用为 warmup（丢弃，吸收 CUDA 编译/冷启动），随后 N 次实测。
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
LABEL = sys.argv[2] if len(sys.argv) > 2 else "bench"

PROMPT = ("请详细、系统地写一篇关于深度神经网络训练方法的科普长文，覆盖：数据预处理、"
          "前向传播、损失函数、反向传播、优化器（SGD/Adam）、正则化、学习率调度。"
          "要求每个部分用 2-3 段展开，内容专业且具体。")

def one_call():
    payload = json.dumps({"model": "local", "prompt": PROMPT,
                          "max_tokens": 600, "temperature": 0.7, "top_p": 0.9}).encode()
    req = urllib.request.Request("http://127.0.0.1:8080/v1/completions", data=payload,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.loads(r.read())
    elapsed = time.time() - t0
    comp = data.get("usage", {}).get("completion_tokens")
    return comp, elapsed

# warmup
print(f"[{LABEL}] warmup...", flush=True)
one_call()
print(f"[{LABEL}] warmup done, measuring {N} runs", flush=True)

tps_list = []
for i in range(N):
    comp, elapsed = one_call()
    tps = comp / elapsed
    tps_list.append(tps)
    print(f"[{LABEL}] run {i+1}: tokens={comp} elapsed={elapsed:.2f}s tok/s={tps:.1f}", flush=True)

mean = statistics.mean(tps_list)
median = statistics.median(tps_list)
mn, mx = min(tps_list), max(tps_list)
# 去掉一个最低+一个最高（若有）做 trimmed mean
trim = sorted(tps_list)
if len(trim) > 2:
    trim = trim[1:-1]
trimmed = statistics.mean(trim) if trim else mean
print(f"=== [{LABEL}] mean={mean:.1f} median={median:.1f} trimmed={trimmed:.1f} "
      f"(min {mn:.1f} / max {mx:.1f}) ===", flush=True)
