#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PaperForge CLI 入口

用法：
    python -m paperforge depth --paper 2606.26157
    python -m paperforge depth --batch --input papers.csv
    python -m paperforge depth --paper xxx --output result.json --format json
"""
import argparse
import sys
import os

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def cmd_depth(args):
    """DEPTH 评审命令"""
    from mock_api.database import init_db
    init_db()

    if args.batch:
        return cmd_depth_batch(args)
    else:
        return cmd_depth_single(args)


def cmd_depth_single(args):
    """单篇评审"""
    import time
    import json
    from mock_api.database import SessionLocal
    from mock_api.models import Paper, DepthReviewV4
    from mock_api.depth_tasks import run_depth_review_sync

    paper_id = args.paper
    print(f"[DEPTH] 评审论文: {paper_id}")

    db = SessionLocal()

    # 检查论文是否存在
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        print(f"[ERROR] 论文 {paper_id} 不存在")
        db.close()
        return 1

    if not paper.full_text or len(paper.full_text) < 100:
        print(f"[ERROR] 论文 {paper_id} 缺少全文（当前 {len(paper.full_text or '')} 字符）")
        db.close()
        return 1

    # 检查缓存
    from mock_api.depth_eval_v4 import get_cached_score
    cached = get_cached_score(db, paper_id)
    if cached and not args.force:
        print(f"[CACHE] 命中缓存，跳过评审")
        fv = cached
        # 构造一个假 review 对象
        class FakeReview:
            def __init__(self, fv):
                self.final_verdict = fv
        review = FakeReview(fv)
    else:
        # 运行评审
        t0 = time.time()
        try:
            run_depth_review_sync(paper_id)
            elapsed = time.time() - t0
            print(f"[OK] 评审完成 ({elapsed:.1f}s)")
        except Exception as e:
            print(f"[ERROR] 评审失败: {e}")
            db.close()
            return 1
        review = db.query(DepthReviewV4).filter(
            DepthReviewV4.paper_id == paper_id
        ).order_by(DepthReviewV4.created_at.desc()).first()

    # 读取结果
    if not cached:
        review = db.query(DepthReviewV4).filter(
            DepthReviewV4.paper_id == paper_id
        ).order_by(DepthReviewV4.created_at.desc()).first()
        if not review:
            print("[ERROR] 未找到评审结果")
            db.close()
            return 1
        fv = review.final_verdict or {}
    else:
        fv = cached

    # 输出结果
    if args.format == "json":
        output = {
            "paper_id": paper_id,
            "title": paper.title,
            "base_score": fv.get("base_score"),
            "calibrated_score": fv.get("calibrated_score"),
            "final_verdict": fv.get("final_verdict"),
            "llm_verdict": fv.get("llm_verdict"),
            "has_figures": fv.get("has_figures"),
            "w_fig": fv.get("w_fig"),
            "figure_consistency_score": fv.get("figure_consistency_score"),
            "weights": fv.get("weights"),
            "evidence_checks": fv.get("evidence_checks"),
            "node_score_stds": fv.get("node_score_stds"),
            "score_uncertainty": fv.get("score_uncertainty"),
        }
        result_str = json.dumps(output, indent=2, ensure_ascii=False)
    elif args.format == "csv":
        # CSV 格式（单行）
        headers = ["paper_id", "base_score", "calibrated_score", "final_verdict", "has_figures", "w_fig"]
        values = [
            paper_id,
            fv.get("base_score", ""),
            fv.get("calibrated_score", ""),
            fv.get("final_verdict", ""),
            fv.get("has_figures", ""),
            fv.get("w_fig", ""),
        ]
        result_str = ",".join(headers) + "\n" + ",".join(str(v) for v in values)
    else:
        # Markdown 格式（默认）
        result_str = f"""# DEPTH 评审结果

## 基本信息
- **论文 ID**: {paper_id}
- **标题**: {paper.title}

## 评分
| 指标 | 值 |
|------|-----|
| base_score | {fv.get('base_score', 'N/A')} |
| calibrated_score | {fv.get('calibrated_score', 'N/A')} |
| final_verdict | {fv.get('final_verdict', 'N/A')} |
| llm_verdict | {fv.get('llm_verdict', 'N/A')} |

## 图表一致性
| 指标 | 值 |
|------|-----|
| has_figures | {fv.get('has_figures', 'N/A')} |
| w_fig | {fv.get('w_fig', 'N/A')} |
| figure_consistency_score | {fv.get('figure_consistency_score', 'N/A')} |

## 权重
```json
{json.dumps(fv.get('weights', {}), indent=2)}
```
"""

    # 输出到文件或 stdout
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result_str)
        print(f"[OK] 结果已保存到: {args.output}")
    else:
        print(result_str)

    db.close()
    return 0


def cmd_depth_batch(args):
    """批量评审"""
    import csv
    import time
    from mock_api.database import SessionLocal
    from mock_api.models import Paper
    from mock_api.depth_tasks import run_depth_review_sync

    # 读取输入文件
    if not args.input:
        print("[ERROR] 批量模式需要 --input 参数")
        return 1

    with open(args.input, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # 支持 paper_id 或 id 列
        paper_ids = [row.get("paper_id") or row.get("id") for row in reader]

    print(f"[DEPTH] 批量评审: {len(paper_ids)} 篇论文")

    db = SessionLocal()
    success = 0
    failed = 0
    results = []

    for i, paper_id in enumerate(paper_ids, 1):
        if not paper_id:
            continue

        # 检查论文是否存在
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            print(f"[{i}/{len(paper_ids)}] SKIP {paper_id} (不存在)")
            failed += 1
            continue

        if not paper.full_text or len(paper.full_text) < 100:
            print(f"[{i}/{len(paper_ids)}] SKIP {paper_id} (缺少全文)")
            failed += 1
            continue

        # 运行评审
        t0 = time.time()
        try:
            run_depth_review_sync(paper_id)
            elapsed = time.time() - t0
            success += 1
            print(f"[{i}/{len(paper_ids)}] OK {paper_id} ({elapsed:.1f}s)")
            results.append({"paper_id": paper_id, "status": "ok", "time": elapsed})
        except Exception as e:
            elapsed = time.time() - t0
            failed += 1
            print(f"[{i}/{len(paper_ids)}] FAIL {paper_id}: {str(e)[:50]} ({elapsed:.1f}s)")
            results.append({"paper_id": paper_id, "status": "fail", "error": str(e)[:100]})

    db.close()

    # 汇总
    print(f"\n[DONE] 成功: {success}, 失败: {failed}, 总计: {len(paper_ids)}")

    # 输出结果
    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=["paper_id", "status", "time", "error"])
            writer.writeheader()
            writer.writerows(results)
        print(f"[OK] 结果已保存到: {args.output}")

    return 0


def main():
    parser = argparse.ArgumentParser(
        prog="paperforge",
        description="PaperForge - 基于本地论文库的学术写作助手"
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # depth 命令
    depth_parser = subparsers.add_parser("depth", help="DEPTH 论文评审")
    depth_parser.add_argument("--paper", type=str, help="论文 ID")
    depth_parser.add_argument("--batch", action="store_true", help="批量模式")
    depth_parser.add_argument("--input", type=str, help="输入文件（CSV，含 paper_id 列）")
    depth_parser.add_argument("--output", type=str, help="输出文件路径")
    depth_parser.add_argument("--format", choices=["json", "csv", "markdown"], default="markdown", help="输出格式")
    depth_parser.add_argument("--force", action="store_true", help="强制重新评审（忽略缓存）")

    args = parser.parse_args()

    if args.command == "depth":
        sys.exit(cmd_depth(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
