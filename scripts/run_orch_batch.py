"""批量运行三层视觉审计编排（智谱上游）。

串行跑每篇，单篇失败/超时不影响整体；统一日志汇总。
环境变量已在脚本内固化（覆盖 .env 的 agnes 设置 + 智谱 key/base_url）。
"""
import os
import sys
import time
import subprocess

os.environ.setdefault("PYTHONUTF8", "1")
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PAPERFORGE_ORCH_LOCAL"] = "1"
os.environ["PAPERFORGE_GLM_VISION_ENABLED"] = "1"
os.environ["PAPERFORGE_GLM_VISION_PROVIDER"] = "glm"
os.environ["PAPERFORGE_GLM_VISION_API_KEY"] = "3fa27087485a44bc9ccc4ae652217ffc.F47ISVRvVZX3EjXd"
os.environ["PAPERFORGE_GLM_VISION_MODEL"] = "glm-4v-flash"
os.environ["PAPERFORGE_GLM_VISION_BASE_URL"] = "https://open.bigmodel.cn/api/paas/v4"
os.environ["PAPERFORGE_GLM_VISION_MAX_CONCUR"] = "8"
os.environ["PAPERFORGE_GLM_TEXT_API_KEY"] = "3fa27087485a44bc9ccc4ae652217ffc.F47ISVRvVZX3EjXd"
os.environ["PAPERFORGE_GLM_TEXT_BASE_URL"] = "https://open.bigmodel.cn/api/paas/v4"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.path.join(ROOT, "scripts", "runs")
os.makedirs(RUNS, exist_ok=True)

PAPERS = [
    "pr_1506.03340", "pr_1502.02367", "pr_1702.02171", "pr_1002.3320",
    "pr_1306.2119", "pr_1612.08810", "pr_1105.1247", "pr_1006.0153",
    "pr_0911.3209", "pr_1005.4446", "pr_1609.06038", "pr_1002.2897",
    "pr_1103.3240", "pr_1307.0060", "pr_1611.01587", "fraud_berberine",
    "kjpp", "pr_1001.2155", "pr_1011.5349", "pr_1106.2662", "pr_1102.2749",
]

PER_TIMEOUT = 480  # 单篇最多 8 分钟
GAP = 8  # 篇间间隔秒，给 4.7 喘息

summary_path = os.path.join(RUNS, "batch_summary.log")
t0 = time.time()
ok, fail, skipped = [], [], []

with open(summary_path, "w", encoding="utf-8") as sumf:
    sumf.write(f"批量审计启动 {time.strftime('%Y-%m-%d %H:%M:%S')}  共 {len(PAPERS)} 篇\n")
    for i, pid in enumerate(PAPERS, 1):
        logp = os.path.join(RUNS, f"orch_{pid}.log")
        sumf.write(f"\n[{i}/{len(PAPERS)}] {pid} -> {logp}\n")
        sumf.flush()
        try:
            with open(logp, "w", encoding="utf-8") as lf:
                proc = subprocess.Popen(
                    [sys.executable, "-u", "scripts/run_glm_orchestrate.py", pid],
                    cwd=ROOT, env=os.environ, stdout=lf, stderr=subprocess.STDOUT,
                )
                try:
                    proc.wait(timeout=PER_TIMEOUT)
                    rc = proc.returncode
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = -999
            # 读末行判定结果
            tail = ""
            if os.path.exists(logp):
                with open(logp, encoding="utf-8", errors="replace") as f:
                    lines = [l for l in f.read().splitlines() if l.strip()]
                    tail = lines[-3:] if lines else []
            if rc == 0 and tail:
                ok.append(pid)
                sumf.write(f"  OK rc=0 末行: {tail[-1][:160]}\n")
            elif rc == -999:
                fail.append((pid, "TIMEOUT"))
                sumf.write(f"  TIMEOUT (> {PER_TIMEOUT}s)  killed\n")
            else:
                fail.append((pid, f"rc={rc}"))
                sumf.write(f"  FAIL rc={rc} 末行: {tail[-1][:160] if tail else ''}\n")
        except Exception as e:
            fail.append((pid, repr(e)))
            sumf.write(f"  ERROR {e!r}\n")
        sumf.flush()
        if i < len(PAPERS):
            time.sleep(GAP)

    elapsed = time.time() - t0
    sumf.write(f"\n==== 完成 耗时 {elapsed/60:.1f}min OK={len(ok)} FAIL={len(fail)} ====\n")
    sumf.write("OK: " + ", ".join(ok) + "\n")
    sumf.write("FAIL: " + ", ".join(f"{p}:{r}" for p, r in fail) + "\n")

print(f"BATCH DONE OK={len(ok)} FAIL={len(fail)} elapsed={elapsed/60:.1f}min -> {summary_path}")
