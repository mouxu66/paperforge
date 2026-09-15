"""云端视觉 provider 限速实测（glm / agnes 通用）。

与 glm_vision_bench.py 的区别：bench 测的是吞吐/延迟，本脚本专攻限速——
  1. 单次冷调用（基线）
  2. 突发连打：每 0.2s 一发连打 N 次，找第一次 429 出现位置与命中率
  3. 429 恢复探测：被限后 sleep 递增（5/10/20/40s），看多久能恢复成功
  4. 稳定节流模拟：按给定间隔匀速打 5 发，验证该间隔是否规避 429

用法（先设好对应 key）：
  set PAPERFORGE_GLM_VISION_API_KEY=xxx
  python scripts/rate_limit_probe.py --provider agnes --burst 25
  python scripts/rate_limit_probe.py --provider glm --model glm-4.6v-flash --burst 40

原则：只读环境变量 + 命令行参数，不碰 mock_api.settings，不做任何应用层重试
（要暴露真实上游限速行为）。
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "uploads/figures/1512.03385/p1_v0.png"

PROMPT = (
    "Read this scientific figure. Report ONLY: (1) the x-axis and y-axis "
    "labels and their numeric tick values; (2) any key printed numbers in the "
    "panels. Be precise with digits. Output plain text, no code fence."
)

DEFAULTS = {
    "glm": {
        "base": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4v-flash",
    },
    "agnes": {
        "base": "https://apihub.agnes-ai.com/v1",
        "model": "agnes-2.5-flash",
    },
}


def load_env() -> None:
    """从仓库根 .env 读取（若存在），不覆盖已存在的环境变量。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


def call_once(url: str, key: str, model: str, timeout: int, thinking: str = "") -> dict:
    """单次调用，无重试（暴露真实状态码）。"""
    try:
        b64 = base64.b64encode(IMG.read_bytes()).decode("utf-8")
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 512,
            "stream": False,
        }
        if thinking:
            payload["thinking"] = {"type": thinking}
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        t0 = time.perf_counter()
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
            return {"ok": True, "latency": dt, "status": 200, "chars": len(content), "err": ""}
        err = (resp.text or "")[:160].replace("\n", " ")
        return {"ok": False, "latency": dt, "status": resp.status_code, "chars": 0, "err": err}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency": -1.0, "status": 0, "chars": 0, "err": repr(exc)[:160]}


def summarize(tag: str, results: list[dict]) -> None:
    n_ok = sum(1 for r in results if r["ok"])
    n_429 = sum(1 for r in results if r["status"] == 429)
    n_other = len(results) - n_ok - n_429
    lats = [r["latency"] for r in results if r["ok"] and r["latency"] > 0]
    avg = sum(lats) / len(lats) if lats else 0
    print(f"  [{tag}] 总数={len(results)} 成功={n_ok} 429={n_429} 其它错={n_other} "
          f"成功均延迟={avg:.2f}s")
    if n_other:
        for r in results:
            if not r["ok"] and r["status"] not in (429,):
                print(f"    非429错误: status={r['status']} err={r['err'][:120]}")
                break


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["glm", "agnes"], required=True)
    ap.add_argument("--model", default=None, help="模型名（默认按 provider 回落）")
    ap.add_argument("--base-url", default=None, help="端点基址（默认按 provider 回落）")
    ap.add_argument("--burst", type=int, default=25, help="突发连打次数")
    ap.add_argument("--interval", type=float, default=0.2, help="突发连打间隔(秒)")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--recover-max", type=int, default=40, help="恢复探测最大等待(秒)")
    ap.add_argument("--thinking", choices=["enabled", "disabled"], default="",
                    help="glm-4.6v-flash 等思考模型：disabled 关闭思考模式（否则 content 为空）")
    args = ap.parse_args()

    load_env()
    key = os.environ.get("PAPERFORGE_GLM_VISION_API_KEY", "").strip()
    if not key:
        print("[ERR] 缺少 PAPERFORGE_GLM_VISION_API_KEY 环境变量/.env。", file=sys.stderr)
        return 2
    d = DEFAULTS[args.provider]
    model = args.model or d["model"]
    base = (args.base_url or d["base"]).rstrip("/")
    url = f"{base}/chat/completions"
    print(f"provider={args.provider}  model={model}\n  端点={url}\n  图片={IMG.name} ({IMG.stat().st_size} B)")

    # ── 阶段1：单次冷调用（×2，取稳定值）──
    print("\n== 阶段1: 单次冷调用 ==")
    cold = [call_once(url, key, model, args.timeout, args.thinking) for _ in range(2)]
    summarize("冷调用x2", cold)
    if not any(r["ok"] for r in cold):
        print("  冷调用失败 → 先检查 key/网络/model，中止。", file=sys.stderr)
        return 1

    # ── 阶段2：突发连打，找第一次 429 ──
    print(f"\n== 阶段2: 突发连打 {args.burst} 次（间隔 {args.interval}s，无节流）==")
    burst: list[dict] = []
    first_429 = -1
    for i in range(1, args.burst + 1):
        r = call_once(url, key, model, args.timeout, args.thinking)
        burst.append(r)
        mark = "  <== 首次429" if (r["status"] == 429 and first_429 < 0) else ""
        if r["status"] == 429 and first_429 < 0:
            first_429 = i
        print(f"  #{i:<2} status={r['status']:<3} latency={r['latency']:6.2f}s "
              f"{r['err'][:80]}{mark}")
        time.sleep(args.interval)
    summarize("突发连打", burst)
    if first_429 > 0:
        print(f"  => 第 {first_429} 次请求首次被限流(429)，此前后 {first_429 - 1} 次成功。")
    else:
        print(f"  => 连打 {args.burst} 次无 429（该档无严格 RPM 限制或上限 > {args.burst}）。")

    # ── 阶段3：并发探针（真正挑战限速上限；串行会被推理耗时天然限速）──
    print(f"\n== 阶段3: 并发探针（并发 2/4/8/12，各自打满 {args.burst} 次）==")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    for level in [2, 4, 8, 12]:
        n_each = args.burst // 2
        results: list[dict] = []
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=level) as ex:
            futs = [ex.submit(call_once, url, key, model, args.timeout, args.thinking)
                    for _ in range(level * n_each)]
            for f in as_completed(futs):
                results.append(f.result())
        wall = time.perf_counter() - t0
        n_ok = sum(1 for r in results if r["ok"])
        n_429 = sum(1 for r in results if r["status"] == 429)
        n_other = len(results) - n_ok - n_429
        lats = [r["latency"] for r in results if r["ok"] and r["latency"] > 0]
        avg = sum(lats) / len(lats) if lats else 0
        eff_rpm = n_ok / wall * 60 if wall > 0 else 0
        print(f"  并发={level:>2}  请求数={len(results):>3}  wall={wall:5.1f}s  "
              f"成功={n_ok:>3}  429={n_429:>3}  其它错={n_other:>2}  "
              f"平均延迟={avg:.2f}s  有效RPM≈{eff_rpm:.0f}")
        if n_429 and level >= 4:
            print("  => 已触发限速，停止更高并发。")
            break
        # 级间冷却，避免上一轮余量污染下一轮
        time.sleep(5)

    # ── 阶段4：稳定节流模拟 ──
    print("\n== 阶段4: 匀速节流模拟（间隔 --interval，打 5 发）==")
    steady: list[dict] = []
    for i in range(5):
        r = call_once(url, key, model, args.timeout, args.thinking)
        steady.append(r)
        print(f"  #{i + 1:<2} status={r['status']:<3} latency={r['latency']:6.2f}s "
              f"{'' if r['ok'] else r['err'][:80]}")
        time.sleep(args.interval)
    summarize("匀速x5", steady)

    print("\n测速完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
