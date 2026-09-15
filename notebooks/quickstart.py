#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PaperForge DEPTH v4.2 快速入门

本脚本演示如何使用 PaperForge 的 DEPTH 评审系统对论文进行自动审稿。

前置条件：
    1. 安装依赖：pip install -r mock_api/requirements.txt
    2. 配置 .env（复制 .env.example）
    3. 启动 LLM 服务（本地 Ollama/llama.cpp 或云端 API）

用法：
    python notebooks/quickstart.py
"""
import asyncio
import os
import sys

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 设置默认环境变量
os.environ.setdefault("PAPERFORGE_LLM_CACHE_TTL", "0")
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")


def check_environment():
    """检查运行环境"""
    print("=" * 60)
    print("PaperForge DEPTH v4.2 快速入门")
    print("=" * 60)

    # 检查数据库
    from mock_api.database import init_db, SessionLocal
    from mock_api.models import Paper

    init_db()
    db = SessionLocal()
    paper_count = db.query(Paper).count()
    db.close()

    print(f"\n[环境检查]")
    print(f"  数据库: OK ({paper_count} 篇论文)")

    # 检查 LLM 配置
    from mock_api.llm.factory import LLMFactory
    try:
        factory = LLMFactory()
        provider = factory.get_provider()
        print(f"  LLM Provider: {provider.__class__.__name__}")
    except Exception as e:
        print(f"  LLM Provider: 未配置 ({e})")
        print(f"  提示: 请在前端「模型管理」页面配置 LLM API")

    return paper_count


def demo_single_review(paper_id: str = None):
    """演示单篇论文评审"""
    from mock_api.database import init_db, SessionLocal
    from mock_api.models import Paper
    from mock_api.depth_tasks import run_depth_review_sync

    init_db()
    db = SessionLocal()

    # 如果未指定 paper_id，选择第一篇有全文的论文
    if not paper_id:
        paper = db.query(Paper).filter(
            Paper.full_text.isnot(None),
            Paper.full_text != ""
        ).first()
        if not paper:
            print("\n[错误] 数据库中没有有全文的论文")
            print("提示: 请先上传 PDF 或从 arXiv 导入论文")
            return
        paper_id = paper.id

    print(f"\n[单篇评审演示]")
    print(f"  论文 ID: {paper_id}")

    # 运行评审
    print(f"  正在运行 DEPTH v4.2 评审...")
    try:
        run_depth_review_sync(paper_id)
        print(f"  评审完成!")
    except Exception as e:
        print(f"  评审失败: {e}")
        return

    # 读取结果
    from mock_api.models import DepthReviewV4
    review = db.query(DepthReviewV4).filter(
        DepthReviewV4.paper_id == paper_id
    ).order_by(DepthReviewV4.created_at.desc()).first()

    if review:
        fv = review.final_verdict or {}
        print(f"\n[评审结果]")
        print(f"  base_score:      {fv.get('base_score', 'N/A')}")
        print(f"  calibrated_score: {fv.get('calibrated_score', 'N/A')}")
        print(f"  final_verdict:   {fv.get('final_verdict', 'N/A')}")
        print(f"  has_figures:     {fv.get('has_figures', 'N/A')}")
        print(f"  w_fig:           {fv.get('w_fig', 'N/A')}")

    db.close()


def demo_batch_review(limit: int = 5):
    """演示批量论文评审"""
    from mock_api.database import init_db, SessionLocal
    from mock_api.models import Paper
    from mock_api.depth_tasks import run_depth_review_sync
    import time

    init_db()
    db = SessionLocal()

    # 获取有全文的论文
    papers = db.query(Paper).filter(
        Paper.full_text.isnot(None),
        Paper.full_text != ""
    ).limit(limit).all()

    if not papers:
        print("\n[错误] 数据库中没有有全文的论文")
        return

    print(f"\n[批量评审演示] ({len(papers)} 篇)")
    print(f"  预计耗时: ~{len(papers) * 30} 秒")

    results = []
    for i, paper in enumerate(papers, 1):
        t0 = time.time()
        try:
            run_depth_review_sync(paper.id)
            elapsed = time.time() - t0
            results.append((paper.id, True, elapsed))
            print(f"  [{i}/{len(papers)}] OK {paper.id} ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - t0
            results.append((paper.id, False, elapsed))
            print(f"  [{i}/{len(papers)}] FAIL {paper.id}: {str(e)[:50]}")

    # 汇总
    ok = sum(1 for _, success, _ in results if success)
    fail = sum(1 for _, success, _ in results if not success)
    avg_time = sum(t for _, _, t in results) / len(results)

    print(f"\n[汇总]")
    print(f"  成功: {ok}/{len(papers)}")
    print(f"  失败: {fail}/{len(papers)}")
    print(f"  平均耗时: {avg_time:.1f}s/篇")

    db.close()


def demo_score_distribution():
    """演示分数分布统计"""
    from mock_api.database import init_db, SessionLocal
    from mock_api.models import DepthReviewV4
    from collections import Counter

    init_db()
    db = SessionLocal()

    rows = db.query(DepthReviewV4).all()
    db.close()

    # 去重：保留每篇论文的最新评审
    latest = {}
    for r in rows:
        latest[r.paper_id] = r

    # 统计
    scores = []
    verdicts = Counter()
    for pid, r in latest.items():
        fv = r.final_verdict or {}
        score = fv.get("calibrated_score")
        if score is not None:
            scores.append(float(score))
        verdict = fv.get("final_verdict", "UNKNOWN")
        verdicts[verdict] += 1

    if not scores:
        print("\n[提示] 暂无评审结果")
        return

    print(f"\n[分数分布] ({len(scores)} 篇)")
    print(f"  均值: {sum(scores)/len(scores):.3f}")
    print(f"  中位数: {sorted(scores)[len(scores)//2]:.3f}")
    print(f"  最小: {min(scores):.3f}")
    print(f"  最大: {max(scores):.3f}")

    print(f"\n[Verdict 分布]")
    for v, c in verdicts.most_common():
        print(f"  {v:20s}: {c:4d} ({100*c/len(scores):.1f}%)")


def main():
    """主函数"""
    paper_count = check_environment()

    if paper_count == 0:
        print("\n[提示] 数据库为空，请先上传论文或从 arXiv 导入")
        return

    # 演示菜单
    print(f"\n[选择演示]")
    print(f"  1. 单篇评审")
    print(f"  2. 批量评审（5篇）")
    print(f"  3. 分数分布统计")
    print(f"  0. 退出")

    choice = input("\n请选择 (0-3): ").strip()

    if choice == "1":
        demo_single_review()
    elif choice == "2":
        demo_batch_review(5)
    elif choice == "3":
        demo_score_distribution()
    else:
        print("退出")


if __name__ == "__main__":
    main()
