"""分步 import 诊断：定位 segfault 在哪个 import。"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os
os.environ.setdefault("LLAMA_CPP_N_GPU_LAYERS", "99")

def step(msg):
    print(f"[step] {msg}", flush=True)
    sys.stdout.flush()

step("1. import mock_api.database")
from mock_api.database import SessionLocal, init_db

step("2. import mock_api.vram_scheduler")
from mock_api.vram_scheduler import get_vram_scheduler

step("3. import mock_api.llama_server_manager")
from mock_api.llama_server_manager import get_llama_server_manager

step("4. import scripts.figure_utils")
from scripts.figure_utils import ask_qwen

step("5. import mock_api.pdf_parser (extract_figures_for_paper)")
from mock_api.pdf_parser import extract_figures_for_paper, route_vlm_for_figure

step("6. import scripts.pipeline_figure_understanding")
from scripts.pipeline_figure_understanding import run_pipeline

step("7. import mock_api.crud.figures")
from mock_api.crud.figures import delete_figures_by_paper, upsert_figure

step("8. import mock_api.semantic_search")
from mock_api.semantic_search import embed_text

step("ALL IMPORTS OK")
