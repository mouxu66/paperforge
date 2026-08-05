"""用 Qwen3-VL-4B（HTTP 视觉模型）重新生成 paper_figures.qwen_summary。

走 figure_qwen._ask_vision_on_figure 的 HTTP-first 路径，调 8082 端口的
Qwen3-VL-4B 服务生成高质量语义摘要。

用法：
    # 确保 8082 端口 Qwen3-VL-4B 服务已启动
    # .env 已配置 PAPERFORGE_VISION_HTTP_URL=http://127.0.0.1:8082
    python scripts/diagnostics/backfill_qwen_summary_vision.py              # 只回填空记录
    python scripts/diagnostics/backfill_qwen_summary_vision.py --all       # 全部重新生成
    python scripts/diagnostics/backfill_qwen_summary_vision.py --paper pr_1612.08810  # 指定论文
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

DB = ROOT / "mock_api" / "paperforge_mock.db"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="全部重新生成（不跳过已有的 qwen_summary）")
    parser.add_argument("--paper", default=None, help="只处理指定 paper_id（如 pr_1612.08810）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 条（0=不限）")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    where = "figure_path IS NOT NULL AND figure_path != ''"
    if not args.all:
        where += " AND (qwen_summary IS NULL OR qwen_summary = '')"
    if args.paper:
        where += f" AND paper_id = '{args.paper}'"
    sql = f"""
        SELECT id, paper_id, figure_path, caption_text, ocr_text, qwen_summary
        FROM paper_figures
        WHERE {where}
        ORDER BY paper_id, figure_index
    """
    if args.limit > 0:
        sql += f" LIMIT {args.limit}"
    cur.execute(sql)
    rows = cur.fetchall()

    if not rows:
        print("无待处理记录")
        return

    print(f"=== Qwen3-VL-4B 视觉摘要回填 ===")
    print(f"待处理: {len(rows)} 条")
    print(f"模式: {'全部重新生成' if args.all else '只回填空记录'}")

    # 延迟 import，确保 .env 已加载
    from mock_api.llm.figure_qwen import _ask_vision_on_figure

    ok, fail, skip = 0, 0, 0
    for i, (fid, paper_id, fig_path, caption, ocr_text, old_qwen) in enumerate(rows, 1):
        p = Path(fig_path)
        if not p.exists():
            print(f"  [{i}/{len(rows)}] {paper_id} fig{fid} 跳过（文件不存在）")
            skip += 1
            continue
        print(f"  [{i}/{len(rows)}] {paper_id} fig{fid} 生成中...", end=" ", flush=True)
        t0 = time.time()
        try:
            summary = _ask_vision_on_figure(
                fig_path,
                caption_text=caption or "",
                timeout=120,
            )
            dt = time.time() - t0
            if summary and summary.strip():
                cur.execute(
                    "UPDATE paper_figures SET qwen_summary = ? WHERE id = ?",
                    (summary.strip(), fid),
                )
                conn.commit()
                print(f"OK ({dt:.1f}s, {len(summary)}字)")
                ok += 1
            else:
                print(f"空结果 ({dt:.1f}s)")
                fail += 1
        except Exception as e:
            print(f"失败: {e}")
            fail += 1

    print(f"\n=== 完成 ===")
    print(f"成功: {ok}, 失败: {fail}, 跳过: {skip}, 总计: {len(rows)}")

    # 统计 qwen_summary 覆盖率
    cur.execute("SELECT COUNT(*) FROM paper_figures WHERE figure_path IS NOT NULL")
    total = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM paper_figures WHERE qwen_summary IS NOT NULL AND qwen_summary != '' AND figure_path IS NOT NULL"
    )
    filled = cur.fetchone()[0]
    print(f"qwen_summary 覆盖率: {filled}/{total} ({100*filled//max(total,1)}%)")

    conn.close()


if __name__ == "__main__":
    main()
