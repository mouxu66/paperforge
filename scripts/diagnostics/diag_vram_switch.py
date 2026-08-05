"""诊断 VRAM 切换：request_ocr 是否真的停掉 llama-server。"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LLAMA_CPP_N_GPU_LAYERS", "99")

import urllib.request

def check_qwen_alive() -> bool:
    try:
        req = urllib.request.Request("http://localhost:8080/v1/models", headers={"User-Agent": "x"})
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False

print(f"[1] Qwen 8080 初始状态: {'ALIVE' if check_qwen_alive() else 'DOWN'}")

from mock_api.vram_scheduler import get_vram_scheduler
from mock_api.settings import get_settings

s = get_settings()
print(f"[2] vram_exclusive={s.vram_exclusive}, qwen_autostart={s.qwen_autostart}")

mgr = None
try:
    from mock_api.llama_server_manager import get_llama_server_manager
    mgr = get_llama_server_manager()
    print(f"[3] llama_server_manager 获取成功")
except Exception as e:
    print(f"[3] FAIL: {e}")
    sys.exit(1)

print(f"[4] 调用 request_ocr()...")
scheduler = get_vram_scheduler()
scheduler.request_ocr()
time.sleep(3)
print(f"[5] request_ocr 后 Qwen 8080: {'ALIVE(未停!)' if check_qwen_alive() else 'DOWN(已停)'}")

print(f"[6] 调用 ocr_finished()...")
scheduler.ocr_finished()
print(f"[7] 等待 30s 让 Qwen 自动拉回...")
for i in range(15):
    time.sleep(2)
    if check_qwen_alive():
        print(f"    [{i*2}s] Qwen 已拉回")
        break
else:
    print(f"    [30s] Qwen 仍未拉回")

print(f"[8] 最终 Qwen 8080: {'ALIVE' if check_qwen_alive() else 'DOWN'}")
