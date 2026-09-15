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

import pytest

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

    def test_integer_last_digit_flagged(self):
        # 整数末位都是 1（11/21/31…），float 表示为 11.0 → 末位应为 1 而非 0
        vals = [11.0, 21.0, 31.0, 41.0, 51.0, 61.0, 71.0, 81.0, 91.0, 101.0]
        flag = table_numbers.detect_digit_preference(vals)
        assert flag is not None
        assert "末位为 1" in flag

    def test_scientific_notation_last_digit_flagged(self):
        # 极小值 str() 用科学计数法（3.1e-05），末位应读有效数字 1 而非指数位 5
        vals = [
            0.000031, 0.000041, 0.000051, 0.000061, 0.000071,
            0.000081, 0.000091, 0.000101, 0.000111, 0.000121,
        ]
        flag = table_numbers.detect_digit_preference(vals)
        assert flag is not None
        assert "末位为 1" in flag


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

    def test_noisy_multi_round_no_false_finding(self, db_session, tmp_path, monkeypatch):
        """多遍转写含噪声遍（值被误归到两组的单遍）→ 多数共识消除，不误报。

        3 遍转写中第 1 遍把 5.589312 同时归到 WT 和 H186R（标签噪声），
        后 2 遍正确归到 WT → 众数投票后 5.589312 只在 WT 保留，
        跨组重复不再命中，不产生 Finding。
        """
        self._seed(db_session, tmp_path, monkeypatch)
        noisy = "WT|0.763641\nH186R|0.811456\nWT|5.589312\nH186R|5.589312"
        clean = "WT|0.763641\nH186R|0.811456\nWT|5.589312"
        call_count = 0

        def mock_transcribe(*args, **kwargs):
            nonlocal call_count
            t = [noisy, clean, clean][call_count]
            call_count += 1
            return t

        monkeypatch.setattr(table_numbers, "transcribe_figure_numbers", mock_transcribe)
        findings = table_numbers.check_figure_number_patterns(db_session, "p-num")
        assert findings == []

    def test_consistent_duplicate_survives_multi_round(self, db_session, tmp_path, monkeypatch):
        """真实跨组重复在全部 3 遍都出现 → 多数共识不误杀，仍命中。"""
        self._seed(db_session, tmp_path, monkeypatch)
        transcript = "WT|0.763641\nH186R|0.811456\nWT|5.589312\nH186R|5.589312"
        monkeypatch.setattr(
            table_numbers,
            "transcribe_figure_numbers",
            lambda *a, **k: transcript,
        )
        findings = table_numbers.check_figure_number_patterns(db_session, "p-num")
        assert len(findings) == 1
        assert "跨组重复" in findings[0]["computed"]

    def test_no_figures_returns_empty(self, db_session):
        assert table_numbers.check_figure_number_patterns(db_session, "no-figs") == []

    def test_all_transcription_fails_raises(self, db_session, tmp_path, monkeypatch):
        """视觉模型转写全部失败（如 8082 停机）→ 响亮失败而非静默 ok findings=0。"""
        self._seed(db_session, tmp_path, monkeypatch)
        monkeypatch.setattr(
            table_numbers,
            "transcribe_figure_numbers",
            lambda *a, **k: "",  # 每遍转写都失败
        )
        with pytest.raises(table_numbers.FigureTranscriptionUnavailable):
            table_numbers.check_figure_number_patterns(db_session, "p-num")

    def test_partial_transcription_success_no_raise(self, db_session, tmp_path, monkeypatch):
        """部分成功（至少一张图转写出来）→ 不抛异常，只对成功图出 finding。"""
        monkeypatch.setattr(table_numbers, "_get_uploads_dir", lambda: tmp_path)
        fig_dir = tmp_path / "figures" / "p-part"
        fig_dir.mkdir(parents=True, exist_ok=True)
        (fig_dir / "f0.png").write_bytes(b"x")
        (fig_dir / "f1.png").write_bytes(b"y")
        db_session.add(Paper(id="p-part", title="Partial"))
        db_session.add(
            PaperFigure(
                paper_id="p-part", page=3, figure_index=0,
                figure_path="figures/p-part/f0.png", figure_number=1,
                caption_text="ok",
            )
        )
        db_session.add(
            PaperFigure(
                paper_id="p-part", page=4, figure_index=1,
                figure_path="figures/p-part/f1.png", figure_number=2,
                caption_text="fail",
            )
        )
        db_session.commit()

        def mock_transcribe(image_path, *a, **k):
            return "" if "f1.png" in str(image_path) else "WT|0.763641\nH186R|0.763641"

        monkeypatch.setattr(table_numbers, "transcribe_figure_numbers", mock_transcribe)
        findings = table_numbers.check_figure_number_patterns(db_session, "p-part")
        # f0 成功转写且跨组重复 → 1 条 finding；f1 转写失败被跳过，但不抛异常
        assert len(findings) == 1
        assert "跨组重复" in findings[0]["computed"]

    def test_cross_figure_duplicate_produces_finding(self, db_session, tmp_path, monkeypatch):
        """两张 figure 共享完全相同的数据 → 跨表复制 Finding。"""
        monkeypatch.setattr(table_numbers, "_get_uploads_dir", lambda: tmp_path)
        fig_dir = tmp_path / "figures" / "p-cross"
        fig_dir.mkdir(parents=True, exist_ok=True)
        (fig_dir / "fig7i.png").write_bytes(b"x")
        (fig_dir / "fig8j.png").write_bytes(b"y")
        db_session.add(Paper(id="p-cross", title="Cross Figure Dup"))
        db_session.add(
            PaperFigure(
                paper_id="p-cross",
                page=5,
                figure_index=0,
                figure_path="figures/p-cross/fig7i.png",
                figure_number=1,
                caption_text="RpL12 RNAi",
            )
        )
        db_session.add(
            PaperFigure(
                paper_id="p-cross",
                page=8,
                figure_index=1,
                figure_path="figures/p-cross/fig8j.png",
                figure_number=2,
                caption_text="RpL12 OE",
            )
        )
        db_session.commit()

        # 两张图的数据完全相同
        transcript = "RpS25|77.40741\nRpS25|84.47466\nRpS25|73.37331"
        call_count = 0

        def mock_transcribe(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return transcript

        monkeypatch.setattr(table_numbers, "transcribe_figure_numbers", mock_transcribe)
        findings = table_numbers.check_figure_number_patterns(db_session, "p-cross")

        # 应包含跨表复制 Finding
        cross_findings = [f for f in findings if "跨表" in f.get("title", "")]
        assert len(cross_findings) >= 1
        cf = cross_findings[0]
        assert cf["type"] == "SUSPICIOUS_DATA_PATTERN"
        assert cf["severity"] == "high"
        assert cf["needs_human_review"] is True
        assert "Figure 1" in cf["title"]
        assert "Figure 2" in cf["title"]
        assert cf["evidence_sources"][0]["shared_count"] == 3

    def test_axis_ticks_only_no_finding(self, db_session, tmp_path, monkeypatch):
        """只转写出轴刻度（圆整数）→ 过滤后无真实数据点，不产生 Finding。"""
        self._seed(db_session, tmp_path, monkeypatch)
        monkeypatch.setattr(
            table_numbers,
            "transcribe_figure_numbers",
            lambda *a, **k: "WT|100.0\nH186R|100.0\nWT|150.0\nH186R|200.0",
        )
        assert table_numbers.check_figure_number_patterns(db_session, "p-num") == []

    def test_ticks_filtered_but_real_duplicate_detected(self, db_session, tmp_path, monkeypatch):
        """轴刻度被过滤，但真实数据点的跨组重复仍应命中。"""
        self._seed(db_session, tmp_path, monkeypatch)
        monkeypatch.setattr(
            table_numbers,
            "transcribe_figure_numbers",
            lambda *a, **k: (
                "WT|100.0\nH186R|100.0\n"  # 轴刻度（过滤）
                "WT|0.763641\nH186R|0.811456\n"
                "WT|5.589312\nH186R|5.589312\n"  # 真实跨组重复（保留）
                "WT|7.992131\nH186R|7.992131"
            ),
        )
        findings = table_numbers.check_figure_number_patterns(db_session, "p-num")
        assert len(findings) == 1
        assert "跨组重复" in findings[0]["computed"]
        assert "100.0" not in findings[0]["computed"]

    def test_axis_tick_filter_can_be_disabled(self, db_session, tmp_path, monkeypatch):
        """filter_axis_ticks=False 时不过滤，轴刻度 100.0 被当数据点命中跨组重复。"""
        self._seed(db_session, tmp_path, monkeypatch)
        monkeypatch.setattr(
            table_numbers,
            "transcribe_figure_numbers",
            lambda *a, **k: "WT|100.0\nH186R|100.0",
        )
        # 默认（过滤开启）→ 轴刻度被剔除，无 finding
        assert table_numbers.check_figure_number_patterns(db_session, "p-num") == []
        # 显式关闭过滤 → 100.0 保留，跨组重复命中
        findings = table_numbers.check_figure_number_patterns(
            db_session, "p-num", filter_axis_ticks=False
        )
        assert len(findings) == 1
        assert "跨组重复" in findings[0]["computed"]
        assert "100.0" in findings[0]["computed"]

    def test_axis_tick_filter_respects_settings_flag(self, db_session, tmp_path, monkeypatch):
        """运行时配置 depth_axis_tick_filter_enabled=False 时，默认路径不过滤轴刻度。"""
        self._seed(db_session, tmp_path, monkeypatch)
        monkeypatch.setattr(
            table_numbers,
            "transcribe_figure_numbers",
            lambda *a, **k: "WT|100.0\nH186R|100.0",
        )

        class _NoTickFilterSettings:
            depth_axis_tick_filter_enabled = False

        monkeypatch.setattr("mock_api.settings.get_settings", lambda: _NoTickFilterSettings())
        findings = table_numbers.check_figure_number_patterns(db_session, "p-num")
        assert len(findings) == 1
        assert "100.0" in findings[0]["computed"]


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

    def test_inconsistent_runs_no_majority_drops_values(self):
        """多遍转写互相不一致且无任一值达到 2/3 众数 → 整组剔除，不产生误判。

        场景：3 遍转写对同一组数值各抄各的（VLM 抖动），没有任何一个值
        在 ≥2 遍中出现 → 众数投票后该组被剔除，避免把偶发值当真实数据。
        """
        transcripts = [
            "A|0.123\nB|0.456",
            "A|0.789\nB|0.123",  # A/B 的值与第 1 遍互换
            "A|0.456\nB|0.789",  # 与第 1、2 遍都不同
        ]
        merged = table_numbers.merge_transcripts(transcripts)
        # 每个值都只出现 1/3 遍，低于 2/3 阈值 → 全部剔除
        assert merged == []

    def test_inconsistent_runs_no_false_cross_group_duplicate(self):
        """值跨遍乱配对时不产生跨组重复误报。

        场景：VLM 每遍把 0.456 归到不同组（A→B→A）。单遍看 0.456 会
        同时出现在 A 和 B 而触发跨组重复；但 0.456 从未在任一组达到
        2/3 众数 → 合并后被剔除，跨组重复信号也随之消失。
        """
        transcripts = [
            "A|0.456\nB|0.789",
            "A|0.123\nB|0.456",
            "A|0.456\nB|0.789",
        ]
        merged = table_numbers.merge_transcripts(transcripts)
        assert merged == [("A", [0.456]), ("B", [0.789])]
        assert table_numbers.detect_cross_group_duplicates(merged) == []


class TestDetectCrossFigureDuplicates:
    def test_identical_data_across_figures_flagged(self):
        """两组完全相同的数据（如 RpL12 RNAi vs OE 的 WT 组）应触发跨表复制检测。"""
        fig_data = [
            (
                "Figure 1 (RNAi)",
                [
                    ("RpS25", [77.40741, 84.47466, 73.37331]),
                    ("RpL12", [71.42857, 80.69768, 73.37058]),
                ],
            ),
            (
                "Figure 2 (OE)",
                [
                    ("RpS25", [67.40741, 74.57585, 63.37331]),  # 不同值
                    ("RpL12", [61.42857, 70.69938, 63.37058]),  # 不同值
                ],
            ),
        ]
        # 没有重复值 → 不应触发
        assert table_numbers.detect_cross_figure_duplicates(fig_data) == []

    def test_shared_values_detected(self):
        """两个 figure 共享完全相同的数值 → 应命中。"""
        fig_data = [
            (
                "Figure 1",
                [
                    ("RpS25", [77.40741, 84.47466, 73.37331]),
                    ("RpL12", [71.42857, 80.69768, 73.37058]),
                ],
            ),
            (
                "Figure 2",
                [
                    ("RpS25", [77.40741, 84.47466, 73.37331]),  # 完全相同
                    ("RpL12", [71.42857, 80.69768, 73.37058]),  # 完全相同
                ],
            ),
        ]
        flags = table_numbers.detect_cross_figure_duplicates(fig_data)
        assert len(flags) == 2
        # 两个 figure 都应被标记
        fig_ids = [f["figure_id"] for f in flags]
        assert "Figure 1" in fig_ids
        assert "Figure 2" in fig_ids
        # 共享数量应为 6（所有值都相同）
        assert all(f["shared_count"] == 6 for f in flags)
        assert all(f["overlap_pct"] == 100.0 for f in flags)

    def test_partial_overlap(self):
        """部分重叠 → 应标记共享数量。"""
        fig_data = [
            (
                "Figure 1",
                [("WT", [1.111111, 2.222222, 3.333333, 4.444444])],
            ),
            (
                "Figure 2",
                [("WT", [1.111111, 2.222222, 9.999999])],  # 2个相同 1个不同
            ),
        ]
        flags = table_numbers.detect_cross_figure_duplicates(fig_data)
        assert len(flags) == 2
        # 共享 2 个值
        assert all(f["shared_count"] == 2 for f in flags)
        # Figure 1 中 50% 重叠（2/4）
        f1 = next(f for f in flags if f["figure_id"] == "Figure 1")
        assert f1["overlap_pct"] == 50.0

    def test_single_figure_no_flag(self):
        """只有 1 个 figure → 不应检测。"""
        fig_data = [("Figure 1", [("WT", [1.1, 2.2, 3.3])])]
        assert table_numbers.detect_cross_figure_duplicates(fig_data) == []

    def test_empty_fig_data(self):
        assert table_numbers.detect_cross_figure_duplicates([]) == []

    def test_no_shared_values(self):
        """完全没有共享值 → 不触发。"""
        fig_data = [
            ("Figure 1", [("WT", [1.1, 2.2])]),
            ("Figure 2", [("WT", [3.3, 4.4])]),
        ]
        assert table_numbers.detect_cross_figure_duplicates(fig_data) == []

    def test_shared_across_different_labels(self):
        """相同数值出现在不同 label 的不同 figure 中 → 也应命中。"""
        fig_data = [
            (
                "Figure 1 (WT)",
                [("RpS25", [77.40741, 84.47466])],
            ),
            (
                "Figure 2 (OE)",
                [("RpL12", [77.40741, 84.47466])],  # 相同数值，不同 label
            ),
        ]
        flags = table_numbers.detect_cross_figure_duplicates(fig_data)
        assert len(flags) == 2
        assert all(f["shared_count"] == 2 for f in flags)


class TestDecimalPrecisionConsistency:
    def test_all_same_precision_flagged(self):
        """3 个值全部 7 位小数 → 命中。"""
        series = [("A/C", [0.7694589, 0.7462303, 0.8444331])]
        flag = table_numbers.detect_decimal_precision_consistency(series)
        assert flag is not None
        assert "精度一致" in flag
        assert "7 位小数" in flag

    def test_different_precision_not_flagged(self):
        """精度不同 → 不命中。"""
        series = [("A/D", [1.59203, 1.47695, 1.5254])]  # 5, 5, 4 位小数
        assert table_numbers.detect_decimal_precision_consistency(series) is None

    def test_too_few_values_not_flagged(self):
        """只有 2 个值 → 不命中。"""
        series = [("A/B", [1.14118, 1.14661])]
        assert table_numbers.detect_decimal_precision_consistency(series) is None

    def test_low_precision_not_flagged(self):
        """精度 <5 位 → 不命中（避免误报）。"""
        series = [("X", [1.12, 2.34, 3.56])]  # 全部 2 位小数
        assert table_numbers.detect_decimal_precision_consistency(series) is None


class TestComplementaryGroups:
    def test_sums_to_constant_flagged(self):
        """两组数值之和全部为 100 → 命中。"""
        series = [
            ("WT", [72.83951, 78.75, 76.2176]),
            ("M", [27.16049, 21.25, 23.7824]),
        ]
        flags = table_numbers.detect_complementary_groups(series)
        assert len(flags) >= 1
        assert any("互补" in f for f in flags)

    def test_highly_similar_flagged(self):
        """两组数值高度相似（相对差异 <5%）→ 命中。"""
        series = [
            ("WT", [50.0, 60.0, 70.0]),
            ("M", [50.1, 59.9, 70.2]),  # 差异 <1%
        ]
        flags = table_numbers.detect_complementary_groups(series)
        assert any("高度相似" in f for f in flags)

    def test_different_groups_not_flagged(self):
        """两组数值差异大 → 不命中。"""
        series = [
            ("WT", [50.0, 60.0, 70.0]),
            ("M", [10.0, 20.0, 30.0]),
        ]
        flags = table_numbers.detect_complementary_groups(series)
        assert flags == []

    def test_too_few_pairs_not_flagged(self):
        """只有 2 对 → 不命中（需要 ≥3）。"""
        series = [
            ("WT", [50.0, 60.0]),
            ("M", [50.0, 60.0]),
        ]
        flags = table_numbers.detect_complementary_groups(series)
        assert flags == []


class TestAxisTickFilter:
    """轴刻度/归一化基线过滤：圆整数剔除，真实测量值保留。"""

    def test_round_ints_are_ticks(self):
        for v in [0.0, 1.0, 2.0, 3.0, 5.0, 100.0, 150.0, 200.0, 250.0, 300.0]:
            assert table_numbers._is_axis_tick_like(v) is True, v

    def test_real_measurements_not_ticks(self):
        for v in [0.13398, 77.40741, 0.763641, 5.589312, 7.992131, 1.234]:
            assert table_numbers._is_axis_tick_like(v) is False, v

    def test_negative_not_tick(self):
        assert table_numbers._is_axis_tick_like(-5.0) is False

    def test_filter_removes_ticks_keeps_data(self):
        series = [
            ("WT", [100.0, 0.763641, 150.0]),
            ("H186R", [100.0, 0.811456]),
            ("只刻度", [200.0, 250.0, 300.0]),  # 整组只剩刻度 → 丢弃
        ]
        filtered = table_numbers.filter_axis_ticks_and_baseline(series)
        assert filtered == [
            ("WT", [0.763641]),
            ("H186R", [0.811456]),
        ]

    def test_filter_empty(self):
        assert table_numbers.filter_axis_ticks_and_baseline([]) == []

    def test_switch_explicit_override_wins(self):
        assert table_numbers._axis_tick_filter_enabled(True) is True
        assert table_numbers._axis_tick_filter_enabled(False) is False

    def test_switch_reads_settings_when_none(self, monkeypatch):
        class _NoTickFilterSettings:
            depth_axis_tick_filter_enabled = False

        monkeypatch.setattr("mock_api.settings.get_settings", lambda: _NoTickFilterSettings())
        assert table_numbers._axis_tick_filter_enabled(None) is False
