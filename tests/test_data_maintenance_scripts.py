# -*- coding: utf-8 -*-
"""两个数据维护脚本的契约测试。

背景（2026-09-16）
----------------
论文列表里出现 278 篇「0 个文本块 / 0 B」的空白条目，实为 `tests/test_security.py`
等用例历史上写进**真实开发库**的夹具（`ssrf-*` / `rebind-*` / `file-*`，
标题统一 "Test Paper"）。清掉之后又发现 546 篇有正文的论文 `chunk_count` /
`index_size` 仍是 0——因为 `backfill_full_text.py` 只写 `full_text` 没顺手算分块。

本文件锁住两个脚本的**安全性质**与**幂等性**，防止以后改坏：
1. `scripts/cleanup_test_fixture_papers.py` —— 只删空白夹具，护栏一条都不能松：
   - 有正文的行**永不删**（哪怕 id 长得像夹具）
   - `category='fraud_test'` 的行**永不删**（实验审计正式夹具）
   - dry-run 绝不写库
2. `scripts/backfill_chunk_metadata.py` —— 只回填有正文的行，且可重复执行。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLEANUP = ROOT / "scripts" / "cleanup_test_fixture_papers.py"
BACKFILL = ROOT / "scripts" / "backfill_chunk_metadata.py"

PAPERS_DDL = """
CREATE TABLE papers (
    id VARCHAR PRIMARY KEY,
    title VARCHAR,
    authors JSON,
    abstract TEXT,
    category VARCHAR,
    tags JSON,
    year INTEGER,
    journal VARCHAR,
    pdf_url VARCHAR,
    citations INTEGER,
    chunk_count INTEGER DEFAULT 0,
    index_size INTEGER DEFAULT 0,
    source VARCHAR,
    full_text TEXT
)
"""

FTS_DDL = "CREATE TABLE paper_fts (paper_id VARCHAR, title VARCHAR)"


def _seed(path: Path, rows: list[dict]) -> None:
    con = sqlite3.connect(str(path))
    con.execute(PAPERS_DDL)
    con.execute(FTS_DDL)
    for r in rows:
        con.execute(
            "INSERT INTO papers(id, title, category, chunk_count, index_size, full_text) "
            "VALUES (?,?,?,?,?,?)",
            (
                r["id"],
                r.get("title", ""),
                r.get("category", "engineering"),
                r.get("chunk_count", 0),
                r.get("index_size", 0),
                r.get("full_text", ""),
            ),
        )
        con.execute("INSERT INTO paper_fts(paper_id, title) VALUES (?,?)", (r["id"], r.get("title", "")))
    con.commit()
    con.close()


def _run(script: Path, db: Path, diag: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script), "--db", str(db), "--diag-dir", str(diag), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        timeout=180,
    )


def _ids(db: Path) -> set[str]:
    con = sqlite3.connect(str(db))
    out = {r[0] for r in con.execute("SELECT id FROM papers")}
    con.close()
    return out


# ── 夹具库：4 篇该删 + 3 篇必须留下 ──
FIXTURE_ROWS = [
    {"id": "file-aaaaaaaa", "title": "Test Paper"},  # 删
    {"id": "ssrf-bbbbbbbb", "title": "Test Paper"},  # 删
    {"id": "rebind-cccccccc", "title": "Test Paper"},  # 删
    {"id": "clip_cnki_49d9fd75a8557465", "title": "CNKI E2E Curl Test"},  # 删
    # 以下必须留下
    {"id": "file-dddddddd", "title": "Test Paper", "full_text": "REAL BODY TEXT " * 10},
    {"id": "fraud_berberine", "title": "Berberine Improves Kidney", "category": "fraud_test",
     "full_text": "x" * 500},
    {"id": "2109.05370", "title": "A Research and Educational Robotic", "full_text": "y" * 800},
]


@pytest.fixture
def mini(tmp_path: Path):
    db = tmp_path / "mini.db"
    diag = tmp_path / "diag"
    _seed(db, FIXTURE_ROWS)
    return db, diag


class TestCleanupFixtures:
    def test_dry_run_writes_nothing(self, mini):
        db, diag = mini
        before = _ids(db)
        proc = _run(CLEANUP, db, diag)
        assert proc.returncode == 0, proc.stderr
        assert "278" not in proc.stdout  # 无关
        assert _ids(db) == before, "dry-run 不得改库"
        assert not diag.exists() or not list(diag.glob("*.json")), "dry-run 不得写清单"

    def test_apply_removes_only_blank_fixtures(self, mini):
        db, diag = mini
        proc = _run(CLEANUP, db, diag, "--apply")
        assert proc.returncode == 0, proc.stderr
        remaining = _ids(db)
        assert remaining == {"file-dddddddd", "fraud_berberine", "2109.05370"}, remaining

    def test_guard_text_is_never_deleted(self, mini):
        """有正文的行即使 id 像夹具、标题是 Test Paper 也必须留下。"""
        db, diag = mini
        _run(CLEANUP, db, diag, "--apply")
        assert "file-dddddddd" in _ids(db)

    def test_guard_fraud_test_category_untouched(self, mini):
        """fraud_test 是实验审计正式夹具，不得删。"""
        db, diag = mini
        _run(CLEANUP, db, diag, "--apply")
        assert "fraud_berberine" in _ids(db)

    def test_backup_and_manifest_written(self, mini):
        db, diag = mini
        proc = _run(CLEANUP, db, diag, "--apply")
        assert proc.returncode == 0, proc.stderr
        backups = list(db.parent.glob(f"{db.name}.bak_pre_fixture_cleanup_*"))
        assert backups, "必须留下一致性快照"
        assert backups[0].stat().st_size > 0
        manifests = list(diag.glob("test_fixture_cleanup_*.json"))
        assert manifests, "必须留下清单"
        data = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert data["deleted_count"] == 4
        assert data["residue_after"] == 0
        assert data["deleted_rows"]["papers"] == 4

    def test_fts_rows_cleaned_with_papers(self, mini):
        """paper_fts 里的 202 行孤儿索引必须跟着删，不能留悬空 paper_id。"""
        db, diag = mini
        _run(CLEANUP, db, diag, "--apply")
        con = sqlite3.connect(str(db))
        orphan = con.execute(
            "SELECT COUNT(*) FROM paper_fts WHERE paper_id NOT IN (SELECT id FROM papers)"
        ).fetchone()[0]
        con.close()
        assert orphan == 0


class TestBackfillChunkMetadata:
    ROWS = [
        {"id": "has-text", "title": "T", "full_text": "word " * 2000},
        {"id": "already-done", "title": "T", "full_text": "word " * 500,
         "chunk_count": 7, "index_size": 123},
        {"id": "no-text", "title": "T", "full_text": ""},
    ]

    @pytest.fixture
    def mini2(self, tmp_path: Path):
        db = tmp_path / "mini2.db"
        diag = tmp_path / "diag2"
        _seed(db, self.ROWS)
        return db, diag

    def _row(self, db: Path, pid: str):
        con = sqlite3.connect(str(db))
        r = con.execute(
            "SELECT chunk_count, index_size FROM papers WHERE id=?", (pid,)
        ).fetchone()
        con.close()
        return r

    def test_dry_run_writes_nothing(self, mini2):
        db, diag = mini2
        proc = _run(BACKFILL, db, diag)
        assert proc.returncode == 0, proc.stderr
        assert self._row(db, "has-text") == (0, 0)

    def test_apply_fills_text_backed_rows_only(self, mini2):
        db, diag = mini2
        proc = _run(BACKFILL, db, diag, "--apply")
        assert proc.returncode == 0, proc.stderr
        cc, sz = self._row(db, "has-text")
        assert cc > 0, "有正文的行必须被回填"
        assert sz > 0
        assert self._row(db, "no-text") == (0, 0), "无正文的行保持 0（不伪造 1 块）"

    def test_apply_preserves_existing_values(self, mini2):
        db, diag = mini2
        _run(BACKFILL, db, diag, "--apply")
        assert self._row(db, "already-done") == (7, 123), "已有值的行不得被覆盖"

    def test_idempotent(self, mini2):
        db, diag = mini2
        _run(BACKFILL, db, diag, "--apply")
        first = self._row(db, "has-text")
        proc = _run(BACKFILL, db, diag, "--apply")
        assert proc.returncode == 0, proc.stderr
        assert "无需回填" in proc.stdout or "待回填论文: 0" in proc.stdout
        assert self._row(db, "has-text") == first

    def test_manifest_and_backup_written(self, mini2):
        db, diag = mini2
        proc = _run(BACKFILL, db, diag, "--apply")
        assert proc.returncode == 0, proc.stderr
        assert list(db.parent.glob(f"{db.name}.bak_pre_chunk_backfill_*"))
        manifests = list(diag.glob("chunk_metadata_backfill_*.json"))
        assert manifests
        data = json.loads(manifests[0].read_text(encoding="utf-8"))
        assert data["updated_count"] == 1
        assert data["integrity_check"] == "ok"
