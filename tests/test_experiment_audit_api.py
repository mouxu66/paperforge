"""实验审计 API 端点集成测试（TestClient + 同步 worker 路径）。

覆盖：
- POST run → task_id，worker 执行完成后 result 可查
- GET result 404 / list / report HTML / leakage 400 / finding-types

⚠️ worker 执行方式：测试库为 StaticPool 单连接内存库，所有 Session（含
TaskManager 后台 worker 线程）共享同一物理 SQLite 连接。sqlite3 驱动对同一
连接并发执行不是线程安全的——后台 worker 与请求/轮询会话并发时会随机出现
StaleDataError（UPDATE 匹配 0 行）/ ObjectDeletedError / IndexError（游标交错）。
因此本文件用 sync_task_submit 夹具把 TaskManager.submit 替换为同步执行：
worker 逻辑、落库、端点全部真实运行，仅与请求同线程串行化，从根本上消除并发。
（与 tests/test_workers_export.py 的 mock TaskManager 模式一致。）

取舍说明：全库其他任务测试均 mock 掉 TaskManager，因此真实异步路径（后台线程、
SSE 进度广播、pending→running→completed 状态机、超时 watchdog）在整个测试套件
中本就没有覆盖；本文件同步化后亦然。如需覆盖真异步行为，应新建独立测试文件，
用文件型临时 SQLite（QueuePool 多连接）而非 StaticPool 跑真实 worker。
"""

from __future__ import annotations

import time

import pytest

from mock_api.models import Paper
from mock_api.tasks import TaskManager

# 含 P/R/F1 不自洽句 → 必有 METRIC_INCONSISTENCY finding
_AUDIT_TEXT = (
    "Our approach achieves precision = 82.0, recall = 85.0 and F1 = 84.6 "
    "on the benchmark. We train with batch size 32 for 50 epochs on a "
    "single GPU with learning rate 3e-4."
)


@pytest.fixture
def sync_task_submit(monkeypatch):
    """让 TaskManager.submit 同步执行 worker（不落后台线程）。

    与真实 submit 的唯一差异：worker_fn 在调用线程内立即执行，而不是投递到
    ThreadPoolExecutor。任务行照常写入 tasks 表，worker 照常调 complete/fail。
    """
    import uuid

    from mock_api import tasks as tasks_mod
    from mock_api.database import SessionLocal
    from mock_api.models import Task as TaskORM

    def _sync_submit(task_type, params=None, worker_fn=None):
        task_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            db.add(
                TaskORM(
                    id=task_id,
                    type=task_type,
                    status="pending",
                    progress=0,
                    params=params or {},
                )
            )
            db.commit()
        finally:
            db.close()
        if worker_fn:
            try:
                worker_fn(task_id, params or {})
            except Exception as e:  # noqa: BLE001 - 对齐 _run_worker 的异常兜底
                tasks_mod.TaskManager.fail(task_id, str(e)[:2000])
        else:
            tasks_mod.TaskManager.fail(task_id, f"不支持的任务类型: {task_type}")
        return task_id

    monkeypatch.setattr(tasks_mod.TaskManager, "submit", staticmethod(_sync_submit))


def _seed_paper(db_session, paper_id: str = "p-api") -> str:
    db_session.add(Paper(id=paper_id, title="API Audit Paper", full_text=_AUDIT_TEXT))
    db_session.commit()
    return paper_id


def _wait_task(task_id: str, timeout: float = 20.0) -> dict:
    """读取任务终态。

    配合 sync_task_submit 夹具：worker 已同步执行完毕，任务应为终态；
    这里单次读取即可。保留有界轮询兜底（防夹具遗漏时无限等待）。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = TaskManager.get(task_id)
        if task and task["status"] in ("completed", "failed"):
            return task
        time.sleep(0.2)
    raise AssertionError(f"任务 {task_id} 在 {timeout}s 内未完成")


class TestAuditEndpoints:
    def test_run_and_result(self, client, db_session, sync_task_submit):
        paper_id = _seed_paper(db_session)
        resp = client.post(f"/api/experiment-audit/run/{paper_id}")
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        task = _wait_task(task_id)
        assert task["status"] == "completed", task.get("error")

        result = client.get(f"/api/experiment-audit/result/{paper_id}")
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "completed"
        assert body["paper_title"] == "API Audit Paper"
        types = {f["type"] for f in body["findings"]}
        assert "METRIC_INCONSISTENCY" in types
        assert body["findings"][0]["finding_id"] == "F-001"

    def test_run_missing_paper_404(self, client):
        resp = client.post("/api/experiment-audit/run/no-such-paper")
        assert resp.status_code == 404
        # 不提交任务，无需等待

    def test_result_without_audit_404(self, client):
        assert client.get("/api/experiment-audit/result/never-audited").status_code == 404

    def test_list_endpoint(self, client, db_session, sync_task_submit):
        paper_id = _seed_paper(db_session, "p-list")
        task_id = client.post(f"/api/experiment-audit/run/{paper_id}").json()["task_id"]
        task = _wait_task(task_id)
        assert task["status"] == "completed", task.get("error")
        resp = client.get("/api/experiment-audit/list")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert body["items"][0]["paper_title"] == "API Audit Paper"

    def test_report_html(self, client, db_session, sync_task_submit):
        paper_id = _seed_paper(db_session, "p-report")
        task_id = client.post(f"/api/experiment-audit/run/{paper_id}").json()["task_id"]
        task = _wait_task(task_id)
        assert task["status"] == "completed", task.get("error")
        audit_id = client.get(f"/api/experiment-audit/result/{paper_id}").json()["audit_id"]

        resp = client.get(f"/api/experiment-audit/report/{audit_id}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        html = resp.text
        assert "论文实验审计报告" in html
        assert "METRIC_INCONSISTENCY" in html
        assert "良性解释" in html  # 免责列存在
        assert "API Audit Paper" in html

    def test_report_missing_404(self, client):
        assert client.get("/api/experiment-audit/report/nope").status_code == 404

    def test_leakage_bad_dir_400(self, client):
        resp = client.post(
            "/api/experiment-audit/leakage",
            json={"train_dir": "C:/no/such/train", "test_dir": "C:/no/such/test"},
        )
        assert resp.status_code == 400

    def test_result_injects_evidence_image_url(self, client, db_session):
        """读路径注入：_audit snippet（含 Windows 反斜杠）→ evidence image_url。"""
        from mock_api.experiment_audit.schemas import assign_finding_ids, make_finding
        from mock_api.models import ExperimentAudit

        paper_id = _seed_paper(db_session, "p-evidence-url")
        finding = make_finding(
            "CHART_AXIS_RISK",
            title="Figure 1 broken_axis",
            evidence_sources=[
                # Windows 形态（生成机器写入 DB 的真实形态）
                {"type": "figure", "snippet": "C:\\data\\uploads\\figures\\p-evidence-url\\_audit\\fig1_broken_axis.png"},
                # Linux 形态
                {"type": "figure", "snippet": "/srv/uploads/figures/p-evidence-url/_audit/fig2_scale.png"},
                # 非证据图路径：不动
                {"type": "text", "snippet": "正文证据片段"},
            ],
        )
        db_session.add(
            ExperimentAudit(paper_id=paper_id, status="completed", findings=assign_finding_ids([finding]))
        )
        db_session.commit()

        body = client.get(f"/api/experiment-audit/result/{paper_id}").json()
        evs = [e for e in body["findings"][0]["evidence_sources"]]
        assert evs[0]["image_url"] == "/api/experiment-audit/evidence-image/p-evidence-url/fig1_broken_axis.png"
        assert evs[1]["image_url"] == "/api/experiment-audit/evidence-image/p-evidence-url/fig2_scale.png"
        assert "image_url" not in evs[2] or evs[2]["image_url"] is None

    def test_evidence_image_serve(self, client, db_session, tmp_path, monkeypatch):
        """证据图端点：200 + PNG；非法文件名 400；不存在 404；论文不存在 404。"""
        from mock_api.utils import paths as paths_mod

        paper_id = _seed_paper(db_session, "p-evidence-img")
        audit_dir = tmp_path / "figures" / paper_id / "_audit"
        audit_dir.mkdir(parents=True)
        png = audit_dir / "fig1_broken_axis.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\nfake-png-bytes")
        monkeypatch.setattr(paths_mod, "_get_uploads_dir", lambda: tmp_path)

        resp = client.get(f"/api/experiment-audit/evidence-image/{paper_id}/fig1_broken_axis.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == png.read_bytes()

        assert client.get(f"/api/experiment-audit/evidence-image/{paper_id}/..%2Fsecret").status_code in (400, 404)
        assert client.get(f"/api/experiment-audit/evidence-image/{paper_id}/missing.png").status_code == 404
        assert client.get("/api/experiment-audit/evidence-image/no-such-paper/x.png").status_code == 404

    def test_finding_types_catalog(self, client):
        from mock_api.experiment_audit.schemas import FINDING_TYPES

        resp = client.get("/api/experiment-audit/finding-types")
        assert resp.status_code == 200
        types = resp.json()
        # 目录应返回注册表里的全部类型（不锁死数量，避免新增类型时测试失修）
        assert len(types) == len(FINDING_TYPES)
        assert {t["type"] for t in types} == set(FINDING_TYPES)
        assert {
            "NUMERIC_MISMATCH",
            "DATA_LEAKAGE_CANDIDATE",
            "FIGURE_REUSE_CANDIDATE",
            "SUSPICIOUS_DATA_PATTERN",
        } <= {t["type"] for t in types}


class TestRelabeledReuseEndpoint:
    """跨论文改标图片复用端点：校验 + 图解析 + 检测接线（检测器 mock）。"""

    def _seed_two_papers(self, db_session, tmp_path, monkeypatch):
        from mock_api import models
        import mock_api.experiment_audit.figure_reuse as figure_reuse

        monkeypatch.setattr(figure_reuse, "_get_uploads_dir", lambda: tmp_path)
        fig_a = tmp_path / "figures" / "p-a"
        fig_b = tmp_path / "figures" / "p-b"
        fig_a.mkdir(parents=True, exist_ok=True)
        fig_b.mkdir(parents=True, exist_ok=True)
        (fig_a / "fa.png").write_bytes(b"a")
        (fig_b / "fb.png").write_bytes(b"b")

        db_session.add(Paper(id="p-a", title="Paper A"))
        db_session.add(Paper(id="p-b", title="Paper B"))
        db_session.add(
            models.PaperFigure(paper_id="p-a", page=1, figure_index=0, figure_path="fa.png")
        )
        db_session.add(
            models.PaperFigure(paper_id="p-b", page=1, figure_index=0, figure_path="fb.png")
        )
        db_session.commit()

    def test_missing_paper_404(self, client, db_session):
        db_session.add(Paper(id="p-a", title="Paper A"))
        db_session.commit()
        resp = client.post(
            "/api/experiment-audit/relabeled-reuse",
            json={"paper_a_id": "p-a", "paper_b_id": "no-such"},
        )
        assert resp.status_code == 404

    def test_same_paper_400(self, client, db_session):
        db_session.add(Paper(id="p-a", title="Paper A"))
        db_session.commit()
        resp = client.post(
            "/api/experiment-audit/relabeled-reuse",
            json={"paper_a_id": "p-a", "paper_b_id": "p-a"},
        )
        assert resp.status_code == 400

    def test_no_figures_400(self, client, db_session):
        db_session.add(Paper(id="p-a", title="Paper A"))
        db_session.add(Paper(id="p-b", title="Paper B"))
        db_session.commit()
        resp = client.post(
            "/api/experiment-audit/relabeled-reuse",
            json={"paper_a_id": "p-a", "paper_b_id": "p-b"},
        )
        assert resp.status_code == 400
        assert "图不足" in resp.json()["detail"]

    def test_comparison_returns_findings(self, client, db_session, tmp_path, monkeypatch):
        import mock_api.experiment_audit.figure_reuse as figure_reuse
        from mock_api.experiment_audit.schemas import make_finding

        self._seed_two_papers(db_session, tmp_path, monkeypatch)
        # mock 检测器（真实 NCC/VLM 太重，端点接线用固定 finding 验证）
        monkeypatch.setattr(
            figure_reuse,
            "detect_relabeled_image_reuse",
            lambda *a, **k: [
                make_finding(
                    "RELABELED_IMAGE_REUSE",
                    title="fa.png 与 fb.png 条带像素复用但标签不一致",
                    claim="NCC=0.965",
                    method="test",
                    needs_human_review=True,
                )
            ],
        )
        resp = client.post(
            "/api/experiment-audit/relabeled-reuse",
            json={"paper_a_id": "p-a", "paper_b_id": "p-b"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["findings_count"] == 1
        assert body["paper_a_id"] == "p-a"
        assert body["paper_b_id"] == "p-b"
        assert body["findings"][0]["type"] == "RELABELED_IMAGE_REUSE"
        assert body["findings"][0]["finding_id"] == "F-001"
