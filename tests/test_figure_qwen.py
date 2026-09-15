"""figure_qwen._ask_vision_on_figure 的 fail-open 单测（P0 测试债 / Tessa #4 等价替换）。

PaperForge 的 figure 视觉理解已切到 Qwen3-VL-4B via ``vision_http_url``
（OpenAI 兼容 llama-server）。该路径在 vision 服务不可达 / 返回错误 / 超时时
必须 **fail-open**（返回 ``None`` 而非抛异常），否则会拖垮整个 figure 任务。

此前该路径 **零测试**（报告 Tessa #4 的等价替换）。本文件补上 fail-open 与
正常解析两类核心用例，并打 ``critical`` 标记，使 CI 在视觉回归时快红灯。

被测函数签名：
    _ask_vision_on_figure(figure_path, caption_text="", timeout=120) -> str | None

依赖：
    - 配置：``get_settings().vision_http_url``（env: ``PAPERFORGE_VISION_HTTP_URL`` / ``VISION_HTTP_URL``）
    - 网络：``requests.post(f"{vision_url}/v1/chat/completions", json=..., timeout=...)``
    - fail-open 语义：文件缺失 / 读图失败 / 连接异常 / 非 200 / 超时 / 空内容 → 返回 ``None``

标记：本文件全部用例属于视觉回归护栏，统一挂 ``@pytest.mark.critical``。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from mock_api.llm.figure_qwen import _ask_vision_on_figure
from mock_api.settings import reset_settings

# 测试用的伪 vision 端点；真实地址不重要，因为 requests.post 会被 mock 掉。
FAKE_VISION_URL = "http://localhost:9/vision-unused"


@pytest.fixture
def figure_png(tmp_path: Path) -> Path:
    """写一个最小的非空 PNG 文件，使 figure_path.exists() 通过。

    HTTP 调用是 mock 的，所以文件内容本身不必是合法 PNG，只要非空即可。
    """
    p = tmp_path / "fig1.png"
    # 8 字节 PNG 签名 + 一些填充字节，保证 read_bytes() 成功且 base64 非空。
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return p


@pytest.fixture
def vision_configured(monkeypatch: pytest.MonkeyPatch):
    """配置 vision_http_url 并清掉 settings 进程级缓存，让被测函数读到它。

    用与 test_lifespan.py 一致的模式：setenv + reset_settings()。
    """
    monkeypatch.setenv("PAPERFORGE_VISION_HTTP_URL", FAKE_VISION_URL)
    reset_settings()
    yield
    reset_settings()


def _fake_post_raising(exc: Exception):
    """返回一个会抛指定异常的 requests.post 替身。"""

    def _boom(*_args, **_kwargs):
        raise exc

    return _boom


@pytest.mark.critical
class TestFigureQwenVisionFailOpen:
    """figure 视觉 HTTP 路径的 fail-open 与正常解析护栏（CI 红灯门禁）。"""

    def test_connection_error_returns_none(
        self, figure_png: Path, vision_configured, monkeypatch: pytest.MonkeyPatch
    ):
        """vision 端点不可达（ConnectionError）→ 返回 None 且不抛异常。"""
        monkeypatch.setattr(
            requests, "post", _fake_post_raising(requests.exceptions.ConnectionError("refused"))
        )
        result = _ask_vision_on_figure(str(figure_png), caption_text="损失曲线", timeout=5)
        assert result is None

    def test_http_500_returns_none(
        self, figure_png: Path, vision_configured, monkeypatch: pytest.MonkeyPatch
    ):
        """vision 服务返回 HTTP 500（raise_for_status 抛 HTTPError）→ 返回 None。"""
        resp = MagicMock()
        resp.status_code = 500
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        monkeypatch.setattr(requests, "post", lambda *a, **k: resp)
        result = _ask_vision_on_figure(str(figure_png), timeout=5)
        assert result is None

    def test_timeout_returns_none(
        self, figure_png: Path, vision_configured, monkeypatch: pytest.MonkeyPatch
    ):
        """请求超时（requests Timeout）→ 返回 None 且不抛异常。"""
        monkeypatch.setattr(
            requests, "post", _fake_post_raising(requests.exceptions.Timeout("timed out"))
        )
        result = _ask_vision_on_figure(str(figure_png), timeout=1)
        assert result is None

    def test_success_parses_content(
        self, figure_png: Path, vision_configured, monkeypatch: pytest.MonkeyPatch
    ):
        """正常返回 OpenAI 风格响应 → 正确解析出 message.content（strip 后）。"""
        captured: dict = {}
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "choices": [{"message": {"content": "这是一张损失随 epoch 下降的曲线图"}}]
        }

        def _spy_post(url, json=None, timeout=None, headers=None):  # noqa: ANN001
            captured["url"] = url
            captured["json"] = json
            captured["timeout"] = timeout
            captured["headers"] = headers
            return resp

        monkeypatch.setattr(requests, "post", _spy_post)
        result = _ask_vision_on_figure(str(figure_png), caption_text="图注", timeout=5)

        assert result == "这是一张损失随 epoch 下降的曲线图"
        # HTTP-first 路由：请求打到 /v1/chat/completions，且超时透传。
        assert captured["url"].endswith("/v1/chat/completions")
        assert captured["timeout"] == 5
        # payload 携带多模态 image_url 消息（而非纯文本）。
        content_parts = captured["json"]["messages"][0]["content"]
        assert any(part.get("type") == "image_url" for part in content_parts)

    def test_empty_content_returns_none(
        self, figure_png: Path, vision_configured, monkeypatch: pytest.MonkeyPatch
    ):
        """vision 返回 200 但 content 为空字符串 → fail-open 返回 None（不回退旧路径）。"""
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"choices": [{"message": {"content": "   "}}]}
        monkeypatch.setattr(requests, "post", lambda *a, **k: resp)
        result = _ask_vision_on_figure(str(figure_png), timeout=5)
        assert result is None

    def test_unconfigured_vision_url_returns_none(self, figure_png: Path, monkeypatch: pytest.MonkeyPatch):
        """未配置 vision_http_url（None）→ 跳过 HTTP，直接 fail-open 返回 None。

        注意：直接 patch get_settings 强制 vision_http_url=None，而不是删 env 变量——
        因为 pydantic-settings 也会从仓库 .env 读取 VISION_HTTP_URL，delenv 清不掉。
        被测函数用 ``from mock_api.settings import get_settings``，patch 模块属性即可生效。
        """
        # 用极简替身强制 vision 未配置（绕过 .env 默认值 http://127.0.0.1:8082）。
        class _NoVisionSettings:
            vision_http_url = None

        monkeypatch.setattr("mock_api.settings.get_settings", lambda: _NoVisionSettings())
        # requests.post 不应被调用：用计数器验证。
        calls = {"n": 0}

        def _count(*a, **k):
            calls["n"] += 1
            raise AssertionError("requests.post 不应在 vision 未配置时被调用")

        monkeypatch.setattr(requests, "post", _count)
        result = _ask_vision_on_figure(str(figure_png), timeout=5)
        assert result is None
        assert calls["n"] == 0
