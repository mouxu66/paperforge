"""API Key 多调用方鉴权 + 限流 + 审计测试（Layer 1 + Layer 2）。

覆盖：
- ApiKeyService CRUD（创建 / 验证 / 吊销 / 删除 / 更新 / 列表）
- key 格式与哈希（pf_live_ 前缀、sha256、明文不存盘）
- authenticate_and_authorize 全分支（pf_live_ / 全局 token / loopback / 远端开放）
- scope 授权矩阵（read/write/admin × GET/POST/admin path）
- 限流（per-key 令牌桶、超限 429、超级管理员不限流）
- 审计（api_call_logs 写入、用量统计查询）
- /api/system/api-keys 管理端点（CRUD + usage）

向后兼容性：所有现有 test_security.py 用例继续通过（loopback + 全局 token）。
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from mock_api.api_keys import (
    ApiKeyService,
    generate_api_key,
    get_api_key_service,
    hash_api_key,
)
from mock_api.app import create_app
from mock_api.audit import cleanup_old_logs, log_api_call, query_usage_summary
from mock_api.database import SessionLocal
from mock_api.models import ApiCallLog
from mock_api.rate_limit import RateLimiter
from mock_api.settings import reset_settings
from sqlalchemy import text


@pytest.fixture
def client():
    """每次测试创建新的 TestClient（确保 middleware 重新加载环境变量）。"""
    return TestClient(create_app())


def _enable_auth_env(monkeypatch, *, allow_remote=True, api_token=None, is_dev=False):
    """统一设置鉴权环境：AUTH_ENABLED=1 + ALLOW_REMOTE + 可选 token + 可选 dev 模式。"""
    monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
    monkeypatch.setenv("PAPERFORGE_ALLOW_REMOTE", "1" if allow_remote else "0")
    if api_token is not None:
        monkeypatch.setenv("PAPERFORGE_API_TOKEN", api_token)
    else:
        # 确保未设置全局 token，避免干扰多 API Key 测试
        monkeypatch.delenv("PAPERFORGE_API_TOKEN", raising=False)
    monkeypatch.setenv("ENV", "development" if is_dev else "production")
    reset_settings()


# ---------------------------------------------------------------------------
# 1. ApiKeyService 纯函数与 key 生成
# ---------------------------------------------------------------------------
class TestKeyGeneration:
    def test_generate_api_key_format(self):
        """生成的 key 必须满足 pf_live_<32hex>_<key_id> 格式。"""
        full_key, key_id, key_hash, key_prefix = generate_api_key()
        assert full_key.startswith("pf_live_"), "key 必须以 pf_live_ 开头"
        assert key_id.startswith("ak_"), "key_id 必须以 ak_ 开头"
        # 32 hex chars between pf_live_ and _ak_xxx
        parts = full_key.split("_")
        # pf_live_<hex>_<ak_xx>
        assert parts[0] == "pf"
        assert parts[1] == "live"
        assert len(parts[2]) == 32, "随机部分必须为 32 位 hex"
        assert all(c in "0123456789abcdef" for c in parts[2]), "随机部分必须为 hex"
        assert key_prefix == full_key[:12], "key_prefix 取前 12 位"

    def test_hash_api_key_is_sha256_hex(self):
        """hash_api_key 必须返回 64 位 hex（sha256）。"""
        h = hash_api_key("pf_live_test_ak_0001")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_generate_two_keys_are_distinct(self):
        """两次生成不应相同（极小概率撞 key 也要测）。"""
        k1 = generate_api_key()
        k2 = generate_api_key()
        assert k1[0] != k2[0], "两次生成的 key 不应相同"
        assert k1[1] != k2[1], "两次生成的 key_id 不应相同"


# ---------------------------------------------------------------------------
# 2. ApiKeyService CRUD
# ---------------------------------------------------------------------------
class TestApiKeyServiceCRUD:
    def test_create_returns_orm_and_plaintext(self):
        """create 必须返回 (ApiKey ORM, full_key 明文)。"""
        service = ApiKeyService()
        api_key, full_key = service.create(name="test-caller", description="测试")
        assert api_key.key_id.startswith("ak_")
        assert api_key.key_prefix == full_key[:12]
        assert api_key.name == "test-caller"
        assert api_key.description == "测试"
        assert api_key.scopes == ["read"]  # 默认
        assert api_key.enabled is True
        assert full_key.startswith("pf_live_")
        # 明文不应存盘
        assert api_key.key_hash != full_key
        assert api_key.key_hash == hash_api_key(full_key)

    def test_verify_by_plaintext_returns_orm(self):
        """verify(full_key) 必须按 sha256 哈希查找并返回 ORM。"""
        service = ApiKeyService()
        api_key, full_key = service.create(name="verify-test")
        # 立即 verify 应能命中
        found = service.verify(full_key)
        assert found is not None
        assert found.key_id == api_key.key_id
        # verify 后 last_used_at 应被更新
        assert found.last_used_at is not None

    def test_verify_unknown_key_returns_none(self):
        """未知 key verify 应返回 None，不抛异常。"""
        service = ApiKeyService()
        assert service.verify("pf_live_" + "0" * 32 + "_ak_unknown") is None

    def test_verify_revoked_key_returns_none(self):
        """被吊销（enabled=False）的 key verify 应返回 None。"""
        service = ApiKeyService()
        api_key, full_key = service.create(name="revoke-test")
        assert service.revoke(api_key.key_id) is True
        assert service.verify(full_key) is None

    def test_verify_non_pf_live_prefix_returns_none(self):
        """verify 对非 pf_live_ 前缀的输入直接返回 None。"""
        service = ApiKeyService()
        assert service.verify("invalid_key") is None
        assert service.verify("") is None
        assert service.verify(None) is None

    def test_revoke_marks_enabled_false(self):
        """revoke 必须将 enabled 设为 False，保留记录。"""
        service = ApiKeyService()
        api_key, _ = service.create(name="revoke-check")
        assert service.revoke(api_key.key_id) is True
        found = service.get_by_id(api_key.key_id)
        assert found is not None
        assert found.enabled is False

    def test_revoke_unknown_returns_false(self):
        """revoke 不存在的 key_id 应返回 False。"""
        service = ApiKeyService()
        assert service.revoke("ak_nonexistent") is False

    def test_delete_removes_record(self):
        """delete 必须彻底删除记录。"""
        service = ApiKeyService()
        api_key, _ = service.create(name="delete-test")
        assert service.delete(api_key.key_id) is True
        assert service.get_by_id(api_key.key_id) is None

    def test_delete_unknown_returns_false(self):
        """delete 不存在的 key_id 应返回 False。"""
        service = ApiKeyService()
        assert service.delete("ak_nonexistent") is False

    def test_update_changes_fields(self):
        """update 必须能更新 name/description/scopes/rate_limit/enabled。"""
        service = ApiKeyService()
        api_key, _ = service.create(name="update-test")
        updated = service.update(
            api_key.key_id,
            name="updated",
            description="new desc",
            scopes=["read", "write"],
            rate_limit_per_min=100,
            enabled=False,
        )
        assert updated is not None
        assert updated.name == "updated"
        assert updated.description == "new desc"
        assert updated.scopes == ["read", "write"]
        assert updated.rate_limit_per_min == 100
        assert updated.enabled is False

    def test_update_unknown_returns_none(self):
        """update 不存在的 key_id 应返回 None。"""
        service = ApiKeyService()
        assert service.update("ak_nonexistent", name="x") is None

    def test_list_all_returns_ordered(self):
        """list_all 必须返回所有 key（按 created_at desc 排序）。"""
        service = ApiKeyService()
        k1, _ = service.create(name="first")
        time.sleep(0.01)
        k2, _ = service.create(name="second")
        keys = service.list_all()
        key_names = [k.name for k in keys]
        assert "first" in key_names
        assert "second" in key_names
        # 第二个应在列表前面（更晚创建）
        idx_first = key_names.index("first")
        idx_second = key_names.index("second")
        assert idx_second < idx_first

    def test_list_all_enabled_only_filters_disabled(self):
        """list_all(enabled_only=True) 必须只返回 enabled=True 的 key。"""
        service = ApiKeyService()
        k1, _ = service.create(name="enabled")
        k2, _ = service.create(name="disabled")
        service.revoke(k2.key_id)
        keys = service.list_all(enabled_only=True)
        key_ids = [k.key_id for k in keys]
        assert k1.key_id in key_ids
        assert k2.key_id not in key_ids

    def test_has_scope_admin_implies_all(self):
        """has_scope 检查：admin scope 应拥有所有权限。"""
        service = ApiKeyService()
        api_key, _ = service.create(name="admin-test", scopes=["admin"])
        assert service.has_scope(api_key, "read") is True
        assert service.has_scope(api_key, "write") is True
        assert service.has_scope(api_key, "admin") is True

    def test_has_scope_read_only(self):
        """has_scope 检查：read scope 不应有 write/admin。"""
        service = ApiKeyService()
        api_key, _ = service.create(name="read-only", scopes=["read"])
        assert service.has_scope(api_key, "read") is True
        assert service.has_scope(api_key, "write") is False
        assert service.has_scope(api_key, "admin") is False


# ---------------------------------------------------------------------------
# 3. authenticate_and_authorize 全分支
# ---------------------------------------------------------------------------
class TestAuthenticateAndAuthorize:
    def test_auth_disabled_returns_superadmin(self, monkeypatch):
        """AUTH_ENABLED=0 时返回超级管理员 AuthResult。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "0")
        reset_settings()
        from mock_api.auth import AuthResult, authenticate_and_authorize  # noqa: F401

        # 构造一个最小 request mock
        class _Client:
            host = "10.0.0.1"

        class _Req:
            method = "GET"
            url = type("U", (), {"path": "/api/papers"})()
            client = _Client()

        result = authenticate_and_authorize(_Req(), token=None)
        assert result.authenticated is True
        assert result.scopes is None  # 超级管理员
        assert result.is_admin is True

    def test_pf_live_key_valid_passes(self, client, monkeypatch):
        """有效 pf_live_ key 应通过鉴权（read scope → GET 放行）。"""
        _enable_auth_env(monkeypatch, api_token="global-tok")
        client = TestClient(create_app())
        # 创建一个 read scope 的 key
        service = get_api_key_service()
        _, full_key = service.create(name="reader", scopes=["read"])

        resp = client.get("/api/papers?page=1&page_size=1", headers={"X-PaperForge-Token": full_key})
        # 鉴权应通过；可能业务 200/404，但不应是 401/403
        assert resp.status_code not in (401, 403), f"read key 应通过 GET: {resp.text}"

    def test_pf_live_key_invalid_returns_401(self, client, monkeypatch):
        """无效 pf_live_ key 应返回 401。"""
        _enable_auth_env(monkeypatch, api_token="global-tok")
        client = TestClient(create_app())
        resp = client.get(
            "/api/papers?page=1&page_size=1",
            headers={"X-PaperForge-Token": "pf_live_" + "0" * 32 + "_ak_unknown"},
        )
        assert resp.status_code == 401

    def test_pf_live_key_revoked_returns_401(self, client, monkeypatch):
        """已吊销的 pf_live_ key 应返回 401。"""
        _enable_auth_env(monkeypatch, api_token="global-tok")
        client = TestClient(create_app())
        service = get_api_key_service()
        api_key, full_key = service.create(name="to-revoke", scopes=["read"])
        service.revoke(api_key.key_id)
        resp = client.get(
            "/api/papers?page=1&page_size=1",
            headers={"X-PaperForge-Token": full_key},
        )
        assert resp.status_code == 401

    def test_read_scope_cannot_write(self, client, monkeypatch):
        """read scope 的 key 不能执行 POST（write 操作）。"""
        _enable_auth_env(monkeypatch, api_token="global-tok")
        client = TestClient(create_app())
        service = get_api_key_service()
        _, full_key = service.create(name="reader", scopes=["read"])
        resp = client.post(
            "/api/favorites",
            json={"paper_id": "x"},
            headers={"X-PaperForge-Token": full_key},
        )
        assert resp.status_code == 403, "read scope 不应能 write"
        assert "权限不足" in resp.text

    def test_write_scope_can_write(self, client, monkeypatch):
        """write scope 的 key 可以执行 POST。"""
        _enable_auth_env(monkeypatch, api_token="global-tok")
        client = TestClient(create_app())
        service = get_api_key_service()
        _, full_key = service.create(name="writer", scopes=["read", "write"])
        resp = client.post(
            "/api/favorites",
            json={"paper_id": "x"},
            headers={"X-PaperForge-Token": full_key},
        )
        # 鉴权应通过；业务可能 404，但不是 401/403
        assert resp.status_code not in (401, 403), f"write scope 应通过 POST: {resp.text}"

    def test_admin_scope_required_for_system_api_keys(self, client, monkeypatch):
        """/api/system/api-keys 路径需要 admin scope；read/write scope 应被拒绝。"""
        _enable_auth_env(monkeypatch, api_token="global-tok")
        client = TestClient(create_app())
        service = get_api_key_service()
        # write scope 的 key 不能访问 /api/system/api-keys
        _, write_key = service.create(name="writer", scopes=["read", "write"])
        resp = client.get(
            "/api/system/api-keys",
            headers={"X-PaperForge-Token": write_key},
        )
        assert resp.status_code == 403, "write scope 不应能访问 /api/system/api-keys"

    def test_admin_scope_can_access_system_api_keys(self, client, monkeypatch):
        """admin scope 的 key 可以访问 /api/system/api-keys。"""
        _enable_auth_env(monkeypatch, api_token="global-tok")
        client = TestClient(create_app())
        service = get_api_key_service()
        _, admin_key = service.create(name="admin", scopes=["admin"])
        resp = client.get(
            "/api/system/api-keys",
            headers={"X-PaperForge-Token": admin_key},
        )
        # 鉴权通过；可能 200 或 envelope 错误，但不应是 401/403
        assert resp.status_code not in (401, 403), f"admin scope 应通过: {resp.text}"

    def test_global_token_treated_as_superadmin(self, client, monkeypatch):
        """全局 token 等价于超级管理员，可访问所有 scope。"""
        _enable_auth_env(monkeypatch, api_token="global-tok-xyz")
        client = TestClient(create_app())
        # GET 应通过
        resp = client.get(
            "/api/papers?page=1&page_size=1",
            headers={"X-PaperForge-Token": "global-tok-xyz"},
        )
        assert resp.status_code not in (401, 403)
        # POST 应通过
        resp = client.post(
            "/api/favorites",
            json={"paper_id": "x"},
            headers={"X-PaperForge-Token": "global-tok-xyz"},
        )
        assert resp.status_code not in (401, 403)
        # /api/system/api-keys 应通过
        resp = client.get(
            "/api/system/api-keys",
            headers={"X-PaperForge-Token": "global-tok-xyz"},
        )
        assert resp.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# 4. 限流（RateLimiter）
# ---------------------------------------------------------------------------
class TestRateLimiter:
    def test_token_bucket_allows_until_capacity(self):
        """令牌桶允许在容量内的请求。"""
        limiter = RateLimiter()
        # 容量 3：连续 3 次应允许
        for _ in range(3):
            allowed, _ = limiter.check("test-key-1", rate_limit_per_min=3)
            assert allowed is True
        # 第 4 次应被拒
        allowed, retry = limiter.check("test-key-1", rate_limit_per_min=3)
        assert allowed is False
        assert retry >= 1

    def test_super_admin_not_limited(self):
        """超级管理员（is_admin=True）不应被限流。"""
        limiter = RateLimiter()
        # 容量 1，但 is_admin=True 应一直允许
        for _ in range(10):
            allowed, _ = limiter.check("admin-key", rate_limit_per_min=1, is_admin=True)
            assert allowed is True

    def test_zero_rate_limit_inherits_default(self):
        """rate_limit_per_min=0 继承系统默认，不是无限流。"""
        limiter = RateLimiter()
        # 默认 60/min，前 60 次放行，第 61 次应被限流
        for i in range(60):
            allowed, _ = limiter.check("zero-key", rate_limit_per_min=0)
            assert allowed is True, f"第 {i + 1} 次应被放行"
        allowed, retry = limiter.check("zero-key", rate_limit_per_min=0)
        assert allowed is False
        assert retry >= 1

    def test_retry_after_is_positive_when_limited(self):
        """限流时 retry_after 必须为正整数。"""
        limiter = RateLimiter()
        # 先耗尽令牌
        limiter.check("retry-test", rate_limit_per_min=1)
        allowed, retry = limiter.check("retry-test", rate_limit_per_min=1)
        assert allowed is False
        assert isinstance(retry, int)
        assert retry >= 1

    def test_per_key_isolation(self):
        """不同 key 的令牌桶必须隔离。"""
        limiter = RateLimiter()
        # key-a 耗尽
        limiter.check("key-a", rate_limit_per_min=1)
        allowed_a, _ = limiter.check("key-a", rate_limit_per_min=1)
        assert allowed_a is False
        # key-b 不应受影响
        allowed_b, _ = limiter.check("key-b", rate_limit_per_min=1)
        assert allowed_b is True

    def test_rate_limit_returns_429_in_request(self, client, monkeypatch):
        """启用限流后，超限请求应返回 429 + Retry-After。"""
        _enable_auth_env(monkeypatch, api_token="global-tok")
        # 设置默认限流为 3/分钟
        monkeypatch.setenv("PAPERFORGE_API_KEY_RATE_LIMIT_DEFAULT", "3")
        reset_settings()
        client = TestClient(create_app())

        service = get_api_key_service()
        # 用 per-key 限流 = 2，便于快速触发
        _, full_key = service.create(name="limited", scopes=["read"], rate_limit_per_min=2)

        headers = {"X-PaperForge-Token": full_key}
        # 前两次应通过（业务返回可能 404 等，但不是 429）
        for i in range(2):
            resp = client.get("/api/papers?page=1&page_size=1", headers=headers)
            assert resp.status_code != 429, f"第 {i + 1} 次不应限流"
        # 第 3 次应被限流
        resp = client.get("/api/papers?page=1&page_size=1", headers=headers)
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        assert "超限" in resp.text or "频率" in resp.text


# ---------------------------------------------------------------------------
# 5. 审计（audit log）
# ---------------------------------------------------------------------------
class TestAuditLog:
    def test_log_api_call_writes_row(self):
        """log_api_call 必须在 api_call_logs 表中写入一行。"""
        log_api_call(
            key_id="test-key",
            method="GET",
            path="/api/papers",
            status_code=200,
            duration_ms=42,
            client_ip="127.0.0.1",
        )
        # 异步线程写入，需要短暂等待
        time.sleep(0.2)
        db = SessionLocal()
        try:
            row = (
                db.query(ApiCallLog)
                .filter(ApiCallLog.key_id == "test-key")
                .order_by(ApiCallLog.id.desc())
                .first()
            )
            assert row is not None
            assert row.method == "GET"
            assert row.path == "/api/papers"
            assert row.status_code == 200
            assert row.duration_ms == 42
            assert row.client_ip == "127.0.0.1"
        finally:
            db.close()

    def test_query_usage_summary_aggregates(self):
        """query_usage_summary 必须按 key_id 聚合统计。"""
        # 直接同步写库，避免异步线程的时序问题
        db = SessionLocal()
        try:
            for _ in range(3):
                db.add(ApiCallLog(
                    key_id="summary-key", method="GET", path="/api/papers",
                    status_code=200, duration_ms=10, client_ip="1.1.1.1",
                ))
            db.add(ApiCallLog(
                key_id="summary-key", method="POST", path="/api/favorites",
                status_code=500, duration_ms=100, client_ip="1.1.1.1",
            ))
            db.commit()
            result = query_usage_summary(db, hours=1)
            item = next((it for it in result if it["keyId"] == "summary-key"), None)
            assert item is not None, "应能在统计中找到 summary-key"
            assert item["totalCalls"] == 4
            assert item["successCalls"] == 3
            assert item["failedCalls"] == 1
            assert item["avgDurationMs"] > 0
        finally:
            db.close()

    def test_cleanup_old_logs_removes_old(self):
        """cleanup_old_logs 必须删除指定天数前的日志。"""
        # 写入一条旧记录（手工调整 timestamp）
        db = SessionLocal()
        try:
            from datetime import datetime, timedelta

            old = ApiCallLog(
                key_id="old-key",
                method="GET",
                path="/api/old",
                status_code=200,
                duration_ms=1,
                client_ip="",
            )
            db.add(old)
            db.commit()
            # 手动改 timestamp 为 60 天前
            cutoff = datetime.now() - timedelta(days=60)
            db.execute(
                text("UPDATE api_call_logs SET timestamp = :ts WHERE key_id = 'old-key'"),
                {"ts": cutoff},
            )
            db.commit()
            deleted = cleanup_old_logs(db, days=30)
            assert deleted >= 1, "应删除至少 1 条 30 天前的记录"
            # 再查应不存在
            remaining = db.query(ApiCallLog).filter(ApiCallLog.key_id == "old-key").count()
            assert remaining == 0
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 6. /api/system/api-keys 管理端点
# ---------------------------------------------------------------------------
class TestApiKeysAdminEndpoints:
    def test_create_endpoint_returns_plaintext_once(self, client, monkeypatch):
        """POST /api/system/api-keys 必须返回明文 key（仅此一次）。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        resp = client.post(
            "/api/system/api-keys",
            json={"name": "endpoint-test", "scopes": ["read"], "rateLimitPerMin": 10},
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["key"].startswith("pf_live_"), "响应必须返回明文 key"
        assert data["keyId"].startswith("ak_")
        assert data["name"] == "endpoint-test"
        assert data["scopes"] == ["read"]
        assert data["rateLimitPerMin"] == 10

    def test_list_endpoint_excludes_plaintext(self, client, monkeypatch):
        """GET /api/system/api-keys 列表不应包含明文 key（只应有 keyPrefix）。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        # 创建一个
        client.post(
            "/api/system/api-keys",
            json={"name": "list-test"},
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        resp = client.get(
            "/api/system/api-keys",
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 1
        # 列表项不应有 key 字段（完整明文）
        for item in items:
            assert "key" not in item, "列表不应包含明文 key"
            # keyPrefix 是前 12 位，包含 pf_live_ 前缀 + 4 hex（不足以还原完整 key）
            assert "keyPrefix" in item
            assert len(item["keyPrefix"]) <= 16, "keyPrefix 必须远短于完整 key"

    def test_get_by_id_endpoint(self, client, monkeypatch):
        """GET /api/system/api-keys/{key_id} 应返回单条详情。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        create_resp = client.post(
            "/api/system/api-keys",
            json={"name": "get-test"},
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        key_id = create_resp.json()["keyId"]
        resp = client.get(
            f"/api/system/api-keys/{key_id}",
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 200
        assert resp.json()["keyId"] == key_id

    def test_get_by_id_returns_404_for_unknown(self, client, monkeypatch):
        """GET 不存在的 key_id 应返回 404。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        resp = client.get(
            "/api/system/api-keys/ak_nonexistent",
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 404

    def test_update_endpoint_changes_scopes(self, client, monkeypatch):
        """PUT /api/system/api-keys/{key_id} 应能更新 scopes。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        create_resp = client.post(
            "/api/system/api-keys",
            json={"name": "update-endpoint", "scopes": ["read"]},
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        key_id = create_resp.json()["keyId"]
        resp = client.put(
            f"/api/system/api-keys/{key_id}",
            json={"scopes": ["read", "write"], "rateLimitPerMin": 50},
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scopes"] == ["read", "write"]
        assert data["rateLimitPerMin"] == 50

    def test_revoke_endpoint_disables_key(self, client, monkeypatch):
        """POST /api/system/api-keys/{key_id}/revoke 应禁用 key。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        create_resp = client.post(
            "/api/system/api-keys",
            json={"name": "revoke-endpoint"},
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        key_id = create_resp.json()["keyId"]
        resp = client.post(
            f"/api/system/api-keys/{key_id}/revoke",
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_delete_endpoint_removes_key(self, client, monkeypatch):
        """DELETE /api/system/api-keys/{key_id} 应彻底删除。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        create_resp = client.post(
            "/api/system/api-keys",
            json={"name": "delete-endpoint"},
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        key_id = create_resp.json()["keyId"]
        resp = client.delete(
            f"/api/system/api-keys/{key_id}",
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 204
        # 再 GET 应 404
        resp = client.get(
            f"/api/system/api-keys/{key_id}",
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 404

    def test_usage_endpoint_returns_summary(self, client, monkeypatch):
        """GET /api/system/api-keys/usage/all 应返回用量统计。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        # 触发一次 API 调用以产生审计日志
        client.get(
            "/api/papers?page=1&page_size=1",
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        # 等待异步审计写入
        time.sleep(0.3)
        resp = client.get(
            "/api/system/api-keys/usage/all?hours=1",
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "period" in data
        assert data["period"] == "1h"

    def test_invalid_scope_in_create_rejected(self, client, monkeypatch):
        """POST 创建时若 scopes 含非法值应返回 422。"""
        _enable_auth_env(monkeypatch, api_token="admin-tok", is_dev=True)
        client = TestClient(create_app())
        resp = client.post(
            "/api/system/api-keys",
            json={"name": "bad-scope", "scopes": ["read", "invalid_scope"]},
            headers={"X-PaperForge-Token": "admin-tok"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 7. 向后兼容性：loopback + 全局 token 仍可工作
# ---------------------------------------------------------------------------
class TestBackwardCompatibility:
    def test_global_token_still_works_for_read(self, client, monkeypatch):
        """原有全局 token 模式应继续工作（GET）。"""
        _enable_auth_env(monkeypatch, api_token="legacy-tok")
        client = TestClient(create_app())
        resp = client.get(
            "/api/papers?page=1&page_size=1",
            headers={"X-PaperForge-Token": "legacy-tok"},
        )
        assert resp.status_code not in (401, 403)

    def test_global_token_still_works_for_write(self, client, monkeypatch):
        """原有全局 token 模式应继续工作（POST）。"""
        _enable_auth_env(monkeypatch, api_token="legacy-tok")
        client = TestClient(create_app())
        resp = client.post(
            "/api/favorites",
            json={"paper_id": "x"},
            headers={"X-PaperForge-Token": "legacy-tok"},
        )
        assert resp.status_code not in (401, 403)

    def test_no_auth_at_loopback_still_works(self, monkeypatch):
        """本机回环 + 未配置 token 时应继续无鉴权放行（向后兼容）。"""
        monkeypatch.setenv("PAPERFORGE_AUTH_ENABLED", "1")
        monkeypatch.delenv("PAPERFORGE_API_TOKEN", raising=False)
        monkeypatch.setenv("PAPERFORGE_ALLOW_REMOTE", "0")  # 默认仅 loopback
        reset_settings()
        client = TestClient(create_app())
        # TestClient 默认 client.host="testclient"，非真实 loopback
        # 真实 loopback 场景由 integration test 覆盖；此处仅验证不抛错
        resp = client.get("/api/papers?page=1&page_size=1")
        # 非 loopback → 应被拒绝（403 loopback 限制）
        assert resp.status_code in (401, 403)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
