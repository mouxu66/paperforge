"""补跑/重试单篇审计（带进程级重试，绕过 Windows native crash 0xC000000A）。

用法：python scripts/run_orch_retry.py <paper_id> [<paper_id> ...]
或带 --auto-fail 自动读取 batch_summary.log 里的 FAIL 篇并重跑。
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

MAX_TRIES = 3
PER_TIMEOUT = 480


def collect_auto_fail():
    sp = os.path.join(RUNS, "batch_summary.log")
    out = []
    if os.path.exists(sp):
        for line in open(sp, encoding="utf-8", errors="replace"):
            if line.strip().startswith("FAIL:"):
                for part in line.split("FAIL:")[1].split(","):
                    pid = part.split(":")[0].strip()
                    if pid:
                        out.append(pid)
    return out


def run_one(pid):
    logp = os.path.join(RUNS, f"orch_{pid}.log")
    for attempt in range(1, MAX_TRIES + 1):
        print(f"  [{pid}] attempt {attempt}/{MAX_TRIES} ...", flush=True)
        with open(logp, "w", encoding="utf-8") as lf:
            proc = subprocess.Popen(
                [sys.executable, "-u", "scripts/run_glm_orchestrate.py", pid],
                cwd=ROOT, env=os.environ, stdout=lf, stderr=subprocess.STDOUT,
            )
            try:
                rc = proc.wait(timeout=PER_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = -999
        if rc == 0:
            return True
        print(f"  [{pid}] attempt {attempt} rc={rc}, retrying...", flush=True)
        time.sleep(5)
    return False


def main():
    args = sys.argv[1:]
    if args and args[0] == "--auto-fail":
        papers = collect_auto_fail()
        print("AUTO-FAIL papers:", papers)
    else:
        papers = args
    if not papers:
        print("usage: python scripts/run_orch_retry.py <pid> ...  | --auto-fail")
        return
    results = []
    for pid in papers:
        ok = run_one(pid)
        results.append((pid, ok))
        time.sleep(8)
    print("\n==== RETRY DONE ====")
    for pid, ok in results:
        print(f"  {pid}: {'OK' if ok else 'STILL_FAIL'}")


if __name__ == "__main__":
    main()
