"""完全关闭 QF 节点 + figure 证据并入的 wrapper（subprocess 隔离）。

绕过 figures_qf_test.py 顶部的 setdefault，用环境变量强制关闭：
  - PAPERFORGE_DEPTH_QF_NODE_ENABLED=false
  - PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED=false
  - PAPERFORGE_DEPTH_FIGURE_WEIGHT=0.0
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TARGET = ROOT / "scripts" / "calibration" / "figures_qf_test.py"

# 强制设置（setdefault 在子进程里不会覆盖已存在的环境变量）
env = dict(os.environ)
env["PAPERFORGE_DEPTH_QF_NODE_ENABLED"] = "false"
env["DEPTH_QF_NODE_ENABLED"] = "false"
env["PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED"] = "false"
env["DEPTH_FIGURE_EVIDENCE_ENABLED"] = "false"
env["PAPERFORGE_DEPTH_FIGURE_WEIGHT"] = "0.0"
env["DEPTH_FIGURE_WEIGHT"] = "0.0"
env["PYTHONUNBUFFERED"] = "1"

# 备份原结果文件，让 figures_qf_test.py 写新结果到 RESULTS_ON
results_on = ROOT / "deliverables" / "figures_qf_test_results_on.jsonl"
if results_on.exists():
    # 用时间戳避免覆盖
    import time
    ts = time.strftime("%H%M%S")
    backup = ROOT / "deliverables" / f"figures_qf_test_results_on_W002_{ts}.jsonl"
    results_on.rename(backup)
    print(f"已备份 w_fig=0.02 结果到 {backup.name}")

# 跑 figures-ON 模式（但 QF 节点被 env 关闭）
cmd = [
    str(ROOT / ".venv" / "Scripts" / "python.exe"),
    str(TARGET),
    "--mode", "on",
    "--fresh",
]
print(f"启动: {' '.join(cmd)}")
print(f"env: QF_NODE={env['PAPERFORGE_DEPTH_QF_NODE_ENABLED']}, "
      f"FIG_EVIDENCE={env['PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED']}, "
      f"W_FIG={env['PAPERFORGE_DEPTH_FIGURE_WEIGHT']}")

r = subprocess.run(cmd, env=env)
sys.exit(r.returncode)
