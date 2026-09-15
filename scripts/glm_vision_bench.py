"""GLM 免费视觉模型独立测速脚本（不触碰本地模型配置）。

测速目标：
  1. 单次冷调用延迟（per-image latency）
  2. 串行 N 张的真实 RPM（requests per minute）
  3. 并发探针 [1/2/4/8]：观察 429 出现位置与有效吞吐

设计原则：
  - 永久独立：只读环境变量，不读 mock_api.settings，不改任何本地视觉配置。
  - 本地 Qwen3-VL-4B(8082) 配置原样保留，本脚本与之零耦合。

环境变量：
  PAPERFORGE_GLM_VISION_API_KEY   智谱 API Key（必填）
  PAPERFORGE_GLM_VISION_BASE_URL  默认 https://open.bigmodel.cn/api/paas/v4
  PAPERFORGE_GLM_VISION_MODEL     默认 glm-4v-flash（可选 glm-4.6v-flash）

用法：
  set PAPERFORGE_GLM_VISION_API_KEY=xxx
  python scripts/glm_vision_bench.py --model glm-4v-flash
  python scripts/glm_vision_bench.py --model glm-4.6v-flash --images "uploads/figures/**/*.png"
"""
from __future__ import annotations

import argparse
import base64
import glob
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGES = [
    r"uploads/figures/pr_1612.08810/p4_v0.png",
    r"uploads/figures/pr_1612.08810/p5_v0.png",
    r"uploads/figures/pr_1612.08810/p6_v0.png",
    r"uploads/figures/pr_1612.08810/p8_v0.png",
    r"uploads/figures/pr_1612.08810/p11_v0.png",
    r"uploads/figures/pr_1612.08810/p12_v0.png",
    r"uploads/figures/pr_1702.02171/p7_i4.png",
    r"uploads/figures/pr_1602.02261/p5_i0.png",
]

PROMPT = (
    "Read this scientific figure. Report ONLY: (1) the x-axis and y-axis "
    "labels and their numeric tick values; (2) any key printed numbers in the "
    "panels. Be precise with digits."
)


def collect_images(patterns: list[str]) -> list[str]:
    out: list[str] = []
    for pat in patterns:
        if any(ch in pat for ch in "*?["):
            for p in sorted(glob.glob(str(ROOT / pat))):
                if p not in out:
                    out.append(p)
        else:
            p = str(ROOT / pat)
            if p not in out:
                out.append(p)
    return [p for p in out if os.path.exists(p)]


def call_once(api_url: str, key: str, model: str, img_path: str, timeout: int) -> dict:
    """单次调用，返回延迟/状态/错误。不做重试（为暴露真实 429）。"""
    try:
        b64 = base64.b64encode(Path(img_path).read_bytes()).decode("utf-8")
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
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        t0 = time.perf_counter()
        resp = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
        dt = time.perf_counter() - t0
        if resp.status_code == 200:
            content = (resp.json()["choices"][0]["message"].get("content") or "").strip()
            return {"ok": True, "latency": dt, "status": 200, "chars": len(content)}
        return {"ok": False, "latency": dt, "status": resp.status_code, "err": resp.text[:200]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency": -1.0, "status": 0, "err": repr(exc)[:200]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("PAPERFORGE_GLM_VISION_MODEL", "glm-4v-flash"))
    ap.add_argument("--images", nargs="*", default=DEFAULT_IMAGES,
                    help="图片路径或 glob，默认 8 张内置样本")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--max-concur", type=int, default=8)
    args = ap.parse_args()

    key = os.environ.get("PAPERFORGE_GLM_VISION_API_KEY")
    if not key:
        print("[ERR] 缺少 PAPERFORGE_GLM_VISION_API_KEY 环境变量。请先 set 你的智谱 API Key。", file=sys.stderr)
        return 2
    base_url = os.environ.get("PAPERFORGE_GLM_VISION_BASE_URL",
                              "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
    api_url = f"{base_url}/chat/completions"

    imgs = collect_images(args.images)
    if not imgs:
        print("[ERR] 没有找到任何测试图片。", file=sys.stderr)
        return 2
    print(f"模型={args.model}  图片数={len(imgs)}  端点={api_url}\n")

    # ── 阶段 1：单次冷调用延迟 ──
    print("== 阶段1: 单次冷调用（各图一发，取均值） ==")
    cold = []
    for p in imgs[: min(3, len(imgs))]:
        r = call_once(api_url, key, args.model, p, args.timeout)
        cold.append(r)
        print(f"  {Path(p).name:18s} status={r['status']} latency={r['latency']:.2f}s "
              f"{'' if r['ok'] else 'ERR=' + r.get('err','')}")
    ok_cold = [r["latency"] for r in cold if r["ok"] and r["latency"] > 0]
    if ok_cold:
        print(f"  => 平均单图延迟 ≈ {sum(ok_cold)/len(ok_cold):.2f}s\n")
    else:
        print("  => 冷调用全部失败，请检查 key/网络/model。\n")

    # ── 阶段 2：串行 RPM ──
    print("== 阶段2: 串行 N 张真实 RPM（无并发） ==")
    t0 = time.perf_counter()
    serial_ok = 0
    for p in imgs:
        r = call_once(api_url, key, args.model, p, args.timeout)
        if r["ok"]:
            serial_ok += 1
    serial_dt = time.perf_counter() - t0
    rpm = serial_ok / serial_dt * 60 if serial_dt > 0 else 0
    print(f"  {serial_ok}/{len(imgs)} 成功  耗时 {serial_dt:.1f}s  => 真实 RPM ≈ {rpm:.1f}\n")

    # ── 阶段 3：并发探针 ──
    print(f"== 阶段3: 并发探针（上限 --max-concur={args.max_concur}）==")
    for level in [1, 2, 4, 8]:
        if level > args.max_concur:
            break
        sem = __import__("threading").Semaphore(level)
        results = []
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=level) as ex:
            futs = []
            for p in imgs:
                def _w(p=p):
                    with sem:
                        return call_once(api_url, key, args.model, p, args.timeout)
                futs.append(ex.submit(_w))
            for f in as_completed(futs):
                results.append(f.result())
        wall = time.perf_counter() - t0
        n_ok = sum(1 for r in results if r["ok"])
        n_429 = sum(1 for r in results if r["status"] == 429)
        n_other = len(results) - n_ok - n_429
        lats = [r["latency"] for r in results if r["ok"] and r["latency"] > 0]
        avg = sum(lats) / len(lats) if lats else 0
        eff_rpm = n_ok / wall * 60 if wall > 0 else 0
        print(f"  并发={level:>2}  wall={wall:5.1f}s  成功={n_ok:>2}  429={n_429:>2}  "
              f"其它错={n_other:>2}  平均延迟={avg:.2f}s  有效RPM≈{eff_rpm:.1f}")

    print("\n测速完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
