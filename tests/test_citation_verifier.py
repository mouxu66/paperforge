"""引用真值校验单测：占位符 DOI / 假 arXiv 号本地启发式 + 默认在线开关。"""

from __future__ import annotations

import pytest

from mock_api.integrity.citation_verifier import (
    ONLINE_ENABLED,
    _monotonic_digit_run,
    detect_fake_arxiv_ids,
    detect_placeholder_dois,
    verify_citations,
)


class TestMonotonicDigitRun:
    def test_ascending_run(self):
        assert _monotonic_digit_run("xx1234567yy", 6)

    def test_descending_run(self):
        assert _monotonic_digit_run("98765", 5)

    def test_wraparound_ascending(self):
        # 8→9→0→1→2→3→4 回绕递增
        assert _monotonic_digit_run("8901234", 6)

    def test_no_run(self):
        assert not _monotonic_digit_run("170603762", 6)
        assert not _monotonic_digit_run("a1b2c3d4", 6)

    def test_non_digit_breaks_run(self):
        # 数字串被 "." 打断后各自长度不足
        assert not _monotonic_digit_run("12.34.56", 3)

    def test_short_run_not_detected(self):
        assert not _monotonic_digit_run("12345", 6)


class TestPlaceholderDois:
    def test_placeholder_doi_detected(self):
        assert detect_placeholder_dois(["10.5555/1234567.8901234"]) == [
            "10.5555/1234567.8901234"
        ]

    def test_real_dois_not_flagged(self):
        real = [
            "10.18653/v1/d16-1147",
            "10.1145/2623330.2623623",
            "10.1609/aaai.v37i5.28901",
            "10.1007/978-3-319-93417-4_38",
        ]
        assert detect_placeholder_dois(real) == []

    def test_sequential_placeholder_family_detected(self):
        dois = [
            "10.5555/1234567.8901234",
            "10.5555/1234567.8901235",
            "10.5555/1234567.8901236",
        ]
        assert detect_placeholder_dois(dois) == dois


class TestFakeArxivIds:
    def test_sequential_serial_detected(self):
        assert "2501.98765" in detect_fake_arxiv_ids(["2501.98765"])
        assert "2405.12345" in detect_fake_arxiv_ids(["2405.12345"])

    def test_invalid_month_detected(self):
        assert "2513.00001" in detect_fake_arxiv_ids(["2513.00001"])
        assert "2400.00001" in detect_fake_arxiv_ids(["2400.00001"])

    def test_real_arxiv_ids_not_flagged(self):
        real = ["1706.03762", "2010.11929", "1902.10197", "1609.02907", "2012.09699"]
        assert detect_fake_arxiv_ids(real) == []


class TestVerifyCitationsOffline:
    def test_fabricated_patterns_mark_suspect_offline(self):
        text = (
            "We build on prior work [1].\n"
            "References:\n"
            "[1] Some Author. A fake paper. DOI: 10.5555/1234567.8901234 arXiv:2501.98765\n"
        )
        rep = verify_citations(text, verify_online=False)
        assert rep.status == "suspect"
        assert "10.5555/1234567.8901234" in rep.placeholder_dois
        assert "2501.98765" in rep.suspect_arxiv_ids

    def test_clean_text_stays_unchecked_offline(self):
        text = "We cite [1]. DOI: 10.1145/2623330.2623623 arXiv:1706.03762."
        rep = verify_citations(text, verify_online=False)
        assert rep.status == "unchecked"
        assert rep.placeholder_dois == []
        assert rep.suspect_arxiv_ids == []

    def test_no_citations_offline(self):
        rep = verify_citations("普通正文，没有引用。", verify_online=False)
        assert rep.status == "unchecked"
        assert rep.total_dois == 0


class TestOnlineDefault:
    def test_online_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("PAPERFORGE_CITATION_VERIFY", raising=False)
        assert ONLINE_ENABLED() is True

    def test_explicit_zero_disables_online(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "0")
        assert ONLINE_ENABLED() is False

    def test_offline_value_disables_online(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "offline")
        assert ONLINE_ENABLED() is False

    def test_explicit_one_enables_online(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "1")
        assert ONLINE_ENABLED() is True


class TestVerifyCitationsOnlineNoNetwork:
    """在线模式下无 DOI 时不触网，返回 unknown 而非崩溃。"""

    def test_no_dois_no_network(self):
        rep = verify_citations("没有 DOI 的正文。", verify_online=True)
        assert rep.status == "unknown"
        assert rep.checked_dois == 0
