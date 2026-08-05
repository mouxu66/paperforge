"""integrity_report 组装与渲染测试（AI 使用声明 + 真实性报告一页纸）。"""

from __future__ import annotations

import json
import time
import zipfile
from datetime import datetime
from io import BytesIO

from mock_api.integrity_report import (
    build_integrity_report,
    render_integrity_batch_zip,
    render_integrity_docx,
    render_integrity_html,
)
from mock_api.models import DepthReviewV4
from mock_api.models import Paper as PaperORM


def _analysis_v2(**overrides) -> dict:
    """构造一份完整的 analysis_v2 结果（对齐 reflection_pipeline 返回结构）。"""
    base = {
        "student_id": "999900000008",
        "student_name": "学生08",
        "paper_title": "TAG-Net 阅读笔记",
        "paper_author": "Zhang",
        "paper_source": "arxiv",
        "bound_paper_id": "2402.09353",
        "report_chars": 1800,
        "paper_chars": 20000,
        "scores": {
            "understanding_accuracy": 0.82,
            "analysis_depth": 0.75,
            "innovative_insights": 0.65,
            "evidence_support": 0.88,
            "fidelity": 0.87,
            "coverage": 0.63,
        },
        "weights": {
            "understanding_accuracy": 0.05,
            "analysis_depth": 0.15,
            "innovative_insights": 0.35,
            "evidence_support": 0.05,
            "fidelity": 0.05,
            "coverage": 0.35,
        },
        "average": 0.71,
        "verdict": "needs_depth",
        "fidelity": 0.87,
        "fidelity_status": "grounded",
        "fidelity_anchors": [],
        "stray_claims": [],
        "copy_ratio": 0.08,
        "copy_sentences": [],
        "truncated": False,
        "effective_evidence_count": 3,
        "hardcoded_overrides": [],
        "parse_failed": False,
        "llm_calls": 1,
        "llm_empty": 0,
        "llm_failed": False,
        "evidence_rejections": {"ok": 3, "from_paper": 0, "not_found": 0},
        "coverage": 0.63,
        "coverage_status": "partial",
        "coverage_covered": ["核心方法"],
        "coverage_uncovered": ["实验设置细节"],
        "sections_present": ["q", "tech", "exp", "reflection"],
        "ai_likelihood": 0.42,
        "ai_likelihood_tier": "uncertain",
        "ai_likelihood_signals": {
            "connector_density": {"score": 0.3, "hits": ["首先"], "weight": 0.22}
        },
        "ai_likelihood_note": "仅供参考",
    }
    base.update(overrides)
    return base


def _seed_report(db, reflection_result=None, paper_id: str = "report_x") -> DepthReviewV4:
    """写入一条 kind='report' 评审记录（默认带完整 analysis_v2）。"""
    rev = DepthReviewV4(
        paper_id=paper_id,
        kind="report",
        status="completed",
        reflection_result=reflection_result or {"analysis_v2": _analysis_v2()},
        completed_at=datetime(2026, 8, 5, 10, 0, 0),
    )
    db.add(rev)
    db.commit()
    return rev


class TestBuild:
    def test_missing_record_returns_none(self, db_session):
        assert build_integrity_report(db_session, "nope") is None

    def test_full_analysis(self, db_session):
        _seed_report(db_session)
        r = build_integrity_report(db_session, "report_x")
        assert r is not None
        assert r["report_type"] == "reflection"
        assert r["student"] == {"id": "999900000008", "name": "学生08"}
        assert r["scoring"]["trusted"] is True
        assert r["scoring"]["trust_warnings"] == []
        assert r["ai_use"]["mode"] == "post_hoc"
        assert r["ai_use"]["provenance_available"] is False
        assert r["ai_use"]["advisory"]["ai_likelihood"] == 0.42
        assert r["authenticity"]["fidelity"] == 0.87
        assert r["authenticity"]["coverage_uncovered"] == ["实验设置细节"]
        # 未建原论文行 → source_paper 为 None，不抛异常
        assert r["source_paper"] is None

    def test_llm_failed_marks_untrusted(self, db_session):
        _seed_report(
            db_session,
            {"analysis_v2": _analysis_v2(llm_failed=True, llm_empty=2)},
        )
        r = build_integrity_report(db_session, "report_x")
        assert r["scoring"]["trusted"] is False
        assert any("LLM" in w for w in r["scoring"]["trust_warnings"])

    def test_evidence_shortage_warning(self, db_session):
        _seed_report(db_session, {"analysis_v2": _analysis_v2(effective_evidence_count=1)})
        r = build_integrity_report(db_session, "report_x")
        assert any("证据不足" in w for w in r["scoring"]["trust_warnings"])

    def test_parse_failed_warning(self, db_session):
        _seed_report(db_session, {"analysis_v2": _analysis_v2(parse_failed=True)})
        r = build_integrity_report(db_session, "report_x")
        assert any("解析失败" in w for w in r["scoring"]["trust_warnings"])

    def test_truncated_warning(self, db_session):
        _seed_report(db_session, {"analysis_v2": _analysis_v2(truncated=True)})
        r = build_integrity_report(db_session, "report_x")
        assert any("输入上限" in w for w in r["scoring"]["trust_warnings"])

    def test_string_llm_failed_is_not_truthy(self, db_session):
        """老库若把 llm_failed 存成字符串 'false'，绝不能误判为 True。"""
        _seed_report(db_session, {"analysis_v2": _analysis_v2(llm_failed="false")})
        r = build_integrity_report(db_session, "report_x")
        assert r["scoring"]["trusted"] is True

    def test_fallback_to_base_scores_without_analysis_v2(self, db_session):
        """老记录只有 4 维 scores（无 analysis_v2）时回退顶层字段。"""
        _seed_report(
            db_session,
            {
                "scores": {
                    "understanding_accuracy": 0.5,
                    "analysis_depth": 0.5,
                    "innovative_insights": 0.5,
                    "evidence_support": 0.5,
                    "average": 0.5,
                },
                "verdict": "needs_evidence",
                "summary_short": "s",
            },
        )
        r = build_integrity_report(db_session, "report_x")
        assert r is not None
        assert r["scoring"]["scores"]["understanding_accuracy"] == 0.5
        assert r["scoring"]["verdict"] == "needs_evidence"

    def test_legacy_string_reflection_result(self, db_session):
        """老库可能把 reflection_result 存成 JSON 字符串，需兼容。"""
        _seed_report(db_session, json.dumps({"analysis_v2": _analysis_v2()}))
        r = build_integrity_report(db_session, "report_x")
        assert r is not None
        assert r["scoring"]["trusted"] is True

    def test_source_paper_metadata(self, db_session):
        db_session.add(
            PaperORM(
                id="2402.09353",
                title="TAG-Net",
                authors=["Zhang", "Li"],
                year=2024,
                source="arxiv",
            )
        )
        db_session.commit()
        _seed_report(db_session)
        r = build_integrity_report(db_session, "report_x")
        sp = r["source_paper"]
        assert sp is not None
        assert sp["title"] == "TAG-Net"
        assert sp["author"] == "Zhang, Li"

    def test_garbage_reflection_result_no_crash(self, db_session):
        _seed_report(db_session, {"analysis_v2": "not a dict"})
        r = build_integrity_report(db_session, "report_x")
        assert r is not None
        assert r["scoring"]["verdict"] == "needs_evidence"


class TestRender:
    def test_docx_is_valid_python_docx(self, db_session):
        _seed_report(db_session)
        r = build_integrity_report(db_session, "report_x")
        data = render_integrity_docx(r)
        assert data[:2] == b"PK"  # docx = zip 容器
        from docx import Document

        doc = Document(BytesIO(data))
        texts = [p.text for p in doc.paragraphs]
        assert any("AI 使用声明" in t for t in texts)
        assert any("学生08" in t for t in texts)
        assert any("真实性核验" in t for t in texts)

    def test_html_escapes_user_content(self, db_session):
        _seed_report(
            db_session,
            {"analysis_v2": _analysis_v2(student_name="<script>alert(1)</script>")},
        )
        r = build_integrity_report(db_session, "report_x")
        out = render_integrity_html(r)
        assert "&lt;script&gt;" in out
        assert "<script>alert" not in out

    def test_html_contains_key_sections(self, db_session):
        _seed_report(db_session)
        r = build_integrity_report(db_session, "report_x")
        out = render_integrity_html(r)
        assert "AI 使用声明" in out
        assert "真实性核验" in out
        assert "评审结论" in out


class TestBatchExport:
    """render_integrity_batch_zip + 批量端点（zip 打包每生一份 docx）。"""

    def test_batch_zip_contains_each_student(self, db_session):
        # 两篇报告必须是不同的学生，zip 内才不会有重名文件
        _seed_report(
            db_session,
            paper_id="report_a",
            reflection_result={
                "analysis_v2": _analysis_v2(student_id="999900000008", student_name="学生08")
            },
        )
        _seed_report(
            db_session,
            paper_id="report_b",
            reflection_result={
                "analysis_v2": _analysis_v2(student_id="999900000011", student_name="学生11")
            },
        )
        zpath = render_integrity_batch_zip(db_session)
        try:
            with zipfile.ZipFile(zpath) as zf:
                names = zf.namelist()
                assert len(names) == 2
                assert len(set(names)) == 2  # 无重名条目
                for n in names:
                    assert n.endswith("_诚信报告.docx")
                    assert zf.read(n)[:2] == b"PK"  # 每份都是合法 docx
        finally:
            zpath.unlink(missing_ok=True)

    def test_batch_empty_zip_has_readme(self, db_session):
        """没有任何报告记录时，zip 内含 README 说明而非空包。"""
        zpath = render_integrity_batch_zip(db_session)
        try:
            with zipfile.ZipFile(zpath) as zf:
                assert "README.txt" in zf.namelist()
        finally:
            zpath.unlink(missing_ok=True)

    def test_batch_zip_dedupes_reruns(self, db_session):
        """同一报告被重评多次（多个同名记录）时，zip 只保留最新一份。"""
        # 同 paper_id 两条记录：先旧后新（created_at 由主键自增决定顺序）
        _seed_report(
            db_session,
            paper_id="report_x",
            reflection_result={"analysis_v2": _analysis_v2(student_id="999900000008", average=0.5)},
        )
        _seed_report(
            db_session,
            paper_id="report_x",
            reflection_result={"analysis_v2": _analysis_v2(student_id="999900000008", average=0.9)},
        )
        zpath = render_integrity_batch_zip(db_session)
        try:
            with zipfile.ZipFile(zpath) as zf:
                names = zf.namelist()
                assert len(names) == 1  # 去重：不因重评产生重名条目
        finally:
            zpath.unlink(missing_ok=True)

    def test_batch_zip_sanitizes_filenames(self, db_session):
        """学号/姓名含非法字符时必须消毒，zip 文件名不能注入路径分隔符。"""
        _seed_report(db_session, {"analysis_v2": _analysis_v2(student_name="张/三\\李四")})
        zpath = render_integrity_batch_zip(db_session)
        try:
            with zipfile.ZipFile(zpath) as zf:
                names = zf.namelist()
                assert len(names) == 1
                assert "/" not in names[0] and "\\" not in names[0]
        finally:
            zpath.unlink(missing_ok=True)

    def test_endpoint_flow(self, client, db_session):
        """start → 轮询 progress → 下载 zip 的完整链路。"""
        _seed_report(
            db_session,
            paper_id="report_a",
            reflection_result={
                "analysis_v2": _analysis_v2(student_id="999900000008", student_name="学生08")
            },
        )
        _seed_report(
            db_session,
            paper_id="report_b",
            reflection_result={
                "analysis_v2": _analysis_v2(student_id="999900000011", student_name="学生11")
            },
        )
        resp = client.post("/api/reports/integrity/export-batch")
        assert resp.status_code == 202
        task_id = resp.json()["task_id"]

        status = "running"
        for _ in range(200):
            p = client.get(f"/api/reports/integrity/export-batch/{task_id}/progress").json()
            status = p["status"]
            if status in ("done", "error"):
                break
            time.sleep(0.05)
        assert status == "done", p
        assert p["progress"] == 100

        dl = client.get(f"/api/reports/integrity/export-batch/{task_id}/download")
        assert dl.status_code == 200
        assert dl.headers["content-type"] == "application/zip"
        assert dl.content[:2] == b"PK"

    def test_endpoint_missing_task_404(self, client):
        """不存在的任务 → 404（progress 与 download 均如此）。"""
        assert client.get("/api/reports/integrity/export-batch/nope/progress").status_code == 404
        assert client.get("/api/reports/integrity/export-batch/nope/download").status_code == 404


class TestEndpoints:
    def test_get_missing_404(self, client):
        resp = client.get("/api/reports/nope/integrity")
        assert resp.status_code == 404

    def test_get_and_export_docx_and_html(self, client, db_session):
        _seed_report(db_session)
        # JSON 预览
        resp = client.get("/api/reports/report_x/integrity")
        assert resp.status_code == 200
        assert resp.json()["student"]["id"] == "999900000008"
        # docx 导出（默认）
        r2 = client.post("/api/reports/report_x/integrity/export")
        assert r2.status_code == 200
        assert r2.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert r2.content[:2] == b"PK"
        # html 导出
        r3 = client.post("/api/reports/report_x/integrity/export", params={"format": "html"})
        assert r3.status_code == 200
        assert "AI 使用声明" in r3.text
        # 非法 format → 422（Query pattern 校验）
        r4 = client.post("/api/reports/report_x/integrity/export", params={"format": "pdf"})
        assert r4.status_code == 422
