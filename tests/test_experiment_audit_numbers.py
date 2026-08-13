"""实验审计 P0-11 图内数值造假指纹单测（table_numbers.py）。

覆盖：
- parse_number_series（label|value 与 markdown 两种格式）
- detect_cross_group_duplicates（跨组重复值）
- detect_digit_preference（末位偏好）
- transcribe_multi / merge_transcripts（多遍转写取多数，跨组重复加固）
- check_figure_number_patterns DB 集成（mock VLM，不触网）
"""

from __future__ import annotations

from unittest.mock import patch

from mock_api.experiment_audit import table_numbers
from mock_api.models import Paper, PaperFigure


class TestParseNumberSeries:
    def test_label_value_format(self):
        txt = "WT|0.763641\nH186R|0.811456\nWT|0.814231"
        series = table_numbers.parse_number_series(txt)
        assert series == [
            ("WT", [0.763641, 0.814231]),
            ("H186R", [0.811456]),
        ]

    def test_markdown_table_format(self):
        txt = "|  | 0 | 0.5 |\n| WT | 1.0 | 2.0 |\n| H186R | 3.0 | 4.0 |"
        series = table_numbers.parse_number_series(txt)
        assert series == [
            ("WT", [1.0, 2.0]),
            ("H186R", [3.0, 4.0]),
        ]

    def test_skips_na_and_header(self):
        txt = "| 组别 | 值 |\n| WT | NA |\n| H186R | ? |"
        assert table_numbers.parse_number_series(txt) == []


class TestCrossGroupDuplicates:
    def test_duplicates_across_groups_flagged(self):
        series = [
            ("WT", [0.763641, 5.589312, 7.992131]),
            ("H186R", [0.811456, 5.589312, 7.992131]),
        ]
        flags = table_numbers.detect_cross_group_duplicates(series)
        assert len(flags) == 2
        assert any("5.589312" in f for f in flags)
        assert any("7.992131" in f for f in flags)
        assert all("跨组重复" in f for f in flags)

    def test_no_duplicates_no_flag(self):
        series = [("WT", [1.1, 2.2]), ("H186R", [3.3, 4.4])]
        assert table_numbers.detect_cross_group_duplicates(series) == []


class TestDigitPreference:
    def test_skewed_last_digit_flagged(self):
        # 20 个数：10 个末位为 1，其余稀疏 → 卡方显著
        vals = [0.01, 0.11, 0.21, 0.31, 0.41, 0.51, 0.61, 0.71, 0.81, 0.91]
        vals += [0.02, 0.13, 0.24, 0.35, 0.46, 0.57, 0.68, 0.79, 0.8, 0.9]
        flag = table_numbers.detect_digit_preference(vals)
        assert flag is not None
        assert "末位偏好" in flag

    def test_uniform_last_digit_not_flagged(self):
        vals = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9] * 2
        assert table_numbers.detect_digit_preference(vals) is None

    def test_too_few_values_not_flagged(self):
        assert table_numbers.detect_digit_preference([1.1, 1.1, 1.1]) is None


class TestCheckFigureNumberPatterns:
    def _seed(self, db, tmp_path, monkeypatch):
        monkeypatch.setattr(table_numbers, "_get_uploads_dir", lambda: tmp_path)
        fig_dir = tmp_path / "figures" / "p-num"
        fig_dir.mkdir(parents=True, exist_ok=True)
        (fig_dir / "f0.png").write_bytes(b"x")
        db.add(Paper(id="p-num", title="Number Audit"))
        db.add(
            PaperFigure(
                paper_id="p-num",
                page=3,
                figure_index=0,
                figure_path="figures/p-num/f0.png",
                figure_number=1,
                caption_text="PGAM1 enzyme activity",
            )
        )
        db.commit()

    def test_fabricated_numbers_produce_finding(self, db_session, tmp_path, monkeypatch):
        self._seed(db_session, tmp_path, monkeypatch)
        monkeypatch.setattr(
            table_numbers,
            "transcribe_figure_numbers",
            lambda *a, **k: (
                "WT|0.763641\nH186R|0.811456\n"
                "WT|5.589312\nH186R|5.589312\n"
                "WT|7.992131\nH186R|7.992131"
            ),
        )
        findings = table_numbers.check_figure_number_patterns(db_session, "p-num")
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "SUSPICIOUS_DATA_PATTERN"
        assert f["severity"] == "high"
        assert f["page"] == 3
        assert f["needs_human_review"] is True
        assert "跨组重复" in f["computed"]
        assert any(e.get("figure_id") == "Figure 1" for e in f["evidence_sources"])

    def test_clean_numbers_no_finding(self, db_session, tmp_path, monkeypatch):
        self._seed(db_session, tmp_path, monkeypatch)
        monkeypatch.setattr(
            table_numbers,
            "transcribe_figure_numbers",
            lambda *a, **k: "WT|1.234\nH186R|2.345\nWT|3.456\nH186R|4.567",
        )
        assert table_numbers.check_figure_number_patterns(db_session, "p-num") == []

    def test_no_figures_returns_empty(self, db_session):
        assert table_numbers.check_figure_number_patterns(db_session, "no-figs") == []


class TestTranscribeMulti:
    def test_multi_run_returns_all_transcripts(self):
        """transcribe_multi 应调用 transcribe_figure_numbers n_runs 次并返回结果列表。"""
        transcripts = ["WT|0.1", "WT|0.2", "WT|0.3"]
        call_count = 0

        def mock_transcribe(*args, **kwargs):
            nonlocal call_count
            t = transcripts[call_count]
            call_count += 1
            return t

        with patch.object(table_numbers, "transcribe_figure_numbers", side_effect=mock_transcribe):
            result = table_numbers.transcribe_multi("fake.png", n_runs=3)
        assert result == ["WT|0.1", "WT|0.2", "WT|0.3"]
        assert call_count == 3

    def test_multi_run_skips_empty_transcripts(self):
        """transcribe_multi 跳过空转写结果。"""
        call_count = 0

        def mock_transcribe(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return "" if call_count % 2 == 0 else "WT|0.1"

        with patch.object(table_numbers, "transcribe_figure_numbers", side_effect=mock_transcribe):
            result = table_numbers.transcribe_multi("fake.png", n_runs=3)
        assert result == ["WT|0.1", "WT|0.1"]

    def test_multi_run_returns_empty_if_all_fail(self):
        """transcribe_multi 全部失败时返回空列表。"""
        with patch.object(table_numbers, "transcribe_figure_numbers", return_value=""):
            result = table_numbers.transcribe_multi("fake.png", n_runs=3)
        assert result == []


class TestMergeTranscripts:
    def test_single_transcript_passthrough(self):
        """单遍转写直接返回 parse 结果。"""
        transcript = "WT|0.763641\nH186R|0.811456"
        merged = table_numbers.merge_transcripts([transcript])
        assert merged == [("WT", [0.763641]), ("H186R", [0.811456])]

    def test_multi_transcript_majority_vote(self):
        """多遍转写取众数：2/3 遍相同则取该值。"""
        transcripts = [
            "WT|0.123\nH186R|0.456",
            "WT|0.123\nH186R|0.789",  # H186R 不同
            "WT|0.123\nH186R|0.456",  # H186R 与第1遍相同（多数）
        ]
        merged = table_numbers.merge_transcripts(transcripts)
        assert merged == [("WT", [0.123]), ("H186R", [0.456])]

    def test_multi_transcript_resolves_label_noise(self):
        """标签噪声场景：2/3 遍把数值归到正确组，消除跨组误判。

        原始问题：VLM 把 5.589312 同时归到 WT 和 H186R，触发误报。
        多遍转写后取众数：2/3 遍正确归组 → 合并后不重复。
        """
        transcripts = [
            # 第1遍：标签正确（WT 有 5.589312，H186R 没有）
            "WT|0.763641\nWT|5.589312\nH186R|0.811456",
            # 第2遍：标签错误（两个组都有 5.589312）
            "WT|0.763641\nH186R|0.811456\nWT|5.589312\nH186R|5.589312",
            # 第3遍：标签正确（同第1遍）
            "WT|0.763641\nWT|5.589312\nH186R|0.811456",
        ]
        merged = table_numbers.merge_transcripts(transcripts)
        # 众数投票后：WT 有 0.763641 和 5.589312，H186R 只有 0.811456
        wt_vals = next(vals for label, vals in merged if label == "WT")
        h186r_vals = next(vals for label, vals in merged if label == "H186R")
        assert 5.589312 in wt_vals
        assert 5.589312 not in h186r_vals  # 多遍后消除跨组重复
        assert table_numbers.detect_cross_group_duplicates(merged) == []

    def test_empty_transcripts_returns_empty(self):
        assert table_numbers.merge_transcripts([]) == []

    def test_all_empty_series_returns_empty(self):
        """所有转写都解析不出数值时返回空。"""
        assert table_numbers.merge_transcripts(["", "NA|?"]) == []
