#!/usr/bin/env python3
"""测试不同 llama-server 构建版本 / GGUF 模型组合下的中文输出。

用法（在 PaperForge 项目根目录执行）：
    python scripts/test_llama_mojibake.py

结果会写入 deliverables/llama_mojibake_test.json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

TEST_PROMPT = "用中文回答：你好，世界。"

COMBINATIONS = [
    {
        "name": "b9660_Q4_K_M",
        "exe": r"D:/llama-b9660-bin-win-cuda-13.3-x64/llama-server.exe",
        "model": r"D:/llama-b9660-bin-win-cuda-13.3-x64/Qwen3.5-9B-Q4_K_M.gguf",
    },
    {
        "name": "b8833_Q4_K_M",
        "exe": r"D:/llama-b8833-bin-win-cuda-13.1-x64/llama-server.exe",
        "model": r"D:/llama-b8833-bin-win-cuda-13.1-x64/Qwen3.5-9B-Q4_K_M.gguf",
    },
    {
        "name": "b10068_Q4_K_M",
        "exe": r"D:/llama-b10068-bin-win-cuda-13.3-x64/llama-server.exe",
        "model": r"D:/llama-b9660-bin-win-cuda-13.3-x64/Qwen3.5-9B-Q4_K_M.gguf",
    },
    {
        "name": "b9660_Q3_K_M",
        "exe": r"D:/llama-b9660-bin-win-cuda-13.3-x64/llama-server.exe",
        "model": r"D:/Qwen3.5-9B-Q3_K_M.gguf",
    },
    {
        "name": "b9878_Q5_K_M_dflash",
        "exe": r"D:/llama-b9878-bin-win-cuda-13.3-x64/llama-server.exe",
        "model": r"D:/llama-b9878-bin-win-cuda-13.3-x64/qwen3.5-9b-dflash-Q5_K_M.gguf",
    },
]

PORT = 18080  # 使用独立端口，避免与 PaperForge 托管的 8080 冲突


def _kill_port_occupier(port: int) -> None:
    """终止占用指定端口的进程（仅限 Windows）。"""
    if sys.platform != "win32":
        return
    try:
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        pids = set()
        for line in out.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                if parts:
                    pids.add(parts[-1])
        for pid in pids:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", pid],
                capture_output=True,
                timeout=10,
            )
    except Exception:
        pass


def _wait_for_health(port: int, timeout: int = 360) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def _test_combination(combo: dict) -> dict:
    name = combo["name"]
    exe = combo["exe"]
    model = combo["model"]
    print(f"\n=== Testing {name} ===")
    print(f"  exe={exe}\n  model={model}")

    if not os.path.exists(exe):
        return {"name": name, "status": "skipped", "reason": f"exe not found: {exe}"}
    if not os.path.exists(model):
        return {"name": name, "status": "skipped", "reason": f"model not found: {model}"}

    _kill_port_occupier(PORT)
    time.sleep(2)

    cmd = [
        exe,
        "-m", model,
        "--port", str(PORT),
        "--host", "0.0.0.0",
        "-ngl", "35",
        "-c", "8192",
        "--parallel", "1",
        "-ctk", "q4_0",
        "-ctv", "q4_0",
        "--jinja",
        "--reasoning", "off",
        "--temp", "0.1",
        "--top-p", "1.0",
        "--repeat-penalty", "1.05",
        "--samplers", "top_k;temp;penalties",
        "-fit", "off",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    ready = _wait_for_health(PORT, timeout=360)
    if not ready:
        proc.terminate()
        return {"name": name, "status": "failed", "reason": "llama-server did not become ready within 360s"}

    payload = json.dumps({
        "model": "qwen3.5-9b",
        "messages": [
            {"role": "user", "content": TEST_PROMPT},
        ],
        "max_tokens": 100,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        try:
            data = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as e:
            return {"name": name, "status": "raw_decode_error", "error": str(e), "raw_prefix": raw[:80].hex()}
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        return {"name": name, "status": "request_failed", "reason": str(e)}
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    return {
        "name": name,
        "status": "ok",
        "content": content,
        "content_repr": repr(content),
        "has_cjk": any("\u4e00" <= ch <= "\u9fff" for ch in content),
    }


def main() -> int:
    results = []
    for combo in COMBINATIONS:
        results.append(_test_combination(combo))
        time.sleep(2)

    out_path = Path("deliverables/llama_mojibake_test.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
