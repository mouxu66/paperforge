#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PaperForge 小程序数据迁移脚本（元数据版，零 AI）

把本地桌面端 SQLite 论文库（mock_api/paperforge_mock.db）中的「元数据」
导出为腾讯云云开发（CloudBase）可直接导入的 JSON 数组。

只搬元数据：标题 / 作者 / 摘要 / 年份 / 期刊 / 分类 / 标签 / 引用数 / DOI / 来源。
不搬：PDF 全文、向量嵌入、图表、审稿评分等（小程序不存这些，也避免版权风险）。

用法：
    python migrate.py                         # 用默认库，输出 migration_papers.json
    python migrate.py --db /path/to/db.db    # 指定库
    python migrate.py --out my.json          # 指定输出

导入方式（二选一）：
  A. 云开发控制台 -> 数据库 -> papers 集合 -> 导入 -> 选这个 JSON（需先建集合）
  B. 自己写一次性云函数读取该 JSON 后 db.collection('papers').doc(_id).set(doc)
"""

import argparse
import json
import os
import sqlite3
import sys

# 默认库路径：优先项目根目录下的 mock_api/paperforge_mock.db
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "..", "mock_api", "paperforge_mock.db")
DEFAULT_OUT = os.path.join(HERE, "migration_papers.json")


def parse_json_col(raw, default):
    """SQLite 里 JSON 列是 TEXT，尝试解析；失败则回退默认值。"""
    if raw is None:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, (list, dict)) else default
    except (ValueError, TypeError):
        return default


def as_list(raw):
    v = parse_json_col(raw, [])
    return v if isinstance(v, list) else [str(v)]


def epoch_ms(dt_str):
    """把 SQLite 的 datetime 字符串转成毫秒时间戳；失败返回当前时间。"""
    if not dt_str:
        return __import__("time").time_ns() // 1_000_000
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            import datetime
            return int(datetime.datetime.strptime(dt_str, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return __import__("time").time_ns() // 1_000_000


def main():
    ap = argparse.ArgumentParser(description="PaperForge -> CloudBase 元数据迁移")
    ap.add_argument("--db", default=DEFAULT_DB, help="本地 SQLite 路径")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出 JSON 路径")
    args = ap.parse_args()

    db_path = os.path.abspath(args.db)
    if not os.path.exists(db_path):
        print(f"[错误] 找不到数据库: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1) 论文主表
    #    注意：本地 papers.authors / tags 是 JSON 列；pdf_url 仅作链接参考，不下载
    cur.execute(
        """
        SELECT id, title, authors, abstract, category, tags,
               year, journal, citations, source, doi, pdf_url
        FROM papers
        """
    )
    rows = cur.fetchall()

    # 2) 笔记（paper_notes），按 paper_id 归并
    notes_map = {}
    try:
        cur.execute("SELECT paper_id, content, created_at FROM paper_notes")
        for r in cur.fetchall():
            pid = r["paper_id"]
            notes_map.setdefault(pid, []).append(
                {
                    "noteId": f"{pid}-{len(notes_map.get(pid, []))}",
                    "content": r["content"] or "",
                    "createdAt": epoch_ms(r["created_at"]),
                }
            )
    except sqlite3.OperationalError:
        # 没有 paper_notes 表也没关系
        pass

    # 3) 收藏（favorites 表，若存在），收集已收藏的 paper_id
    fav_set = set()
    try:
        cur.execute("SELECT paper_id FROM favorites")
        for r in cur.fetchall():
            fav_set.add(r["paper_id"])
    except sqlite3.OperationalError:
        pass

    docs = []
    skipped = 0
    for r in rows:
        pid = r["id"]
        if not pid or not (r["title"] or "").strip():
            skipped += 1
            continue
        category = r["category"] or "all"
        # 小程序分类筛选用 imported / report / my / all，
        # 本地若用了别的分类值则原样保留（仍会出现在「全部」里）
        doc = {
            "_id": pid,
            "title": (r["title"] or "").strip(),
            "authors": as_list(r["authors"]),
            "abstract": r["abstract"] or "",
            "year": int(r["year"] or 0),
            "journal": r["journal"] or "",
            "category": category,
            "tags": as_list(r["tags"]),
            "favorite": pid in fav_set,
            "notes": notes_map.get(pid, []),
            "citations": int(r["citations"] or 0),
            "source": r["source"] or "local",
            "doi": r["doi"] or "",
            "url": r["pdf_url"] or "",
            "pdfUrl": "",
            "createdAt": epoch_ms(None),
        }
        docs.append(doc)

    conn.close()

    out_path = os.path.abspath(args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)

    print(f"[完成] 读取 {len(rows)} 条，导出 {len(docs)} 条，跳过 {skipped} 条空记录")
    print(f"[输出] {out_path}  ({os.path.getsize(out_path) / 1024:.1f} KB)")
    print("[提示] 在云开发控制台「数据库」新建 papers 集合后，导入此 JSON 即可。")


if __name__ == "__main__":
    main()
