"""一次性跑完 llama-bench flag 组合矩阵，对比 decode/prompt_eval 速度。

用法（8080 停止后）:
  .venv/Scripts/python.exe scripts/bench_llama_flags.py

输出: deliverables/llama_bench_matrix.md
"""
import subprocess
import time
from pathlib import Path

BENCH = r"D:/llama-b10357-win/llama-bench.exe"
MODEL = r"D:/Qwen3.5-9B-Q3_K_M.gguf"
OUT = Path(__file__).resolve().parent.parent / "deliverables" / "llama_bench_matrix.md"

# (名称, 额外参数) —— 公共参数: -ngl 35 -c 8192 -ctk q4_0 -ctv q4_0 -p 2048 -n 128
CASES = [
    ("基线(auto fa, b2048/ub512)", []),
    ("fa=on, b2048/ub512", ["-fa", "on"]),
    ("fa=off, b2048/ub512", ["-fa", "off"]),
    ("fa=on, b512/ub512", ["-fa", "on", "-b", "512", "-ub", "512"]),
    ("fa=on, b1024/ub1024", ["-fa", "on", "-b", "1024", "-ub", "1024"]),
    ("fa=on, ub256", ["-fa", "on", "-ub", "256"]),
    ("fa=on, threads=16", ["-fa", "on", "-t", "16"]),
]

COMMON = ["-m", MODEL, "-ngl", "35", "-ctk", "q4_0", "-ctv", "q4_0",
          "-p", "2048", "-n", "128", "-r", "2", "-o", "md"]

def run(case_name: str, extra: list[str]) -> str:
    cmd = [BENCH, *COMMON, *extra]
    print(f"\n===== {case_name} =====", flush=True)
    t0 = time.time()
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        dt = time.time() - t0
        out = (res.stdout or "") + (res.stderr or "")
        # 提取结果表行
        lines = [l for l in out.splitlines() if "|" in l and "pp" in l.lower() or "tg" in l.lower()]
        body = "\n".join(l for l in out.splitlines() if l.strip().startswith("|"))
        print(out[-2000:], flush=True)
        return f"### {case_name} ({dt:.0f}s)\n\n```\n{body}\n```\n\n"
    except subprocess.TimeoutExpired:
        return f"### {case_name}\n\nTIMEOUT\n\n"
    except Exception as e:  # noqa: BLE001
        return f"### {case_name}\n\nERROR: {e}\n\n"

def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    parts = ["# llama-bench flag 矩阵（Qwen3.5-9B Q3_K_M @ RTX 5060 Laptop 8GB）\n",
             f"模型: {MODEL}\n公共: -ngl 35 -c 8192 -ctk q4_0 -ctv q4_0 -p 2048 -n 128 -r 2\n"]
    for name, extra in CASES:
        parts.append(run(name, extra))
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"\n结果已写入 {OUT}")

if __name__ == "__main__":
    main()
