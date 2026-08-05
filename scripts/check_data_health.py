"""全库数据健康检查脚本。

背景（2026-08-03）：papers.authors 曾因「双重编码」（``json.dumps()`` 写入
SQLAlchemy JSON 列导致再次序列化）产生全站乱码。本脚本扫描数据库里**所有**
JSON 列（及存 JSON 的 TEXT 列），检测类似的结构性脏数据，可选择性自动修复。

检测项（按严重度）：
- ``DOUBLE_ENCODED``  双重编码：JSON 字符串内嵌套数组/对象（如 ``"[\"A\",\"B\"]"``）
- ``TYPE_MISMATCH``   类型不匹配：list 列存了字符串/对象，或 dict 列存了列表
- ``JSON_NULL``       字面 ``'null'`` 字符串（``json.dumps(None)`` 残渣，应存 SQL NULL）
- ``NULL_VIOLATION``  ORM 声明非空的列存了 NULL
- ``NOT_JSON``        非法 JSON：无法 ``json.loads`` 的裸文本
- ``CHAR_ARRAY``      逐字符拆分数组（``list('["A"]')`` 的副作用）
- ``QUOTED_ELEMENT``  列表元素带包裹引号（如 ``"Alice"``）
- ``EMPTY_ELEMENT``   列表含空字符串/纯空白元素
- ``EMPTY``           整列存了空字符串
- ``UNEXPECTED_TYPE`` 存储类型不是字符串（理论 JSON 列均为 TEXT）
- ``EMBEDDING_DIM``   向量维度异常（embedding TEXT 列）
- ``VECTOR_TYPE``     向量元素非数值

说明：
- NULL 检查以 **ORM nullable 定义**为准：nullable=True 的列（如 ``tasks.result``
  未完成时为 NULL、``q*_result`` 在 kind='report' 时为 NULL）存 NULL 是合法的。
- ``--fix`` 仅归一化可安全自动修复的 list/dict/JSON_NULL 问题，不做内容猜测。

用法：
    python scripts/check_data_health.py                 # 检查并输出报告
    python scripts/check_data_health.py --dry-run       # 预览将修复的行（不写库）
    python scripts/check_data_health.py --fix           # 修复可安全自动修复的脏数据
    python scripts/check_data_health.py --db <path>     # 指定数据库（默认开发库）
    python scripts/check_data_health.py --limit <n>     # 每列最多扫描 n 行

退出码：0=无问题；1=发现需注意的问题；2=脚本自身错误。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

# 确保 mock_api 可导入（发现 ORM 表结构）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mock_api.models as models  # noqa: E402  (导入以注册全部 ORM 表)

# ---------------------------------------------------------------------------
# 列期望类型（'list' / 'dict' / 'any'）；未列出的 JSON 列默认 'any'
# ---------------------------------------------------------------------------
EXPECT: dict[tuple[str, str], str] = {
    ("papers", "authors"): "list",
    ("papers", "tags"): "list",
    ("papers", "fields_of_study"): "list",
    ("writing_projects", "keywords"): "list",
    ("user_templates", "chapters"): "list",
    ("hotspot_configs", "keywords"): "list",
    ("depth_scores", "keywords"): "list",
    ("depth_scores", "missing_items"): "list",
    ("depth_scores", "critique_points"): "list",
    ("depth_scores", "defense_points"): "list",
    ("pdf_annotations", "quadpoints"): "list",
    ("api_keys", "scopes"): "list",
    ("paper_figures", "axis_info"): "dict",
    ("paper_figures", "claim_validation"): "dict",
    ("tasks", "params"): "dict",
    ("tasks", "result"): "dict",
    ("depth_reviews_v4", "reflection_result"): "dict",
    # v4.1 九节点结果均为 dict（v4.2 后部分为 NodeOutput dict）
    # 注意：模型列只有 q0~q4 + q5a/q5b/q5c（无独立 q5_result）
    **{
        ("depth_reviews_v4", f"q{i}_result"): "dict"
        for i in range(0, 5)
    },
    ("depth_reviews_v4", "q5a_result"): "dict",
    ("depth_reviews_v4", "q5b_result"): "dict",
    ("depth_reviews_v4", "q5c_result"): "dict",
    # 证据池为 [{id, content/claim_ref, ...}] 列表
    ("depth_reviews_v4", "evidence_pool"): "list",
    ("depth_reviews_v4", "final_verdict"): "dict",
}

# JSON 数组字符串列（TEXT 列但存 JSON 序列化向量）
JSON_TEXT_COLUMNS: dict[tuple[str, str], str] = {
    ("paper_embeddings", "embedding"): "vector",
    ("paper_figures", "embedding"): "vector",
}


# ---------------------------------------------------------------------------
# 纯分析函数（可单测）
# ---------------------------------------------------------------------------
def _looks_like_char_array(items: list) -> bool:
    """判断列表是否是由逐字符拆分产生的脏数据。

    要求元素均为超短字符串 **且** 拼接后可解析为结构化 JSON（数组/对象），
    避免把合法的单字母列表（如 ``["A", "B", "C"]`` 首字母缩写）误报。
    """
    if not items or not all(isinstance(i, str) and len(i) <= 2 for i in items):
        return False
    joined = "".join(items).strip()
    if not joined:
        return False
    try:
        inner = json.loads(joined)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(inner, (list, dict))


def analyze_json_value(
    raw: Any,
    expect: str = "any",
    *,
    table: str = "",
    column: str = "",
    nullable: bool = True,
) -> list[tuple[str, str]]:
    """分析一个 JSON 列的原始存储值，返回 [(code, detail), ...]。

    传入的 raw 为数据库原始文本（不经 ORM 反序列化），以便检测双重编码。
    nullable 为该列在 ORM 中的可空性（False 时 NULL 视为违规）。
    """
    issues: list[tuple[str, str]] = []

    def add(code: str, detail: str) -> None:
        issues.append((code, detail))

    if raw is None:
        if not nullable:
            add("NULL_VIOLATION", f"{table}.{column} ORM 非空列存了 NULL")
        return issues

    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            add("EMPTY", f"{table}.{column} 存了空字符串")
            return issues
    else:
        # 非字符串（理论上 JSON 列都是 TEXT 存储；防意外类型）
        add("UNEXPECTED_TYPE", f"{table}.{column} 存储类型 {type(raw).__name__}")
        return issues

    # 1. 尝试 JSON 解析
    try:
        parsed = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        # 裸文本：list 列常见于「逗号串」；dict 列基本不可修复
        if expect == "list":
            add("NOT_JSON", f"{table}.{column} 非 JSON 文本（疑似逗号串）: {s[:80]!r}")
        else:
            add("NOT_JSON", f"{table}.{column} 非法 JSON: {s[:80]!r}")
        return issues

    # 2. 字面 'null' 字符串（json.dumps(None) 残渣）
    if parsed is None:
        add("JSON_NULL", f"{table}.{column} 存了字面 'null' 字符串（应存 SQL NULL）")
        return issues

    # 3. 解析成功且非 null
    if isinstance(parsed, str):
        inner = parsed.strip()
        if inner.startswith(("[", "{")):
            # 双重编码：JSON 字符串内嵌套数组/对象
            try:
                inner_parsed = json.loads(inner)
                inner_shape = "list" if isinstance(inner_parsed, list) else (
                    "dict" if isinstance(inner_parsed, dict) else "scalar"
                )
            except (json.JSONDecodeError, ValueError):
                inner_shape = "unparseable"
            add("DOUBLE_ENCODED", f"{table}.{column} 双重编码（字符串包 {inner_shape}）: {s[:80]!r}")
            # 递归分析内层以发现更深问题
            issues.extend(
                analyze_json_value(inner, expect, table=table, column=column, nullable=nullable)
            )
        else:
            # JSON 字符串但非数组/对象
            if expect == "list":
                # 引号包裹的逗号串，如 "Edward J. Hu, Yelong Shen"
                add("TYPE_MISMATCH", f"{table}.{column} list 列存了 JSON 字符串: {s[:80]!r}")
            elif expect == "dict":
                add("TYPE_MISMATCH", f"{table}.{column} dict 列存了 JSON 字符串: {s[:80]!r}")
        return issues

    # 4. 类型期望检查
    if expect == "list" and not isinstance(parsed, list):
        add("TYPE_MISMATCH", f"{table}.{column} 期望 list 实为 {type(parsed).__name__}: {s[:80]!r}")
        return issues
    if expect == "dict" and not isinstance(parsed, dict):
        add("TYPE_MISMATCH", f"{table}.{column} 期望 dict 实为 {type(parsed).__name__}: {s[:80]!r}")
        return issues

    # 5. 列表内容检查
    if isinstance(parsed, list):
        if _looks_like_char_array(parsed):
            add("CHAR_ARRAY", f"{table}.{column} 逐字符拆分数组: {s[:80]!r}")
            return issues
        for i, item in enumerate(parsed):
            if item is None:
                add("EMPTY_ELEMENT", f"{table}.{column}[{i}] 为 null")
                continue
            if isinstance(item, str):
                if not item.strip():
                    add("EMPTY_ELEMENT", f"{table}.{column}[{i}] 为空字符串")
                elif len(item) >= 2 and item.startswith('"') and item.endswith('"'):
                    add("QUOTED_ELEMENT", f"{table}.{column}[{i}] 带包裹引号: {item!r}")
            # 非字符串元素（list 列里的数字/对象）→ 视列类型而定，不强制
        return issues

    # 6. dict 内容：可选的深层检查（如 q*_result 是否含 node_name），暂不猜测
    return issues


def analyze_vector_value(
    raw: Any,
    expected_dim: int | None = None,
    *,
    nullable: bool = True,
) -> list[tuple[str, str]]:
    """分析向量 TEXT 列（JSON 序列化的 float 数组）。"""
    issues: list[tuple[str, str]] = []
    if raw is None:
        if not nullable:
            issues.append(("NULL_VIOLATION", "ORM 非空向量列存了 NULL"))
        return issues
    if not isinstance(raw, str) or not raw.strip():
        issues.append(("EMPTY", "向量列为空字符串"))
        return issues
    try:
        vec = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        issues.append(("NOT_JSON", f"向量列非法 JSON: {raw[:60]!r}"))
        return issues
    if not isinstance(vec, list):
        issues.append(("VECTOR_TYPE", f"向量列不是数组: {type(vec).__name__}"))
        return issues
    if expected_dim is not None and len(vec) != expected_dim:
        issues.append(("EMBEDDING_DIM", f"向量维度 {len(vec)} != 期望 {expected_dim}"))
    non_float = [v for v in vec if not isinstance(v, (int, float))]
    if non_float:
        issues.append(("VECTOR_TYPE", f"向量含非数值元素 {len(non_float)} 个"))
    return issues


# ---------------------------------------------------------------------------
# 扫描与报告
# ---------------------------------------------------------------------------
def _discover_columns() -> list[tuple[str, str, str, bool]]:
    """自动发现所有 JSON 列 + JSON_TEXT 列，返回 [(table, column, kind, nullable)]。"""
    from sqlalchemy import JSON as SaJSON

    found: list[tuple[str, str, str, bool]] = []
    for table_name, table in models.Base.metadata.tables.items():
        for col in table.columns:
            if isinstance(col.type, SaJSON):
                kind = EXPECT.get((table_name, col.name), "any")
                found.append((table_name, col.name, kind, bool(col.nullable)))
    for (table_name, col_name), kind in JSON_TEXT_COLUMNS.items():
        nullable = False
        try:
            col = models.Base.metadata.tables[table_name].columns[col_name]
            nullable = bool(col.nullable)
        except KeyError:
            pass
        found.append((table_name, col_name, kind, nullable))
    return found


def _table_has_column(table: str, column: str, cur: sqlite3.Cursor) -> bool:
    rows = cur.execute(
        "SELECT 1 FROM pragma_table_info(?) WHERE name = ?", (table, column)
    ).fetchall()
    return bool(rows)


def scan(
    db_path: Path,
    limit: int | None = None,
    fix: bool = False,
    dry_run: bool = False,
) -> int:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    columns = _discover_columns()
    # column -> [(rowid, code, detail)]
    all_issues: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
    fixed_count = 0
    scanned_total = 0

    for table, column, kind, nullable in columns:
        if not _table_has_column(table, column, cur):
            print(f"  [skip] {table}.{column} 列不存在（旧库未迁移）")
            continue
        rows = cur.execute(
            f'SELECT rowid AS _rid, "{column}" AS v FROM "{table}"'
        ).fetchall()
        if limit is not None:
            rows = rows[:limit]
        scanned_total += len(rows)
        col_issues: list[tuple[int, str, str]] = []
        col_fixed = 0

        for row in rows:
            rid, raw = row["_rid"], row["v"]
            if kind == "vector":
                issues = analyze_vector_value(
                    raw,
                    expected_dim=_dim_for(cur, table, rid),
                    nullable=nullable,
                )
            else:
                issues = analyze_json_value(
                    raw, expect=kind, table=table, column=column, nullable=nullable
                )

            if not issues:
                continue

            # 收集问题（每行最多记 3 条避免刷屏）
            for code, detail in issues[:3]:
                col_issues.append((rid, code, detail))

            # --fix / --dry-run：仅对可安全修复的 issue 归一化
            if (fix or dry_run) and kind != "vector":
                normalized, fixable = _normalize_value(raw, expect=kind)
                if fixable:
                    new_text = (
                        json.dumps(normalized, ensure_ascii=False)
                        if normalized is not None
                        else None
                    )
                    if new_text != raw:
                        if fix:
                            cur.execute(
                                f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                                (new_text, rid),
                            )
                        col_fixed += 1

        if col_issues:
            all_issues[(table, column)] = col_issues
        if col_fixed:
            fixed_count += col_fixed

    if fix:
        conn.commit()
    elif dry_run:
        print(f"  [dry-run] 未写入任何更改（共 {fixed_count} 行将被修复）")
        fixed_count = 0  # 避免下方统计误导

    # 汇总报告
    print("=" * 72)
    print(f"  数据健康检查报告  @ {db_path}")
    print("=" * 72)
    if not all_issues:
        print("  ✅ 未发现问题：所有 JSON 列数据健康。")
    else:
        total = sum(len(v) for v in all_issues.values())
        code_counter: dict[str, int] = {}
        for issues in all_issues.values():
            for _rid, code, _detail in issues:
                code_counter[code] = code_counter.get(code, 0) + 1
        print(f"  发现 {total} 条问题（{len(all_issues)} 列受影响）：")
        for code, count in sorted(code_counter.items(), key=lambda x: -x[1]):
            print(f"    - {code}: {count}")
        print()
        for (table, column), issues in sorted(all_issues.items()):
            # 只展示前 3 行示例，但统计完整
            print(f"  ┌─ {table}.{column}（{len(issues)} 条问题，示例：）")
            for _rid, code, detail in issues[:3]:
                print(f"  │   row {_rid} [{code}]: {detail}")
            print("  └─")
    if fix:
        print(f"  🔧 已修复 {fixed_count} 行（仅归一化可安全修复的 list/dict/null 问题）。")
    print(f"  扫描行数：{scanned_total}；发现问题的列：{len(all_issues)}")
    conn.close()
    return 1 if all_issues else 0


def _dim_for(cur: sqlite3.Cursor, table: str, rowid: int) -> int | None:
    """取 paper_embeddings 行的 dim 列；无则回退 384。"""
    if table == "paper_embeddings":
        try:
            row = cur.execute(
                "SELECT dim FROM paper_embeddings WHERE rowid = ?", (rowid,)
            ).fetchone()
            return int(row["dim"]) if row and row["dim"] else 384
        except Exception:  # noqa: BLE001 - 维度列缺失时回退默认
            return 384
    return 384  # paper_figures.embedding 固定 384 维


def _normalize_value(raw: str | None, expect: str) -> tuple[Any | None, bool]:
    """将脏数据归一化为正确的存储值，返回 (归一化值, 是否可安全修复)。

    支持：字面 'null' → SQL NULL、双重编码解包、逗号串拆分、引号元素解包、
    空元素过滤、逐字符数组重组。仅对 list/dict 列生效；**类型不匹配
    （list 列存 dict 或反之）标记为不可修复**，因为清洗后仍是错误类型。
    无法安全归一化时返回 (None, False)（None 且不可修复表示「无法归一化」，
    与可修复的「归一化为 NULL」区分）。
    """
    if raw is None:
        return None, False
    s = raw.strip()
    if not s:
        return None, False

    try:
        parsed = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        if expect == "list":
            # 裸逗号串 → 拆分为列表
            items = [p.strip().strip('"') for p in s.split(",") if p.strip().strip('"')]
            return (items if items else None), True
        return None, False

    # 字面 'null' → SQL NULL
    if parsed is None:
        return None, True

    # 双重编码 / JSON 字符串 → 递归归一化内层
    if isinstance(parsed, str):
        inner = parsed.strip()
        try:
            inner_parsed = json.loads(inner)
        except (json.JSONDecodeError, ValueError):
            if expect == "list":
                items = [
                    p.strip().strip('"') for p in inner.split(",") if p.strip().strip('"')
                ]
                return (items if items else None), True
            return None, False
        return _normalize_parsed(inner_parsed, expect)

    return _normalize_parsed(parsed, expect)


def _normalize_parsed(parsed: Any, expect: str) -> tuple[Any | None, bool]:
    """对已解析的值做内容归一化（清洗引号/空元素/逐字符数组）。"""
    if isinstance(parsed, list):
        if _looks_like_char_array(parsed):
            # detector 已保证拼接结果可解析且为结构化 JSON
            inner = json.loads("".join(str(p) for p in parsed).strip())
            if isinstance(inner, list):
                return _normalize_parsed(inner, expect)
            return None, False
        # 类型期望：list 列才允许清洗后返回；dict 列存了列表属于类型不匹配，
        # 不可“修复”（清洗后仍是错误类型），返回不可修复避免误导 --fix。
        if expect != "list":
            return None, False
        cleaned: list[Any] = []
        for item in parsed:
            if item is None:
                continue
            if isinstance(item, str):
                s = item.strip().strip('"')
                if s:
                    cleaned.append(s)
            else:
                cleaned.append(item)
        return cleaned, True
    if isinstance(parsed, dict):
        if expect != "dict":
            return None, False  # list 列存了 dict → 类型不匹配，不可修复
        return parsed, True  # dict 内容不猜测，仅解包
    return None, False


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperForge 全库数据健康检查")
    parser.add_argument("--fix", action="store_true", help="修复可安全自动修复的脏数据")
    parser.add_argument(
        "--dry-run", action="store_true", help="预览将修复的行数（不写库）"
    )
    parser.add_argument("--db", type=str, default="", help="数据库路径（默认开发库）")
    parser.add_argument("--limit", type=int, default=None, help="每列最多扫描行数")
    args = parser.parse_args()

    if args.db:
        db_path = Path(args.db)
    else:
        default = ROOT / "mock_api" / "paperforge_mock.db"
        appdata = Path.home() / "AppData" / "Roaming" / "PaperForge" / "paperforge_mock.db"
        db_path = default if default.exists() else appdata
    if not db_path.exists():
        print(f"[错误] 数据库不存在: {db_path}", file=sys.stderr)
        return 2

    try:
        return scan(db_path, limit=args.limit, fix=args.fix, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - 顶层错误兜底
        print(f"[错误] 扫描失败: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
