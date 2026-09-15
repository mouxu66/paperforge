"""纯文本模型（glm-4.7-flash 等）限速实测。

与 rate_limit_probe.py（视觉）不同：纯文本模型不读图，payload 仅含文本。
专为"统筹/裁决"层（如 glm-4.7-flash）测限速：并发探针 + 突发连打。

用法：
  set PAPERFORGE_GLM_VISION_API_KEY=xxx
  python scripts/rate_limit_probe_text.py --model glm-4.7-flash --burst 30
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PROMPT = (
    "Read the following figure audit signals and decide which figures need "
    "visual re-scan. Output JSON with a 'needs_rescan' list. Signals: "
    "fig1 axis ticks [0,1,2,3], fig2 OCR says '10%' but image shows '1%', "
    "fig3 legend color clash. Be precise."
)


def load_env() -> None:
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


def call_once(url: str, key: str, model: str, timeout: int) -> dict:
    try:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "temperature": 0.0,
            "max_tokens": 256,
            "stream": False,
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        t0 = time.perf_counter()
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
            return {"ok": True, "latency": dt, "status": 200, "chars": len(content), "err": ""}
        return {"ok": False, "latency": dt, "status": resp.status_code,
                "chars": 0, "err": (resp.text or "")[:160].replace("\n", " ")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency": -1.0, "status": 0, "chars": 0, "err": repr(exc)[:160]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="glm-4.7-flash")
    ap.add_argument("--base-url", default="https://open.bigmodel.cn/api/paas/v4")
    ap.add_argument("--burst", type=int, default=30)
    ap.add_argument("--interval", type=float, default=0.3)
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    load_env()
    key = os.environ.get("PAPERFORGE_GLM_VISION_API_KEY", "").strip()
    if not key:
        print("[ERR] 缺少 PAPERFORGE_GLM_VISION_API_KEY", file=sys.stderr)
        return 2
    url = f"{args.base_url.rstrip('/')}/chat/completions"
    print(f"model={args.model}\n  端点={url}\n  [纯文本，无图]")

    # 阶段1 冷调用
    print("\n== 阶段1: 单次冷调用 x2 ==")
    cold = [call_once(url, key, args.model, args.timeout) for _ in range(2)]
    n_ok = sum(1 for r in cold if r["ok"])
    lats = [r["latency"] for r in cold if r["ok"] and r["latency"] > 0]
    print(f"  成功={n_ok}/2 平均延迟={sum(lats)/len(lats):.2f}s" if lats else f"  失败: {cold[0]['err'][:120]}")
    if not lats:
        return 1

    # 阶段2 突发连打
    print(f"\n== 阶段2: 突发连打 {args.burst} 次（间隔 {args.interval}s）==")
    burst = []
    first_429 = -1
    for i in range(1, args.burst + 1):
        r = call_once(url, key, args.model, args.timeout)
        burst.append(r)
        if r["status"] == 429 and first_429 < 0:
            first_429 = i
            print(f"  #{i:<2} status=429 <== 首次429")
        elif r["status"] != 200:
            print(f"  #{i:<2} status={r['status']} {r['err'][:80]}")
        time.sleep(args.interval)
    n_ok = sum(1 for r in burst if r["ok"])
    n_429 = sum(1 for r in burst if r["status"] == 429)
    print(f"  => 成功={n_ok} 429={n_429} 首次429位置={'#' + str(first_429) if first_429 > 0 else '无'}")

    # 阶段3 并发探针
    print(f"\n== 阶段3: 并发探针（2/4/8）==")
    for level in [2, 4, 8]:
        results = []
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=level) as ex:
            futs = [ex.submit(call_once, url, key, args.model, args.timeout) for _ in range(level * 6)]
            for f in as_completed(futs):
                results.append(f.result())
        wall = time.perf_counter() - t0
        n_ok = sum(1 for r in results if r["ok"])
        n_429 = sum(1 for r in results if r["status"] == 429)
        eff = n_ok / wall * 60 if wall > 0 else 0
        print(f"  并发={level:>2} 请求={len(results):>2} wall={wall:5.1f}s 成功={n_ok:>2} 429={n_429:>2} 有效RPM≈{eff:.0f}")
        time.sleep(4)

    print("\n纯文本限速实测完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
