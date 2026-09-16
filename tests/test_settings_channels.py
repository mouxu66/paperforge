"""配置通道纪律测试（P3/P4：显式解析 + 失败响亮）。

覆盖 2026-09-16 引入的两件事：

1. ``resolve_citation_verify_mode``：同一个 ``PAPERFORGE_CITATION_VERIFY``
   此前在三个模块里各有一套「藏起来的默认值」（在线 / 离线 / 跳过），
   现在统一为三态（skip / offline / online）+ 调用方显式声明的默认档。
2. 裁决阈值的**加载期**校验：``verdict_accept_threshold < 0.75`` 这类配置
   必须加载即报错，而不是在 ``_apply_hard_verdict`` 里被 max() 静默夹回。
"""

from __future__ import annotations

import pytest
from mock_api.depth_calibration import validate_verdict_thresholds
from mock_api.settings import (
    Settings,
    env_first_bool,
    get_settings,
    resolve_citation_verify_mode,
)


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """每个用例都从干净配置开始（不继承 .env 或前序用例的 env）。

    注意：不要在这里调用 reset_settings()——它内部用的是模块全局 get_settings，
    被本 fixture patch 成 lambda 后会 AttributeError（monkeypatch 收尾自会还原）。
    """
    monkeypatch.delenv("PAPERFORGE_CITATION_VERIFY", raising=False)
    monkeypatch.setattr("mock_api.settings.get_settings", lambda: Settings(citation_verify=""))
    yield


class TestCitationVerifyMode:
    """三态解析：显式 env > Settings(.env) > 调用方默认档。"""

    def test_unset_falls_back_to_caller_default(self):
        """未配置任何通道时，结果 = 调用方显式声明的默认档。"""
        assert resolve_citation_verify_mode(default="offline") == "offline"
        assert resolve_citation_verify_mode(default="skip") == "skip"
        assert resolve_citation_verify_mode(default="online") == "online"

    @pytest.mark.parametrize("raw", ["0", "false", "off", "no", "none", "disabled", "skip"])
    def test_explicit_off_values_mean_skip(self, monkeypatch, raw):
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", raw)
        assert resolve_citation_verify_mode(default="offline") == "skip"

    @pytest.mark.parametrize("raw", ["1", "true", "yes", "on", "online", "crossref", "TRUE"])
    def test_explicit_on_values_mean_online(self, monkeypatch, raw):
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", raw)
        assert resolve_citation_verify_mode(default="skip") == "online"

    def test_offline_and_unknown_values_stay_local_only(self, monkeypatch):
        """offline（含大小写）与其他非空串 → 仅本地校验，绝不触网。"""
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "OFFLINE")
        assert resolve_citation_verify_mode(default="skip") == "offline"
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "maybe-later")
        assert resolve_citation_verify_mode(default="skip") == "offline"

    def test_env_beats_settings(self, monkeypatch):
        """显式 env（测试/运维热切换）优先于 Settings/.env。"""
        monkeypatch.setattr(
            "mock_api.settings.get_settings", lambda: Settings(citation_verify="1")
        )
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "0")
        assert resolve_citation_verify_mode(default="offline") == "skip"

    def test_settings_channel_is_honoured(self, monkeypatch):
        """.env/Settings 里的值必须生效（旧实现只读 os.environ → 静默失效）。"""
        monkeypatch.setattr(
            "mock_api.settings.get_settings", lambda: Settings(citation_verify="1")
        )
        assert resolve_citation_verify_mode(default="offline") == "online"

    def test_illegal_caller_default_is_loud(self):
        """调用方默认档必须是三态之一，拼错立即报错（不静默当成 offline）。"""
        with pytest.raises(ValueError, match="非法"):
            resolve_citation_verify_mode(default="onlinee")


class TestVerdictThresholdValidation:
    """裁决阈值：加载期校验，不静默夹紧。"""

    def test_settings_reject_accept_below_floor(self):
        with pytest.raises(Exception) as exc:  # pydantic ValidationError
            Settings(verdict_accept_threshold=0.6)
        assert "0.75" in str(exc.value)

    def test_settings_accept_default_is_valid(self):
        s = get_settings()
        assert s.verdict_accept_threshold >= 0.75

    def test_validate_rejects_accept_below_floor(self):
        with pytest.raises(ValueError, match="accept"):
            validate_verdict_thresholds(accept=0.70, reject=0.40)

    def test_validate_rejects_reject_swallowing_minor(self):
        with pytest.raises(ValueError, match="reject"):
            validate_verdict_thresholds(accept=0.80, reject=0.70)

    def test_validate_accepts_sane_ladder(self):
        validate_verdict_thresholds(accept=0.77, reject=0.48)  # 不抛异常即通过


class TestEnvFirstHelpers:
    """env-first 助手：显式 env 优先，非法值回退而不静默用错值。"""

    def test_bool_env_wins(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_REFLECTION_PENALIZE_PAPER_FLAGS", "1")
        assert env_first_bool("PAPERFORGE_REFLECTION_PENALIZE_PAPER_FLAGS", False) is True
        monkeypatch.setenv("PAPERFORGE_REFLECTION_PENALIZE_PAPER_FLAGS", "0")
        assert env_first_bool("PAPERFORGE_REFLECTION_PENALIZE_PAPER_FLAGS", True) is False

    def test_bool_unset_uses_fallback(self, monkeypatch):
        monkeypatch.delenv("PAPERFORGE_REFLECTION_PENALIZE_PAPER_FLAGS", raising=False)
        assert env_first_bool("PAPERFORGE_REFLECTION_PENALIZE_PAPER_FLAGS", True) is True
        assert env_first_bool("PAPERFORGE_REFLECTION_PENALIZE_PAPER_FLAGS", False) is False
