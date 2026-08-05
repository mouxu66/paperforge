"""合成对抗样本：验证「照抄惩罚」修复效果。

构造 4 种合成报告（基于绑定论文的真实全文）：
    copy        — 逐字粘贴论文英文原句
    translation — 近乎逐句翻译论文摘要（嵌入相似度 ~0.85+）
    paraphrase  — 合理的转述（有据但非照抄）
    fabricate   — 与论文无关的编造内容（低相似 + 断言）

输出每篇的 fidelity / copy_ratio / coverage / verdict，
对比修复前（旧算法单均值向量）与修复后（句对句 + 照抄惩罚）。

用法：
    python scripts/reflection_adversarial.py --paper 0706.2974
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PAPERFORGE_LOCAL_ML", "1")
os.environ.setdefault("PAPERFORGE_REFLECTION_SKIP_CROSSVAL", "1")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def build_samples(paper_text: str) -> dict[str, dict]:
    import re

    sents = [s.strip() for s in re.split(r"[。！？!?\n]+", paper_text or "") if len(s.strip()) >= 40]
    if not sents:
        raise SystemExit("论文无可用于构造样本的句子")
    head = sents[:3]

    copy_body = "\n".join(head)
    translation_body = "\n".join(f"论文中提到：{s[:80]}。" for s in head)
    paraphrase_body = (
        "一、论文研究了一个什么样的问题？\n"
        "本文针对远程实验教学中的关键挑战提出了开放平台方案，"
        "强调标准互通与真实设备接入，并分析了教育场景中的可复用性。\n"
        "二、论文中使用了什么机器人，软硬件结构分别是什么样的？\n"
        "论文面向通用远程实验室架构，硬件侧依赖工业网络、数据采集与可编程控制器，"
        "软件侧定义了人机接口、物理过程、工具服务、教学内容和教学控制等组件，"
        "组件间通过标准协议通信。\n"
        "三、论文中做了哪些实验，有什么样的结果，每个结果表示什么意思？\n"
        "论文属于架构综述，通过标准对照与约束定义来论证方案，"
        "并提出与学习设计标准兼容的可复用场景描述方式。\n"
        "四、收获与感想\n"
        "阅读后我认识到标准化与开放架构对实验教学平台长期价值的重要性。"
    )
    fabricate_body = (
        "一、论文研究了一个什么样的问题？\n"
        "这篇论文证明了永动机方案在能量循环中能够完全自持运行，"
        "并且给出了绝对不可能失败的实验数据，所有结果表明效率必然超过百分之百。\n"
        "二、论文中使用了什么机器人，软硬件结构分别是什么样的？\n"
        "论文使用的机器人是作者独创的量子悬浮飞行器，"
        "搭载了绝对先进的全息控制芯片，所有传感器均能检测到宇宙暗物质。\n"
        "三、论文中做了哪些实验，有什么样的结果，每个结果表示什么意思？\n"
        "实验结果表明所有设备都实现了零损耗运行，"
        "证明了这个方案毫无疑问能够彻底解决全球能源问题。\n"
        "四、收获与感想\n"
        "这篇论文让我认识到所有科学难题都能通过创新思维得到彻底解决。"
    )

    return {
        "copy": {"q": copy_body, "tech": "", "exp": "", "reflection": "感想：本文值得学习。"},
        "translation": {"q": translation_body, "tech": "", "exp": "", "reflection": "感想：本文值得学习。"},
        "paraphrase": {"q": paraphrase_body, "tech": "", "exp": "", "reflection": "感想：收获很大。"},
        "fabricate": {"q": fabricate_body, "tech": "", "exp": "", "reflection": "感想：收获很大。"},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", default="0706.2974", help="绑定论文 id（arXiv id 或 upload_*）")
    args = ap.parse_args()

    from mock_api.database import SessionLocal
    from mock_api.models import Paper as PaperORM
    from mock_api.reflection_fidelity import compute_coverage, compute_fidelity

    db = SessionLocal()
    p = db.query(PaperORM).filter(PaperORM.id == args.paper).first()
    if p is None:
        print(f"论文 {args.paper} 不存在")
        return 1
    print(f"论文: {p.title[:60]} ({len(p.full_text)} chars)\n")
    print(f"{'样本':<12}{'fidelity':>9}{'copy_ratio':>11}{'grounded':>9}{'coverage':>9}")
    for name, sections in build_samples(p.full_text).items():
        fid = compute_fidelity(sections, p.full_text)
        cov = compute_coverage(sections, p.full_text)
        print(
            f"{name:<12}{fid.fidelity!s:>9}{str(fid.copy_ratio):>11}"
            f"{str(fid.grounded_ratio):>9}{str(cov.coverage):>9}"
        )
        if name in ("copy", "fabricate"):
            print(f"    copy_sentences: {len(fid.copy_sentences)} | stray: {len(fid.stray_claims)}")
    db.close()
    print("\n期望：copy/translation 的 fidelity 被照抄惩罚压到低位；paraphrase 保持高位；fabricate 低位且无照抄。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
