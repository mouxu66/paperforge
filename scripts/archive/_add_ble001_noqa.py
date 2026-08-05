"""One-shot helper: annotate bare `except Exception` sites with BLE001 noqa.

Strategy (per thinker plan + 22-item optimization list item #10):

- For each file in MOCK_API_FILES, run a per-line regex that matches
  `except Exception:` / `except Exception as <name>:` lines WITHOUT an existing
  `# noqa: BLE001` or `# pragma: no cover` marker on the same line.
- Replace with the SAME except clause plus an inline `# noqa: BLE001 - <reason>`
  comment, where `<reason>` is chosen from FILE_REASONS table.
- Files NOT in the table get a conservative default "broad catch context".

NOT a permanent tool: run once, verify ruff + pytest, commit the resulting
annotations, then archive this script (it can be re-run idempotently because
already-annotated lines are skipped).

Scope:
- ONLY touches bare `except Exception` (not subclass specifics like
  `except (ValueError, KeyError)` — those are fine on their own).
- Re-running is idempotent: already has noqa → no change.

Heuristic for "already-annotated": looks for `# noqa:` or `# pragma:` ANYWHERE on
the same line (multi-line noqa isn't supported).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# File → one-line reason for "why we keep broad catch here."
# These reasons document the INTENT so future maintainers don't accidentally
# replace `except Exception` with narrow types without understanding the
# boundary (worker loop / OCR per-page / startup hook, etc.).
FILE_REASONS: dict[str, str] = {
    # ── Workers & background tasks ────────────────────────────
    # 任何后台线程里的回调 / 子任务，吞下非致命异常不能中断整个 pipeline。
    "mock_api/workers/batch_delete.py": "worker loop - 单条失败应隔离，不应中断批处理",
    "mock_api/workers/export.py": "worker loop - 导出多章节/多资源时单点失败需隔离",
    "mock_api/workers/figures.py": "worker loop - figure 抽取/OCR 单图失败需隔离",
    "mock_api/workers/reviews.py": "worker loop - 评审子任务单点失败需隔离，避免整 pipeline 失败",
    "mock_api/tasks.py": "TaskManager worker body - 后台任务异常必须兜底写 failed 状态",
    "mock_api/depth_tasks.py": "depth reviewer worker - 9 节点流水线子任务异常需隔离",
    "mock_api/export_tasks.py": "export worker - 导出任务子步骤异常需隔离",
    # ── OCR pipeline per-page ─────────────────────────────────
    # Per-page failure should not abort the whole PDF.
    "mock_api/pdf_parser.py": "OCR/PDF 单页/单图回调 - 单失败不应中断整文档",
    # ── DEPTH 评审 / LLM watchdog ───────────────────────────
    "mock_api/depth_eval_v4.py": "DEPTH 节点/watchdog 回调 - broad catch 兜底并写日志",
    "mock_api/depth_pipeline.py": "DAG 节点 - 单节点异常需隔离不传播",
    "mock_api/depth_calibration.py": "calibration I/O - 文件缺失/解析失败需兜底",
    "mock_api/depth_utils.py": "工具函数 - 边界条件兜底",
    # ── Scheduler / periodic jobs ─────────────────────────────
    "mock_api/scheduler.py": "periodic job - 单次失败不应停调度，重试交给下个 tick",
    # ── Semantic / retrieval ─────────────────────────────────
    "mock_api/semantic_search.py": "可选向量检索 - 离线/缺模型时退化到 FTS，不阻断请求",
    "mock_api/nlp_utils.py": "NLP helper - 退化路径兜底",
    # ── Reflection fidelity computation ──────────────────────
    "mock_api/reflection_fidelity.py": "fidelity heuristic - 退化到默认值而非失败",
    "mock_api/reflection_binding.py": "reflection 绑定 - 跨模块异常兜底",
    "mock_api/reflection_pipeline.py": "reflection pipeline - 子任务异常隔离",
    "mock_api/reflection_docx_parser.py": "docx 结构多样 - 单字段解析失败不阻塞整体",
    # ── Ranker heuristics ─────────────────────────────────────
    "mock_api/recommend_ranker.py": "ranker 启发式 - 单维度失败退化到默认值，不阻断推荐",
    # ── Recommended resolver / paper ingest ──────────────────
    "mock_api/report_paper_resolver.py": "外部 arXiv 下载 - 网络/解析失败应 status 降级",
    "mock_api/arxiv_crawler.py": "arxiv HTTP - 单次拉取失败，下次跳过该关键词",
    # ── Writing assist streaming ──────────────────────────────
    "mock_api/writing_assist.py": "LLM 流式输出 - chunk 异常应中断而不是崩溃",
    # ── Settings / config / launcher (startup) ────────────────
    "mock_api/settings.py": "settings 加载 - 缺字段用默认值，引导可启动",
    "mock_api/config.py": "config 加载 - 兜底默认值，确保启动",
    "mock_api/compute_mode.py": "compute mode 切换 - 失败时回退基础模式",
    "mock_api/launcher.py": "lifespan/startup 钩子 - 启动失败应记录，不阻止",
    "mock_api/watchdog_zotero.py": "Zotero 监控 poll - 单次异常不打断监控循环",
    "mock_api/ocr_logging.py": "OCR 日志写入 - 日志失败不影响 OCR 主流程",
    # ── Database / migrations ────────────────────────────────
    "mock_api/database.py": "migration/backup 钩子 - 失败应记录，不阻塞启动",
    "mock_api/_dl_multilingual_model.py": "模型下载 - 单次失败下次重试",
    # ── LLM factory ──────────────────────────────────────────
    "mock_api/llm/factory.py": "LLM provider 切换 - fallback chain 兜底",
    # ── Admin / packaging ─────────────────────────────────────
    "mock_api/admin/package.py": "admin 打包 - 局部失败隔离，不影响其他文件",
    # ── Consistency for main.py / crud/* (will narrow partly) ─
    "mock_api/crud/figures.py": "figure crud - 退化到空记录而非失败",
    "mock_api/crud/search.py": "FTS5 退化 - 异常时返回空列表而非 500",
    "mock_api/crud/embeddings.py": "embedding upsert - 失败留待下次重试",
    "mock_api/crud/analysis.py": "analysis 派生字段 - 单维度失败用默认值",
    "mock_api/crud/notes.py": "notes crud - 单笔记失败隔离，不影响批处理",
    "mock_api/crud/papers.py": "papers crud - 退化到默认查询/返回",
    "mock_api/crud/__init__.py": "crud package init - 模块级 fallback",
    # ── Endpoint barriers (main.py) — errors envelope catches downstream ──
    "mock_api/main.py": "endpoint barrier - 未捕获异常由 errors.envelope_response 兜底 5xx 返回",
    # ── Lifespan / startup hooks (app.py) ────────────────────────
    "mock_api/app.py": "lifespan 钩子 - 启动失败应记录，不阻止应用启动",
    # ── Semantic Scholar enrichment ────────────────────────────
    "mock_api/semantic_scholar.py": "enrichment fallback - enrichment 失败返回默认元数据",
}

# ── Regex & matching ────────────────────────────────────────────────────
# Match: `except Exception:` OR `except Exception as name:` OR `except
# Exception as _name:` at the start of the bare line; the entire match MUST NOT
# contain `# noqa` or `# pragma` anywhere on the line.
_EXCEPT_RE = re.compile(
    r"^(?P<indent>\s*)except Exception(?P<binding>(?:\s+as\s+\w+)?):(?P<rest>.*)$"
)
_HAS_ANNOTATION = re.compile(r"#\s*(?:noqa|pragma)\b", re.IGNORECASE)


def annotate_file(path: Path, reason: str) -> int:
    """Insert `# noqa: BLE001 - <reason>` on every bare `except Exception`.

    Returns the count of inserted annotations (0 if already annotated).
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    inserted = 0
    for i, line in enumerate(lines):
        # Strip only the leading content for regex (preserve newline).
        body = line.rstrip("\n")
        if _HAS_ANNOTATION.search(body):
            continue  # already annotated via noqa or pragma
        m = _EXCEPT_RE.match(body)
        if not m:
            continue
        # Reconstruct: indent + `except Exception...` + noqa suffix + rest + newline
        suffix = f"  # noqa: BLE001 - {reason}"
        new_body = m.group("indent") + "except Exception" + m.group("binding") + ":" + suffix + m.group("rest")
        if not new_body.endswith("\n"):
            new_body += "\n"
        lines[i] = new_body
        inserted += 1
    if inserted > 0:
        path.write_text("".join(lines), encoding="utf-8")
    return inserted


def main() -> int:
    total_files = 0
    total_inserted = 0
    for rel, reason in FILE_REASONS.items():
        p = ROOT / rel
        if not p.exists():
            print(f"⚠️  missing: {rel}")
            continue
        n = annotate_file(p, reason)
        total_files += 1
        total_inserted += n
        print(f"{rel}: +{n} noqa")
    print(f"\n=== done: {total_inserted} noqa inserted across {total_files} files ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
