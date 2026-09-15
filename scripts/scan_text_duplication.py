#!/usr/bin/env python3
"""全库 full_text 两两相似度扫描 → 重复发表/论文工厂候选清单 CSV。

跑 text_similarity.scan_corpus_duplication（词级 5-gram shingling + Jaccard，
倒排索引加速），候选对按 jaccard 降序落 CSV 供人工复核。阈值默认 0.30
（比单篇检测的 0.55 低，保召回；结论一律交人工，脚本不出判定）。

--semantic：追加一轮语义级重复扫描（换词重写）——fastembed 嵌入全文片段，
余弦相似度高 + 表层 Jaccard 低 = 措辞不同但语义相同。需 fastembed 可用，
不可用时打印提示并跳过（fail-open）。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/scan_text_duplication.py
    .venv/Scripts/python.exe scripts/scan_text_duplication.py \
        --jaccard-threshold 0.30 --max-pairs 200 \
        --out deliverables/text_duplication_candidates.csv
    .venv/Scripts/python.exe scripts/scan_text_duplication.py --semantic
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from mock_api.database import SessionLocal  # noqa: E402
from mock_api.experiment_audit import text_similarity  # noqa: E402

_FIELDS = ("paper_a", "paper_b", "jaccard", "title_a", "title_b")
_SEMANTIC_FIELDS = ("paper_a", "paper_b", "cosine", "jaccard", "title_a", "title_b")


def _write_csv(out: Path, fieldnames: tuple[str, ...], rows: list[dict]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"已落盘: {out}（{len(rows)} 行）")


def _scan_semantic(db, args) -> list[dict]:
    """全库语义级重复扫描：embed 全部论文，两两余弦 + 表层 Jaccard 双信号。"""
    from mock_api.semantic_search import embed_batch

    entries = text_similarity._corpus_entries(  # noqa: SLF001 - 同包复用
        db,
        shingle_size=args.shingle_size,
        min_words=text_similarity._MIN_WORDS,  # noqa: SLF001
        limit_papers=args.limit,
    )
    if len(entries) < 2:
        return []
    # _corpus_entries 只带 shingle 集，语义嵌入需要原文 → 逐篇取 full_text
    texts = [
        text_similarity._normalize(  # noqa: SLF001
            text_similarity._fulltext_of(db, pid)  # noqa: SLF001
        )
        for pid, _, _ in entries
    ]
    excerpts = [t[: text_similarity._MAX_EMBED_CHARS] for t in texts]  # noqa: SLF001
    vecs = embed_batch(excerpts)
    if not vecs:
        return []

    import numpy as np

    matrix = np.asarray(vecs, dtype=np.float64)  # (n, 384) 已归一化，cosine=点积
    sims = matrix @ matrix.T
    scored: list[tuple[float, float, int, int]] = []
    n = len(entries)
    for i in range(n):
        for j in range(i + 1, n):
            cosine = float(sims[i, j])
            if cosine < args.cosine_threshold:
                continue
            jaccard = text_similarity._jaccard(entries[i][2], entries[j][2])  # noqa: SLF001
            if jaccard > args.surface_jaccard_max:
                continue  # 表层已高度重叠 → 归表层检测管，不算「换词重写」
            scored.append((cosine, jaccard, i, j))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: list[dict] = []
    for cosine, jaccard, i, j in scored[: args.max_pairs]:
        pid_a, title_a, _ = entries[i]
        pid_b, title_b, _ = entries[j]
        out.append(
            {
                "paper_a": pid_a,
                "paper_b": pid_b,
                "cosine": round(cosine, 4),
                "jaccard": round(jaccard, 4),
                "title_a": title_a,
                "title_b": title_b,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="全库 full_text 两两相似度扫描 → 候选清单 CSV")
    ap.add_argument(
        "--jaccard-threshold", type=float, default=text_similarity.DEFAULT_CORPUS_JACCARD_THRESHOLD
    )
    ap.add_argument("--shingle-size", type=int, default=text_similarity.DEFAULT_SHINGLE_SIZE)
    ap.add_argument("--max-pairs", type=int, default=text_similarity.DEFAULT_CORPUS_MAX_PAIRS)
    ap.add_argument("--limit", type=int, default=0, help="最多扫描的论文数（0=全部）")
    ap.add_argument("--out", default=str(ROOT / "deliverables" / "text_duplication_candidates.csv"))
    ap.add_argument(
        "--semantic",
        action="store_true",
        help="追加语义级重复（换词重写）扫描，输出 deliverables/semantic_duplication_candidates.csv",
    )
    ap.add_argument(
        "--cosine-threshold", type=float, default=text_similarity.DEFAULT_COSINE_THRESHOLD
    )
    ap.add_argument(
        "--surface-jaccard-max", type=float, default=text_similarity.DEFAULT_SURFACE_JACCARD_MAX
    )
    ap.add_argument(
        "--semantic-out", default=str(ROOT / "deliverables" / "semantic_duplication_candidates.csv")
    )
    args = ap.parse_args()

    db = SessionLocal()
    try:
        print("全库全文两两相似度扫描（词级 shingling + Jaccard，倒排索引加速）…")
        pairs = text_similarity.scan_corpus_duplication(
            db,
            shingle_size=args.shingle_size,
            jaccard_threshold=args.jaccard_threshold,
            max_pairs=args.max_pairs,
            limit_papers=args.limit,
        )
        print(f"表层重复候选总数: {len(pairs)}（Jaccard 阈值 {args.jaccard_threshold}）")
        for p in pairs[:10]:
            print(f"  {p['paper_a']} ↔ {p['paper_b']} (jaccard={p['jaccard']})")
        _write_csv(Path(args.out), _FIELDS, pairs)

        if args.semantic:
            print("\n语义级重复扫描（embedding 余弦 + 表层 Jaccard 双信号）…")
            if not text_similarity.semantic_available():
                print(
                    "⚠️ fastembed/向量模型不可用，语义扫描跳过（pip install fastembed 并联网下载模型后可重试）"
                )
                return 0
            sem = _scan_semantic(db, args)
            print(
                f"语义级重复候选总数: {len(sem)}（余弦阈值 {args.cosine_threshold}，表层 Jaccard ≤ {args.surface_jaccard_max}）"
            )
            for p in sem[:10]:
                print(
                    f"  {p['paper_a']} ↔ {p['paper_b']} (cosine={p['cosine']}, jaccard={p['jaccard']})"
                )
            _write_csv(Path(args.semantic_out), _SEMANTIC_FIELDS, sem)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
