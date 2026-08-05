"""scripts/pipeline_figure_understanding.py 集成测试。

Mock OCR 和 Qwen，验证 figure → OCR → Qwen → DB 端到端链路。
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

# 图表理解管线依赖 matplotlib（scripts/figure_utils.py 惰性导入）。
# 未安装时优雅跳过，避免整模块在导入期 ImportError 导致 CI 红色 ERROR。
pytest.importorskip("matplotlib")

from mock_api.models import Paper, PaperFigure


@pytest.fixture
def pdf_bytes():
    """生成一个带实验图的简单 PDF。"""
    from scripts.figure_utils import make_experimental_chart_bytes, make_pdf_with_figure

    img_bytes = make_experimental_chart_bytes()
    return make_pdf_with_figure(img_bytes)


class TestFigureUnderstandingPipeline:
    """run_pipeline 端到端链路测试。"""

    def test_pipeline_stores_ocr_and_qwen_summary(self, db_session, pdf_bytes):
        """Mock OCR 和 Qwen，验证 DB 正确写入 PaperFigure。"""
        from scripts.pipeline_figure_understanding import run_pipeline

        paper_id = "test_pipeline_001"

        def fake_extract(pdf_bytes: bytes, paper_id: str) -> list[dict]:
            return [
                {
                    "page": 1,
                    "figure_index": 0,
                    "figure_path": "test_figure.png",
                    "ocr_text": "Fake OCR text from chart",
                }
            ]

        def fake_ask_qwen(ocr_text: str) -> str:
            return "Fake Qwen summary: the chart shows accuracy increasing."

        result = run_pipeline(
            paper_id=paper_id,
            title="Test Paper",
            pdf_bytes=pdf_bytes,
            db=db_session,
            extract_fn=fake_extract,
            ask_fn=fake_ask_qwen,
        )

        assert result == 0

        # 验证 paper 记录
        paper = db_session.query(Paper).filter(Paper.id == paper_id).first()
        assert paper is not None
        assert paper.title == "Test Paper"

        # 验证 figure 记录
        figure = db_session.query(PaperFigure).filter(PaperFigure.paper_id == paper_id).first()
        assert figure is not None
        assert figure.ocr_text == "Fake OCR text from chart"
        assert figure.qwen_summary == "Fake Qwen summary: the chart shows accuracy increasing."
        assert figure.page == 1
        assert figure.figure_index == 0

    def test_pipeline_creates_paper_when_missing(self, db_session, pdf_bytes):
        """当 paper 记录不存在时，pipeline 应自动创建。"""
        from scripts.pipeline_figure_understanding import run_pipeline

        paper_id = "test_pipeline_002"

        result = run_pipeline(
            paper_id=paper_id,
            title="Auto Created Paper",
            pdf_bytes=pdf_bytes,
            db=db_session,
            extract_fn=lambda _pdf, _pid: [{
                "page": 2,
                "figure_index": 1,
                "figure_path": "auto.png",
                "ocr_text": "auto ocr",
            }],
            ask_fn=lambda _text: "auto summary",
        )

        assert result == 0
        paper = db_session.query(Paper).filter(Paper.id == paper_id).first()
        assert paper is not None
        assert paper.title == "Auto Created Paper"

    def test_pipeline_returns_error_when_no_figures(self, db_session, pdf_bytes):
        """OCR 返回空列表时，pipeline 应返回 1。"""
        from scripts.pipeline_figure_understanding import run_pipeline

        paper_id = "test_pipeline_003"

        result = run_pipeline(
            paper_id=paper_id,
            title="Empty Figure Paper",
            pdf_bytes=pdf_bytes,
            db=db_session,
            extract_fn=lambda _pdf, _pid: [],
            ask_fn=lambda _text: "should not be called",
        )

        assert result == 1
        figure = db_session.query(PaperFigure).filter(PaperFigure.paper_id == paper_id).first()
        assert figure is None


class TestAskQwenVRAMLock:
    """ask_qwen 应在使用 Qwen 前请求 VRAM 调度器锁定。"""

    def test_ask_qwen_requests_vram_lock(self, monkeypatch):
        """调用 ask_qwen 时，必须向 VRAM 调度器请求 Qwen 锁。"""
        from unittest.mock import Mock

        from scripts.figure_utils import ask_qwen

        mock_scheduler = Mock()
        monkeypatch.setattr(
            "mock_api.vram_scheduler.get_vram_scheduler",
            lambda: mock_scheduler,
        )

        # 避免真实 HTTP 调用（ask_qwen 已迁移到 mock_api.llm.figure_qwen）
        monkeypatch.setattr(
            "mock_api.llm.figure_qwen.requests.post",
            lambda *args, **kwargs: Mock(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": "summary"}}]}),
        )

        ask_qwen("some ocr text")

        mock_scheduler.request_qwen.assert_called_once_with(wait=True)

    def test_ask_qwen_fail_open_when_scheduler_unavailable(self, monkeypatch):
        """VRAM 调度器不可用时，ask_qwen 应 fail-open 继续执行。"""
        from unittest.mock import Mock

        from scripts.figure_utils import ask_qwen

        monkeypatch.setattr(
            "mock_api.vram_scheduler.get_vram_scheduler",
            lambda: Mock(side_effect=ImportError("no scheduler")),
        )

        monkeypatch.setattr(
            "mock_api.llm.figure_qwen.requests.post",
            lambda *args, **kwargs: Mock(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": "summary"}}]}),
        )

        result = ask_qwen("some ocr text")

        assert result == "summary"


class TestFigureUnderstandingCLI:
    """main() CLI entry tests."""

    @pytest.fixture(autouse=True)
    def _mock_external_services(self, monkeypatch):
        """Mock VRAM scheduler and Qwen, avoiding real services."""
        monkeypatch.setattr(
            "mock_api.vram_scheduler.get_vram_scheduler",
            lambda: Mock(request_qwen=lambda **kwargs: None),
        )
        monkeypatch.setattr(
            "mock_api.pdf_parser.extract_figures_for_paper",
            lambda _pdf, _pid: [
                {
                    "page": 1,
                    "figure_index": 0,
                    "figure_path": "cli_figure.png",
                    "ocr_text": "CLI OCR text",
                }
            ],
        )
        monkeypatch.setattr(
            "scripts.pipeline_figure_understanding.ask_qwen",
            lambda _text, **kwargs: "CLI Qwen summary",
        )

    def test_cli_synthetic_mode(self):
        """不传 --pdf 时进入合成模式并正常跑通。"""
        from scripts.pipeline_figure_understanding import main

        result = main([])
        assert result == 0

    def test_cli_with_pdf_path(self, tmp_path):
        """使用 --pdf 和 --paper-id 处理真实 PDF。"""
        from scripts.figure_utils import make_experimental_chart_bytes, make_pdf_with_figure
        from scripts.pipeline_figure_understanding import main

        img_bytes = make_experimental_chart_bytes()
        pdf_bytes = make_pdf_with_figure(img_bytes)
        pdf_path = tmp_path / "real_paper.pdf"
        pdf_path.write_bytes(pdf_bytes)

        result = main(["--pdf", str(pdf_path), "--paper-id", "cli_real_001"])
        assert result == 0

        from mock_api.database import SessionLocal
        with SessionLocal() as db:
            paper = db.query(Paper).filter(Paper.id == "cli_real_001").first()
            assert paper is not None
            assert paper.title == "real_paper"

            figure = db.query(PaperFigure).filter(PaperFigure.paper_id == "cli_real_001").first()
            assert figure is not None
            assert figure.qwen_summary == "CLI Qwen summary"

    def test_cli_pdf_not_found(self):
        """--pdf 指向不存在的文件时应返回 1。"""
        from scripts.pipeline_figure_understanding import main

        result = main(["--pdf", "nonexistent.pdf"])
        assert result == 1
