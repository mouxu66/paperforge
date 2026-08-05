"""测试 mock_api/llm/factory.py 中的 LLMFactory。

覆盖场景：
- 数据库无配置时调用 get_provider() → 抛出 RuntimeError
- 切换模型后，后续请求使用新模型
- detect_provider 关键词识别
- _mask_api_key 脱敏
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from mock_api.llm.factory import LLMFactory, _mask_api_key, detect_provider


# ---------------------------------------------------------------------------
# 辅助函数：构造 mock 配置行（模拟 ORM LLMConfig 对象）
# ---------------------------------------------------------------------------
def _make_config(
    config_id: int = 1,
    display_name: str = "我的 GPT-4o",
    api_url: str = "https://api.openai.com/v1",
    api_key: str = "sk-test1234567890",
    model_id: str = "gpt-4o",
    enabled: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=config_id,
        display_name=display_name,
        api_url=api_url,
        api_key=api_key,
        model_id=model_id,
        enabled=enabled,
    )


# ---------------------------------------------------------------------------
# 数据库无配置 → RuntimeError
# ---------------------------------------------------------------------------
def test_empty_configs_raises_runtime_error():
    """get_provider() 在无配置时抛出 RuntimeError。"""
    factory = LLMFactory()
    with (
        patch.object(factory, "_load_configs_from_db", return_value=[]),
        pytest.raises(RuntimeError, match="未配置任何大模型"),
    ):
        factory.get_provider()


# ---------------------------------------------------------------------------
# 切换模型
# ---------------------------------------------------------------------------
def test_switch_model_changes_provider():
    """switch() 后 get_provider() 返回新模型。"""
    factory = LLMFactory()
    config1 = _make_config(config_id=1, display_name="模型A", model_id="gpt-4o")
    config2 = _make_config(config_id=2, display_name="模型B", model_id="claude-3")

    with (
        patch.object(factory, "_load_configs_from_db", return_value=[config1, config2]),
        patch.object(
            factory, "_load_config_by_id", side_effect=lambda cid: {1: config1, 2: config2}.get(cid)
        ),
    ):
        # 首次调用：自动选择第一个启用的配置
        p1 = factory.get_provider()
        assert p1.model == "gpt-4o"
        assert factory.current_label() == "模型A"

        # 切换到模型 2
        p2 = factory.switch("2")
        assert p2.model == "claude-3"
        assert factory.current_label() == "模型B"

        # 后续 get_provider() 返回新模型
        p3 = factory.get_provider()
        assert p3.model == "claude-3"


def test_switch_to_disabled_model_raises():
    """切换到已禁用的模型 → 抛出 ValueError。"""
    factory = LLMFactory()
    config = _make_config(config_id=1, enabled=False)

    with (
        patch.object(factory, "_load_configs_from_db", return_value=[config]),
        patch.object(factory, "_load_config_by_id", return_value=config),
        pytest.raises(ValueError, match="已禁用"),
    ):
        factory.switch("1")


# ---------------------------------------------------------------------------
# available_models
# ---------------------------------------------------------------------------
def test_available_models():
    """available_models 返回所有已启用配置。"""
    factory = LLMFactory()
    configs = [
        _make_config(config_id=1, display_name="GPT", api_url="https://api.openai.com/v1"),
        _make_config(
            config_id=2, display_name="GLM", api_url="https://open.bigmodel.cn/api/paas/v4"
        ),
    ]
    with patch.object(factory, "_load_configs_from_db", return_value=configs):
        models = factory.available_models()
        assert len(models) == 2
        assert models[0]["value"] == "1"
        assert models[0]["label"] == "GPT"
        assert models[0]["provider"] == "openai"
        assert models[1]["provider"] == "zhipu"


# ---------------------------------------------------------------------------
# detect_provider
# ---------------------------------------------------------------------------
def test_detect_provider_openai():
    assert detect_provider("https://api.openai.com/v1") == "openai"


def test_detect_provider_zhipu():
    assert detect_provider("https://open.bigmodel.cn/api/paas/v4") == "zhipu"


def test_detect_provider_deepseek():
    assert detect_provider("https://api.deepseek.com/v1") == "deepseek"


def test_detect_provider_default():
    """未匹配关键词 → 默认 openai 兼容协议。"""
    assert detect_provider("http://localhost:11434/v1") == "openai"


def test_detect_provider_empty():
    assert detect_provider("") == "openai"


# ---------------------------------------------------------------------------
# _mask_api_key
# ---------------------------------------------------------------------------
def test_mask_api_key_normal():
    key = "sk-abcdef1234567890"
    masked = _mask_api_key(key)
    assert masked == "sk-a***7890"


def test_mask_api_key_short():
    """短 key → 返回 ***。"""
    assert _mask_api_key("short") == "***"


def test_mask_api_key_empty():
    assert _mask_api_key("") == ""


def test_mask_api_key_exactly_8():
    """长度刚好 8 → 返回 ***。"""
    assert _mask_api_key("12345678") == "***"
