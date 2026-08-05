"""将论文表中的历史 source-based / CS 子类 category 迁移到大学科门类。

运行方式：
    python scripts/migrate_categories_to_disciplines.py [--db PATH]

说明：
- 本脚本只改 category 列，不影响 source 列。
- 对于无法识别的旧分类，统一归为 interdisciplinary。
- 已经是大门类（如 engineering / science / medicine 等）的记录不会被修改。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把项目根目录加入路径，以便导入 mock_api
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402


# 旧分类 -> 大学科门类
# 注意：report 是感悟报告专用分类，必须保留，不要映射到学科门类。
LEGACY_TO_DISCIPLINE: dict[str, str] = {
    # 特殊兜底值
    "all": "interdisciplinary",  # 早期 seed 数据/默认值的兜底

    # source-based
    "arxiv": "engineering",
    "upload": "interdisciplinary",
    "cnki": "interdisciplinary",
    "google_scholar": "interdisciplinary",
    "web_clipper": "interdisciplinary",
    "pubmed": "medicine",
    "ieee": "engineering",
    "springer": "interdisciplinary",
    # CS / AI 子类
    "cs": "engineering",
    "computer_science": "engineering",
    "llm": "engineering",
    "lora": "engineering",
    "quant": "engineering",
    "ri": "engineering",
    "rl": "engineering",
    "cv": "engineering",
    "comm": "engineering",
    "nlp": "engineering",
    "audio": "engineering",
    "speech": "engineering",
    "agent": "engineering",
    "agents": "engineering",
    "robot": "engineering",
    "robotics": "engineering",
    "multimodal": "engineering",
    "gan": "engineering",
    "gen": "engineering",
    "generation": "engineering",
    "diffusion": "engineering",
    "transformer": "engineering",
    "bert": "engineering",
    "gpt": "engineering",
    "rag": "engineering",
    "search": "engineering",
    "db": "engineering",
    "database": "engineering",
    "graph": "engineering",
    "kg": "engineering",
    "knowledge_graph": "engineering",
    "time_series": "engineering",
    "optimization": "engineering",
    "security": "engineering",
    "privacy": "engineering",
    "systems": "engineering",
    "cloud": "engineering",
    "edge": "engineering",
    "federated": "engineering",
    "hardware": "engineering",
    "software": "engineering",
    "code": "engineering",
    "programming": "engineering",
    "algorithm": "engineering",
    "data_mining": "engineering",
    "hci": "engineering",
    # 理科 / 医科 / 社科 子类
    "bio": "medicine",
    "biology": "science",
    "medical": "medicine",
    "medicine": "medicine",
    "chemistry": "science",
    "physics": "science",
    "quantum": "science",
    "neuroscience": "medicine",
    "math": "science",
    "theory": "science",
    "climate": "science",
    # 其他
    "social": "law",
    "economics": "economics",
    "finance": "economics",
    "education": "education",
    "environment": "agriculture",
    "energy": "engineering",
    "game": "economics",
}

# 已经是大类门的值，不需要再改
DISCIPLINES = {
    "philosophy",
    "economics",
    "law",
    "education",
    "literature",
    "history",
    "science",
    "engineering",
    "agriculture",
    "medicine",
    "military",
    "management",
    "arts",
    "interdisciplinary",
}


def _target_for_category(cat: str | None) -> str | None:
    """返回 category 应该被迁移到的目标门类。

    - 已经是大类门的值：保持不动（返回 None 表示无需迁移）。
    - 空字符串 / NULL：归为 interdisciplinary。
    - report：必须保留，返回 None。
    - 其他旧分类：按 LEGACY_TO_DISCIPLINE 映射，未命中则默认 interdisciplinary。
    """
    if cat is None:
        return "interdisciplinary"
    cat = cat.strip()
    if not cat:
        return "interdisciplinary"
    if cat in DISCIPLINES:
        return None  # 已是大类，不动
    if cat == "report":
        return None  # 感悟报告专用分类，保留
    return LEGACY_TO_DISCIPLINE.get(cat, "interdisciplinary")


def migrate(db_path: str, dry_run: bool = False) -> dict[str, int]:
    engine = create_engine(f"sqlite:///{db_path}")
    updated: dict[str, int] = {}

    with engine.connect() as conn:
        # 一次性读出需要迁移的 category 值（包含空字符串）
        rows = conn.execute(
            text("SELECT DISTINCT category FROM papers")
        ).fetchall()
        categories = {row[0] for row in rows}

        for cat in categories:
            target = _target_for_category(cat)
            if target is None:
                continue  # 不需要迁移

            # 区分空字符串与 NULL
            if cat is None:
                where_clause = "category IS NULL"
                params: dict[str, object] = {}
            elif cat == "":
                where_clause = "category = :cat"
                params = {"cat": ""}
            else:
                where_clause = "category = :cat"
                params = {"cat": cat}

            if dry_run:
                count = conn.execute(
                    text(f"SELECT COUNT(*) FROM papers WHERE {where_clause}"), params
                ).scalar()
            else:
                result = conn.execute(
                    text(f"UPDATE papers SET category = :target WHERE {where_clause}"),
                    {"target": target, **params},
                )
                count = result.rowcount
                conn.commit()
            updated[str(cat)] = count or 0
            print(f"{'[dry-run] ' if dry_run else ''}{cat!r} -> {target!r}: {count} 篇")

    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移论文 category 到大学科门类")
    parser.add_argument("--db", default="mock_api/paperforge_mock.db", help="SQLite 数据库路径")
    parser.add_argument("--dry-run", action="store_true", help="仅预览不修改")
    args = parser.parse_args()

    updated = migrate(args.db, dry_run=args.dry_run)
    print("\n迁移完成：")
    print(f"共处理 {len(updated)} 种旧分类，涉及 {sum(updated.values())} 篇论文")


if __name__ == "__main__":
    main()
