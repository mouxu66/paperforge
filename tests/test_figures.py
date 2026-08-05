"""测试 figure 检索相关接口（/api/figures/search, /api/search/hybrid）。

覆盖场景：
- figure 语义检索空查询返回空列表
- figure 图片服务 404/400 边界
- hybrid 融合检索空查询返回空结构
- hybrid 融合检索在向量不可用时降级
- M0: 矢量图渲染兜底能识别矢量绘制的 figure
- Rec4: 图注关联能把 caption 绑定到 figure
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from mock_api.main import app
from mock_api.pdf_parser import _associate_captions, extract_figures_for_paper


@pytest.fixture
def client():
    """返回已挂载路由的 TestClient。"""
    return TestClient(app)


def test_search_figures_empty_query_returns_empty(client: TestClient):
    """空查询 → /api/figures/search 返回空列表。"""
    resp = client.post("/api/figures/search", json={"q": "", "top_k": 10})
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_figures_vector_unavailable_returns_empty(client: TestClient):
    """fastembed 不可用时 figure 检索降级为空列表。"""
    with patch("mock_api.semantic_search.embed_text", return_value=None):
        resp = client.post("/api/figures/search", json={"q": "accuracy curve", "top_k": 10})
    assert resp.status_code == 200
    assert resp.json() == []


def test_hybrid_search_empty_query_returns_empty(client: TestClient):
    """空查询 → /api/search/hybrid 返回空结构。"""
    resp = client.post("/api/search/hybrid", json={"q": "", "top_k": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data["papers"] == []
    assert data["figures"] == []
    assert data["fused_figures"] == []


def test_hybrid_search_vector_unavailable_returns_empty_figures(client: TestClient):
    """fastembed 不可用时 hybrid 返回空 figure 列表但 papers 可正常返回。"""
    with patch("mock_api.semantic_search.embed_text", return_value=None):
        resp = client.post("/api/search/hybrid", json={"q": "accuracy curve", "top_k": 10})
    assert resp.status_code == 200
    data = resp.json()
    assert data["figures"] == []
    assert data["fused_figures"] == []


def test_extract_figures_vector_rendering_finds_vector_figure():
    """M0: 用 fitz 绘制一个纯矢量图，验证 extract_figures_for_paper 能抽出 source='vector' 的 figure。"""
    import io

    import fitz

    from mock_api.pdf_parser import extract_figures_for_paper

    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    # 画一个占页面较大面积的矩形（模拟 matplotlib 矢量图）
    rect = fitz.Rect(50, 50, 450, 400)
    page.draw_rect(rect, color=(0, 0, 0.8), fill=(0.9, 0.9, 1.0), width=2)
    # 加一段图注文本
    page.insert_text((50, 430), "Figure 1: Synthetic vector figure for testing.", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()

    figs = extract_figures_for_paper(pdf_bytes, paper_id="test_vector_render", skip_ocr=True)
    # 只要有一路抽出来即可；位图路径应该为空，矢量路径应该命中
    assert any(f.get("source") == "vector" for f in figs), "应识别出矢量 figure"


def test_extract_figures_associates_caption_with_vector_figure():
    """Rec4: 纯矢量图中应能把 Figure caption 关联到 figure。"""
    import io

    import fitz

    from mock_api.pdf_parser import extract_figures_for_paper

    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    rect = fitz.Rect(50, 50, 450, 400)
    page.draw_rect(rect, color=(0, 0, 0.8), fill=(0.9, 0.9, 1.0), width=2)
    page.insert_text((50, 430), "Figure 1: Synthetic vector figure for testing.", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()

    figs = extract_figures_for_paper(pdf_bytes, paper_id="test_vector_caption", skip_ocr=True)
    vector_figs = [f for f in figs if f.get("source") == "vector"]
    assert vector_figs
    for f in vector_figs:
        caption = (f.get("caption_text") or "").strip()
        # 至少有一个矢量图关联上图注
        if "Synthetic vector figure" in caption:
            break
    else:
        assert False, "图注未正确关联到矢量 figure"


def test_qf_health_endpoint_returns_coverage_shape(client: TestClient):
    """Rec2: /api/stats/qf-health 返回预期字段与数值范围。"""
    resp = client.get("/api/stats/qf-health")
    assert resp.status_code == 200
    data = resp.json()
    assert "coverage" in data
    assert "qfMean" in data
    assert "qfStd" in data
    assert "histogram" in data
    assert "m0Active" in data
    assert "realFigureCount" in data
    assert "captionedFigureCount" in data
    assert "captionCoverage" in data
    assert 0 <= data["coverage"] <= 1
    assert 0 <= data["qfMean"] <= 1
    assert 0 <= data["captionCoverage"] <= 1
    assert data["captionedFigureCount"] <= data["realFigureCount"]


def test_qf_health_kpi_counts_real_figures(client: TestClient, db_session):
    """Rec2: captionCoverage 按 (paper_id, figure_number) 去重，不受 detector 过度分割影响。"""
    from mock_api.crud.figures import upsert_figure

    # 模拟一篇论文的真实图被 detector 切成 3 个簇：
    # - 两个簇共享同一个 figure_number（同一真实图的分割碎片）
    # - 一个簇无图号（过度分割碎片）
    # - 一个簇带图注
    upsert_figure(
        db_session,
        paper_id="kpi_paper_1",
        page=1,
        figure_index=0,
        figure_path="/tmp/f1.png",
        ocr_text="ocr1",
        figure_number=1,
        caption_text="Figure 1: accuracy.",
    )
    upsert_figure(
        db_session,
        paper_id="kpi_paper_1",
        page=1,
        figure_index=1,
        figure_path="/tmp/f2.png",
        ocr_text="ocr2",
        figure_number=1,
        caption_text="",
    )
    upsert_figure(
        db_session,
        paper_id="kpi_paper_1",
        page=2,
        figure_index=2,
        figure_path="/tmp/f3.png",
        ocr_text="ocr3",
        figure_number=None,
        caption_text="",
    )
    db_session.commit()

    resp = client.get("/api/stats/qf-health")
    assert resp.status_code == 200
    data = resp.json()
    # 真实图只有 1 个（去重后），且已带图注
    assert data["realFigureCount"] == 1
    assert data["captionedFigureCount"] == 1
    assert data["captionCoverage"] == 1.0


def test_associate_captions_matches_by_number_and_proximity():
    """Rec4: 同一页多图时按标号+邻近一对一关联 caption，避免错配。"""

    class MockRect:
        def __init__(self):
            self.height = 1000.0

    class MockPage:
        rect = MockRect()

        def get_text(self, _mode):
            return [
                (0, 110, 100, 120, "Fig. 1: Accuracy curve for ResNet.", 0, 0),
                (0, 210, 100, 220, "Fig. 2: Accuracy curve for Transformer.", 1, 0),
            ]

    figures = [
        {"bbox": (0, 100, 100, 110)},
        {"bbox": (0, 200, 100, 210)},
        {"bbox": (0, 800, 100, 810)},
    ]

    _associate_captions(MockPage(), figures)

    assert figures[0].get("caption_text") == "Fig. 1: Accuracy curve for ResNet."
    assert figures[0].get("figure_number") == 1
    assert figures[1].get("caption_text") == "Fig. 2: Accuracy curve for Transformer."
    assert figures[1].get("figure_number") == 2
    # 第三个图没有可用 caption，不应复用已被占用的 caption
    assert figures[2].get("caption_text", "") == ""


def test_associate_captions_exact_number_overrides_distance():
    """Rec4: figure 已有 figure_number 时，应优先按标号匹配而非垂直距离。"""

    class MockRect:
        def __init__(self):
            self.height = 1000.0

    class MockPage:
        rect = MockRect()

        def get_text(self, _mode):
            return [(0, 10, 100, 20, "Fig. 9: Far caption.", 0, 0)]

    figures = [{"bbox": (0, 900, 100, 910), "figure_number": 9}]

    _associate_captions(MockPage(), figures)

    assert figures[0].get("caption_text") == "Fig. 9: Far caption."
    assert figures[0].get("figure_number") == 9


def test_m1_hard_match_confidence_and_label():
    """M1: 精确图号 + 空间邻近应得到 hard 标签与高置信度。"""

    class MockRect:
        def __init__(self):
            self.height = 1000.0

    class MockPage:
        rect = MockRect()

        def get_text(self, _mode):
            return [(50, 110, 150, 120, "Figure 1: Accuracy curve.", 0, 0)]

    figures = [{"bbox": (50, 50, 150, 100), "figure_number": 1}]

    _associate_captions(MockPage(), figures)

    assert figures[0].get("caption_text") == "Figure 1: Accuracy curve."
    assert figures[0].get("match_label") == "hard"
    assert figures[0].get("match_confidence", 0) >= 0.9


def test_m1_unmatched_when_no_caption():
    """M1: 无可用 caption 时应为 unmatched。"""

    class MockRect:
        def __init__(self):
            self.height = 1000.0

    class MockPage:
        rect = MockRect()

        def get_text(self, _mode):
            return []

    figures = [{"bbox": (50, 50, 150, 100), "figure_number": 1}]

    _associate_captions(MockPage(), figures)

    assert figures[0].get("caption_text", "") == ""
    assert figures[0].get("match_label") == "unmatched"
    assert figures[0].get("match_confidence", 1.0) == 0.0


def test_m2_vlm_routing_decisions():
    """M2: 灰带 VLM 路由根据匹配标签与 OCR 长度做出正确决策。"""
    from mock_api.pdf_parser import route_vlm_for_figure

    hard_short = {
        "match_label": "hard",
        "match_confidence": 0.95,
        "caption_text": "Figure 1: Accuracy.",
        "ocr_text": "89.7 87.5",
    }
    gray = {
        "match_label": "gray",
        "match_confidence": 0.5,
        "caption_text": "Figure 2: Results.",
        "ocr_text": "some ocr",
    }
    long_ocr = {
        "match_label": "hard",
        "match_confidence": 0.95,
        "caption_text": "Figure 3: Long.",
        "ocr_text": "x" * 500,
    }

    assert route_vlm_for_figure(hard_short)["decision"] == "rule_only"
    assert route_vlm_for_figure(gray)["decision"] == "vlm"
    assert route_vlm_for_figure(long_ocr)["decision"] == "vlm"


def test_extract_figures_persists_vlm_decision():
    """M2: extract_figures_for_paper 应计算并返回 vlm_decision。"""
    import fitz
    from mock_api.pdf_parser import extract_figures_for_paper

    doc = fitz.open()
    page = doc.new_page(width=500, height=700)
    rect = fitz.Rect(50, 50, 450, 400)
    page.draw_rect(rect, color=(0, 0, 0.8), fill=(0.9, 0.9, 1.0), width=2)
    page.insert_text((50, 430), "Figure 1: Synthetic vector figure for testing.", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()

    figs = extract_figures_for_paper(pdf_bytes, paper_id="test_vlm_decision", skip_ocr=True)
    # 至少有一个 figure 被抽出，且包含 vlm_decision
    assert figs
    for fig in figs:
        assert "vlm_decision" in fig
        assert fig["vlm_decision"] in ("vlm", "rule_only", "skip")


def test_persist_figures_generates_qwen_summary(db_session):
    """Worker 应为有 OCR 的 figure 调用 Qwen 生成语义摘要并持久化。"""
    from unittest.mock import patch

    from mock_api.models import PaperFigure
    from mock_api.workers.figures import _persist_figures

    figs = [
        {
            "page": 1,
            "figure_index": 0,
            "figure_path": "/tmp/f1.png",
            "ocr_text": "accuracy epoch baseline proposed",
            "caption_text": "Figure 1: Accuracy.",
            "vlm_decision": "vlm",
        },
        {
            "page": 2,
            "figure_index": 1,
            "figure_path": "/tmp/f2.png",
            "ocr_text": "",
            "caption_text": "Figure 2: Loss.",
            "vlm_decision": "skip",
        },
    ]

    with patch("mock_api.pdf_parser.extract_figures_for_paper", return_value=figs), \
         patch("mock_api.semantic_search.embed_text", return_value=[0.1] * 384), \
         patch("mock_api.workers.figures.ask_qwen", return_value="Qwen generated summary") as mock_ask:
        count = _persist_figures(db_session, b"fake pdf", "paper-1")

    assert count == 2
    # 只有包含 OCR 的 figure 才调用 Qwen
    assert mock_ask.call_count == 1
    rows = db_session.query(PaperFigure).order_by(PaperFigure.figure_index).all()
    assert len(rows) == 2
    assert rows[0].qwen_summary == "Qwen generated summary"
    # 无 OCR 的 figure 不调用 Qwen，摘要为空
    assert rows[1].qwen_summary is None or rows[1].qwen_summary == ""


def test_persist_figures_qwen_failure_is_fail_open(db_session):
    """Qwen 失败时仍应持久化 figure，并用 ocr_text 兜底 qwen_summary。"""
    from unittest.mock import patch

    from mock_api.models import PaperFigure
    from mock_api.workers.figures import _persist_figures

    figs = [
        {
            "page": 1,
            "figure_index": 0,
            "figure_path": "/tmp/f1.png",
            "ocr_text": "some ocr text",
            "caption_text": "Figure 1: Accuracy.",
            "vlm_decision": "vlm",
        }
    ]

    with patch("mock_api.pdf_parser.extract_figures_for_paper", return_value=figs), \
         patch("mock_api.semantic_search.embed_text", return_value=[0.1] * 384), \
         patch("mock_api.workers.figures.ask_qwen", side_effect=RuntimeError("Qwen down")) as mock_ask:
        count = _persist_figures(db_session, b"fake pdf", "paper-2")

    assert count == 1
    assert mock_ask.call_count == 1
    rows = db_session.query(PaperFigure).all()
    assert len(rows) == 1
    # Qwen 失败时，qwen_summary 回退到 ocr_text，保证 QF 节点仍有信号
    assert rows[0].qwen_summary == "some ocr text"


def test_persist_figures_m2_vlm_routing(db_session):
    """M2: 仅 vlm 决策调用 Qwen；rule_only 生成规则摘要；skip 不生成摘要。"""
    from unittest.mock import patch

    from mock_api.models import PaperFigure
    from mock_api.workers.figures import _persist_figures

    figs = [
        {
            "page": 1,
            "figure_index": 0,
            "figure_path": "/tmp/f1.png",
            "ocr_text": "ocr vlm",
            "caption_text": "Figure 1: Accuracy.",
            "vlm_decision": "vlm",
        },
        {
            "page": 2,
            "figure_index": 1,
            "figure_path": "/tmp/f2.png",
            "ocr_text": "ocr rule",
            "caption_text": "Figure 2: Loss.",
            "vlm_decision": "rule_only",
        },
        {
            "page": 3,
            "figure_index": 2,
            "figure_path": "/tmp/f3.png",
            "ocr_text": "ocr skip",
            "caption_text": "",
            "vlm_decision": "skip",
        },
    ]

    with patch("mock_api.pdf_parser.extract_figures_for_paper", return_value=figs), \
         patch("mock_api.semantic_search.embed_text", return_value=[0.1] * 384), \
         patch("mock_api.workers.figures.ask_qwen", return_value="VLM summary") as mock_ask:
        count = _persist_figures(db_session, b"fake pdf", "paper-3")

    assert count == 3
    # 只有 vlm 决策的 figure 调用 Qwen
    assert mock_ask.call_count == 1

    rows = db_session.query(PaperFigure).order_by(PaperFigure.figure_index).all()
    assert len(rows) == 3
    assert rows[0].qwen_summary == "VLM summary"
    # rule_only 生成基于 caption 的规则摘要，未调用 Qwen
    assert rows[1].qwen_summary == "Caption: Figure 2: Loss."
    # skip 不生成摘要，但仍会用 ocr_text 兜底
    assert rows[2].qwen_summary == "ocr skip"


def test_upsert_figure_normalizes_empty_caption_text_to_none(db_session):
    """DB 整洁度：空 / 纯空白 caption_text 应归一化为 None。"""
    from mock_api.crud.figures import upsert_figure
    from mock_api.models import PaperFigure

    upsert_figure(
        db_session,
        paper_id="cap_paper_1",
        page=1,
        figure_index=0,
        figure_path="/tmp/f1.png",
        ocr_text="ocr",
        caption_text="   ",
    )
    upsert_figure(
        db_session,
        paper_id="cap_paper_1",
        page=1,
        figure_index=1,
        figure_path="/tmp/f2.png",
        ocr_text="ocr",
        caption_text="",
    )
    upsert_figure(
        db_session,
        paper_id="cap_paper_1",
        page=1,
        figure_index=2,
        figure_path="/tmp/f3.png",
        ocr_text="ocr",
        caption_text="Figure 3: valid caption.",
    )
    db_session.commit()

    rows = db_session.query(PaperFigure).order_by(PaperFigure.figure_index).all()
    assert len(rows) == 3
    assert rows[0].caption_text is None
    assert rows[1].caption_text is None
    assert rows[2].caption_text == "Figure 3: valid caption."


def test_qf_timeline_endpoint_returns_before_after(client: TestClient, db_session):
    """Rec2: /api/stats/qf-timeline 返回修复前后 KPI 对比。"""
    from mock_api.crud.figures import upsert_figure

    # 模拟 detector 过度分割：3 个簇共享同一个真实图号 + 1 个无图号碎片
    upsert_figure(
        db_session,
        paper_id="timeline_paper_1",
        page=1,
        figure_index=0,
        figure_path="/tmp/f1.png",
        ocr_text="ocr1",
        figure_number=1,
        caption_text="Figure 1: accuracy.",
    )
    upsert_figure(
        db_session,
        paper_id="timeline_paper_1",
        page=1,
        figure_index=1,
        figure_path="/tmp/f2.png",
        ocr_text="ocr2",
        figure_number=1,
        caption_text="",
    )
    upsert_figure(
        db_session,
        paper_id="timeline_paper_1",
        page=2,
        figure_index=2,
        figure_path="/tmp/f3.png",
        ocr_text="ocr3",
        figure_number=None,
        caption_text="",
    )
    db_session.commit()

    resp = client.get("/api/stats/qf-timeline")
    assert resp.status_code == 200
    data = resp.json()
    assert "oldKpiTotalClusters" in data
    assert "newKpiRealFigures" in data
    assert "coverageBefore" in data
    assert "coverageAfter" in data
    assert "paperBreakdown" in data
    # 真实图去重后只有 1 个真实图
    assert data["newKpiRealFigures"] == 1
    assert data["newKpiCaptionedReal"] == 1


def test_persist_figures_skips_ask_qwen_when_merged_summary_present(db_session):
    """如果 merged vision call 已经产出 summary 且 vision_attempted=True，
    _generate_qwen_summaries 不应再调用 ask_qwen，避免同一张图被看两次。"""
    from unittest.mock import patch

    from mock_api.models import PaperFigure
    from mock_api.workers.figures import _persist_figures

    figs = [
        {
            "page": 1,
            "figure_index": 0,
            "figure_path": "/tmp/f1.png",
            "ocr_text": "ocr vlm",
            "caption_text": "Figure 1: Accuracy.",
            "vlm_decision": "vlm",
            "vision_attempted": True,
            "summary": "Merged vision summary from _analyze_image_bytes.",
        }
    ]

    with patch("mock_api.pdf_parser.extract_figures_for_paper", return_value=figs), \
         patch("mock_api.semantic_search.embed_text", return_value=[0.1] * 384), \
         patch("mock_api.workers.figures.ask_qwen") as mock_ask:
        count = _persist_figures(db_session, b"fake pdf", "paper-merged-summary")

    assert count == 1
    assert mock_ask.call_count == 0

    rows = db_session.query(PaperFigure).all()
    assert len(rows) == 1
    assert rows[0].qwen_summary == "Merged vision summary from _analyze_image_bytes."


def test_persist_figures_stores_source_text_span_and_claim_validation(db_session):
    """P0-1/P1: _persist_figures 应从 full_text 抽取 source_text_span，
    并校验 caption/span 中的数值断言与 axis_info 范围是否一致。"""
    from unittest.mock import patch

    from mock_api.models import Paper, PaperFigure
    from mock_api.workers.figures import _persist_figures

    # Seed a paper with full_text referencing Figure 1
    db_session.add(
        Paper(
            id="paper-source-span",
            title="Source span test",
            authors=[],
            abstract="",
            year=2024,
            full_text="As shown in Figure 1, our method achieves 88% accuracy. "
                       "This outperforms the baseline by a large margin.",
        )
    )
    db_session.commit()

    figs = [
        {
            "page": 1,
            "figure_index": 0,
            "figure_path": "/tmp/f1.png",
            "ocr_text": "ocr",
            "caption_text": "Figure 1: Accuracy comparison.",
            "vlm_decision": "rule_only",
            "figure_number": 1,
            "axis_info": {
                "x_label": "epoch",
                "y_label": "Accuracy",
                "y_ticks": [80, 85, 90],
            },
        }
    ]

    with patch("mock_api.pdf_parser.extract_figures_for_paper", return_value=figs), \
         patch("mock_api.semantic_search.embed_text", return_value=[0.1] * 384), \
         patch("mock_api.workers.figures.ask_qwen"):
        count = _persist_figures(db_session, b"fake pdf", "paper-source-span")

    assert count == 1
    rows = db_session.query(PaperFigure).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.source_text_span is not None
    assert "Figure 1" in row.source_text_span
    assert "88%" in row.source_text_span
    assert row.axis_info is not None
    assert row.claim_validation is not None
    assert any(v["metric_matched"] for v in row.claim_validation["validated"])


def test_persist_figures_persists_curve_correction(db_session):
    """P3/P4: 当 curve_points 复核通过时，应在 claim_validation 中持久化 curve_corrected 标记。"""
    from unittest.mock import patch

    from mock_api.models import Paper, PaperFigure
    from mock_api.workers.figures import _persist_figures

    db_session.add(
        Paper(
            id="paper-curve-correct",
            title="Curve correction test",
            authors=[],
            abstract="",
            year=2024,
            full_text="As shown in Figure 1, our method achieves 0.95 accuracy.",
        )
    )
    db_session.commit()

    figs = [
        {
            "page": 1,
            "figure_index": 0,
            "figure_path": "/tmp/f1.png",
            "ocr_text": "ocr",
            "caption_text": "Figure 1: Accuracy comparison.",
            "vlm_decision": "rule_only",
            "figure_number": 1,
            "axis_info": {
                "x_label": "epoch",
                "y_label": "Accuracy",
                "y_ticks": [0.8, 0.9],
            },
        }
    ]

    # 模拟曲线点：y 范围 0.9-0.98，包含断言 0.95
    with patch("mock_api.pdf_parser.extract_figures_for_paper", return_value=figs), \
         patch("mock_api.semantic_search.embed_text", return_value=[0.1] * 384), \
         patch("mock_api.figure_curves.extract_curve_points", return_value=[
             {"x": 1, "y": 0.9}, {"x": 2, "y": 0.95}, {"x": 3, "y": 0.98}
         ]), \
         patch("mock_api.workers.figures.ask_qwen"):
        count = _persist_figures(db_session, b"fake pdf", "paper-curve-correct")

    assert count == 1
    rows = db_session.query(PaperFigure).all()
    assert len(rows) == 1
    validated = rows[0].claim_validation["validated"]
    # 0.95 落在曲线 y 范围 [0.9, 0.98] 内，应被修正为 valid
    assert any(v.get("curve_corrected") for v in validated)
    assert all(v["valid"] is True for v in validated if v.get("curve_corrected"))
