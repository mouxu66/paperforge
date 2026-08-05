"""按关键词列表批量拉取 arXiv 论文并入库。

使用方式：
    python scripts/fetch_arxiv_batch.py --base-url http://127.0.0.1:8770

工作流程：
1. 按关键词列表依次调用 mock_api.arxiv_crawler.search_arxiv 拉取 arXiv 元数据。
2. 用全局 seen_ids 集合去重（同一篇论文只入库一次）。
3. 通过 POST /api/arxiv/import 批量入库到 SQLite。
4. 拉取完成后自动调用 scripts/embed_papers.py 生成向量嵌入。
5. 输出 JSON + Markdown 拉取统计报告。

关键词覆盖：machine learning, deep learning, NLP, computer vision,
LLM, transformer, diffusion, LoRA, RAG, agent。每个关键词拉取 50 篇。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# 确保能导入 mock_api 包
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_api.arxiv_crawler import search_arxiv  # noqa: E402

# 默认关键词列表（覆盖主流 AI/ML 研究方向）
DEFAULT_KEYWORDS = [
    "machine learning",
    "deep learning",
    "NLP",
    "computer vision",
    "LLM",
    "transformer",
    "diffusion",
    "LoRA",
    "RAG",
    "agent",
]

DEFAULT_MAX_RESULTS = 50
# arXiv API 建议请求间隔 ≥ 3 秒，避免被限流
REQUEST_INTERVAL_SEC = 3.0
# 指数退避重试参数
MAX_RETRY = 3
INITIAL_BACKOFF = 2.0


def fetch_with_retry(keyword: str, max_results: int) -> tuple[list[dict], str | None]:
    """带指数退避重试的 arXiv 拉取。

    Returns:
        (papers, error) — 成功时 error 为 None；失败时 papers 为空列表。
    """
    backoff = INITIAL_BACKOFF
    last_err: str | None = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            papers = search_arxiv(keyword, max_results=max_results)
            return papers, None
        except RuntimeError as e:
            last_err = str(e)
            print(f"  [重试 {attempt}/{MAX_RETRY}] {keyword} 拉取失败：{e}")
            if attempt < MAX_RETRY:
                time.sleep(backoff)
                backoff *= 2
    return [], last_err


def import_papers(base_url: str, papers: list[dict]) -> tuple[int, int, list[str]]:
    """通过 /api/arxiv/import 批量入库。

    Returns:
        (success_count, fail_count, failed_ids)
    """
    if not papers:
        return 0, 0, []

    payload = {"papers": papers}
    try:
        resp = requests.post(
            f"{base_url}/api/arxiv/import",
            json=payload,
            timeout=60,
        )
    except requests.RequestException as e:
        print(f"  [入库错误] 请求失败：{e}")
        return 0, len(papers), [p["id"] for p in papers]

    if resp.status_code != 200:
        print(f"  [入库错误] HTTP {resp.status_code}：{resp.text[:200]}")
        return 0, len(papers), [p["id"] for p in papers]

    try:
        data = resp.json()
        success = data.get("successCount", 0)
        fail = data.get("failCount", 0)
        failed_ids = [r["id"] for r in data.get("results", []) if not r.get("success", True)]
        return success, fail, failed_ids
    except (ValueError, KeyError, TypeError) as e:
        print(f"  [入库错误] 解析响应失败：{e}")
        return 0, len(papers), [p["id"] for p in papers]


def fetch_and_import_keyword(
    keyword: str,
    max_results: int,
    base_url: str,
    seen_ids: set[str],
) -> dict:
    """拉取并入库单个关键词的论文。

    Returns:
        {
            "keyword": str,
            "fetched": int,       # arXiv 实际返回数量
            "new": int,           # 去重后新论文数量
            "imported": int,       # 入库成功数量
            "failed": int,        # 入库失败数量
            "duplicate": int,     # 与前序关键词重复的数量
            "error": Optional[str],
            "duration_sec": float,
        }
    """
    start_ts = time.time()
    print(f"\n[{keyword}] 开始拉取（目标 {max_results} 篇）...")

    papers, err = fetch_with_retry(keyword, max_results)
    if err:
        print(f"[{keyword}] 拉取失败：{err}")
        return {
            "keyword": keyword,
            "fetched": 0,
            "new": 0,
            "imported": 0,
            "failed": 0,
            "duplicate": 0,
            "error": err,
            "duration_sec": round(time.time() - start_ts, 2),
        }

    print(f"[{keyword}] arXiv 返回 {len(papers)} 篇")

    # 去重：同一篇论文（arxiv_id）只在首次出现时入库
    new_papers: list[dict] = []
    duplicate_count = 0
    for p in papers:
        pid = p.get("id", "")
        if not pid:
            continue
        if pid in seen_ids:
            duplicate_count += 1
            continue
        seen_ids.add(pid)
        new_papers.append(p)

    print(f"[{keyword}] 去重后新增 {len(new_papers)} 篇（重复 {duplicate_count} 篇）")

    if not new_papers:
        return {
            "keyword": keyword,
            "fetched": len(papers),
            "new": 0,
            "imported": 0,
            "failed": 0,
            "duplicate": duplicate_count,
            "error": None,
            "duration_sec": round(time.time() - start_ts, 2),
        }

    # 入库
    success, fail, failed_ids = import_papers(base_url, new_papers)
    print(f"[{keyword}] 入库成功 {success} / 失败 {fail}")

    return {
        "keyword": keyword,
        "fetched": len(papers),
        "new": len(new_papers),
        "imported": success,
        "failed": fail,
        "duplicate": duplicate_count,
        "error": None,
        "duration_sec": round(time.time() - start_ts, 2),
    }


def trigger_embeddings(force: bool = False) -> dict:
    """调用 scripts/embed_papers.py 生成向量嵌入。

    Returns:
        {"success": bool, "stdout": str, "returncode": int}
    """
    cmd = [sys.executable, str(ROOT / "scripts" / "embed_papers.py")]
    if force:
        cmd.append("--force")
    print("\n" + "=" * 60)
    print("触发向量嵌入生成（scripts/embed_papers.py）...")
    print("=" * 60)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=3600,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "embed_papers.py 执行超时（>3600s）",
            "returncode": -1,
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1,
        }


def generate_reports(
    stats: list[dict],
    embedding_result: dict,
    output_dir: Path,
) -> tuple[Path, Path]:
    """生成 JSON 与 Markdown 统计报告。

    Returns:
        (json_path, md_path)
    """
    total_fetched = sum(s["fetched"] for s in stats)
    total_new = sum(s["new"] for s in stats)
    total_imported = sum(s["imported"] for s in stats)
    total_failed = sum(s["failed"] for s in stats)
    total_duplicate = sum(s["duplicate"] for s in stats)

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "keywords_total": len(stats),
            "keywords_success": sum(1 for s in stats if s["error"] is None),
            "keywords_failed": sum(1 for s in stats if s["error"] is not None),
            "papers_fetched": total_fetched,
            "papers_new": total_new,
            "papers_imported": total_imported,
            "papers_failed": total_failed,
            "papers_duplicate": total_duplicate,
        },
        "embedding": {
            "success": embedding_result["success"],
            "returncode": embedding_result["returncode"],
            "stdout_tail": embedding_result["stdout"][-500:] if embedding_result["stdout"] else "",
            "stderr_tail": embedding_result["stderr"][-500:] if embedding_result["stderr"] else "",
        },
        "details": stats,
    }

    json_path = output_dir / "arxiv_fetch_report.json"
    md_path = output_dir / "arxiv_fetch_report.md"

    # JSON 报告
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Markdown 报告
    lines = [
        "# arXiv 批量拉取统计报告",
        "",
        f"生成时间：{report['generated_at']}",
        "",
        "## 总览",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 关键词总数 | {len(stats)} |",
        f"| 成功关键词数 | {report['summary']['keywords_success']} |",
        f"| 失败关键词数 | {report['summary']['keywords_failed']} |",
        f"| arXiv 返回论文数 | {total_fetched} |",
        f"| 去重后新增论文数 | {total_new} |",
        f"| 入库成功数 | {total_imported} |",
        f"| 入库失败数 | {total_failed} |",
        f"| 重复跳过数 | {total_duplicate} |",
        "",
        "## 向量嵌入生成",
        "",
        f"- 成功：{'是' if embedding_result['success'] else '否'}",
        f"- 返回码：{embedding_result['returncode']}",
        "",
        "## 关键词明细",
        "",
        "| 关键词 | 拉取数 | 新增数 | 入库成功 | 失败 | 重复 | 耗时(s) | 错误 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in stats:
        err = (s["error"] or "")[:60]
        lines.append(
            f"| {s['keyword']} | {s['fetched']} | {s['new']} | "
            f"{s['imported']} | {s['failed']} | {s['duplicate']} | "
            f"{s['duration_sec']} | {err} |"
        )

    if embedding_result["stderr"]:
        lines += [
            "",
            "## 向量嵌入 stderr（末尾 500 字符）",
            "",
            "```",
            embedding_result["stderr"][-500:],
            "```",
        ]

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="按关键词批量拉取 arXiv 论文并入库")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8770",
        help="PaperForge 后端地址（默认 http://127.0.0.1:8770）",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_MAX_RESULTS,
        help=f"每个关键词拉取数量（默认 {DEFAULT_MAX_RESULTS}）",
    )
    parser.add_argument(
        "--keywords",
        nargs="*",
        default=DEFAULT_KEYWORDS,
        help="关键词列表（默认覆盖 10 个主流 AI/ML 方向）",
    )
    parser.add_argument(
        "--skip-embed",
        action="store_true",
        help="跳过向量嵌入生成步骤（仅入库）",
    )
    parser.add_argument(
        "--force-embed",
        action="store_true",
        help="强制重新生成所有向量（传递 --force 给 embed_papers.py）",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "scripts"),
        help="报告输出目录（默认 scripts/）",
    )
    args = parser.parse_args()

    # 健康检查
    print("=" * 60)
    print("PaperForge arXiv 批量拉取工具")
    print(f"后端地址：{args.base_url}")
    print(f"关键词列表（{len(args.keywords)} 个）：{args.keywords}")
    print(f"每个关键词拉取：{args.max_results} 篇")
    print("=" * 60)

    try:
        health = requests.get(f"{args.base_url}/api/health", timeout=10)
        health.raise_for_status()
        print(f"后端健康检查通过：{health.json()}")
    except Exception as e:
        print(f"[错误] 后端不可达：{e}")
        print("       请先启动后端：python -m mock_api.main")
        sys.exit(1)

    # 逐关键词拉取
    seen_ids: set[str] = set()
    stats: list[dict] = []
    for i, kw in enumerate(args.keywords):
        if i > 0:
            print(f"\n等待 {REQUEST_INTERVAL_SEC}s 以避免 arXiv 限流...")
            time.sleep(REQUEST_INTERVAL_SEC)
        result = fetch_and_import_keyword(
            keyword=kw,
            max_results=args.max_results,
            base_url=args.base_url,
            seen_ids=seen_ids,
        )
        stats.append(result)

    # 触发向量嵌入
    embedding_result: dict = {
        "success": False,
        "stdout": "",
        "stderr": "已跳过向量嵌入生成（--skip-embed）",
        "returncode": 0,
    }
    if not args.skip_embed:
        embedding_result = trigger_embeddings(force=args.force_embed)
        if embedding_result["success"]:
            print("向量嵌入生成完成。")
            if embedding_result["stdout"]:
                print("embed_papers.py 输出（末尾）：")
                print(embedding_result["stdout"][-500:])
        else:
            print(f"[警告] 向量嵌入生成失败：{embedding_result['stderr'][:200]}")
    else:
        print("\n已跳过向量嵌入生成。")

    # 生成报告
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = generate_reports(stats, embedding_result, output_dir)

    # 汇总输出
    total_fetched = sum(s["fetched"] for s in stats)
    total_new = sum(s["new"] for s in stats)
    total_imported = sum(s["imported"] for s in stats)
    print("\n" + "=" * 60)
    print("拉取完成汇总")
    print("=" * 60)
    print(f"关键词总数：    {len(stats)}")
    print(f"arXiv 返回：    {total_fetched} 篇")
    print(f"去重后新增：    {total_new} 篇")
    print(f"入库成功：      {total_imported} 篇")
    print(f"报告（JSON）：  {json_path}")
    print(f"报告（Markdown）：{md_path}")


if __name__ == "__main__":
    main()
