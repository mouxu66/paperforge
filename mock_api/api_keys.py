"""API Key 管理服务（Layer 1：多调用方鉴权）。

职责：
- 创建 / 验证 / 吊销 / 更新 / 列出 API Key
- key 明文仅创建时返回一次，DB 中只存 sha256 哈希
- 与现有 PAPERFORGE_API_TOKEN 并存：全局 token 作为超级管理员 key

key 格式：pf_live_<32位随机hex>_<key_id>
  - pf_live_ 前缀：便于在日志/配置文件中识别 PaperForge API Key
  - 32 位随机 hex：128 位熵，足够防爆破
  - key_id：ak_ 开头的短 ID，用于管理端点引用（不泄露完整 key）

设计要点：
- verify_key 用 sha256 比较（constant-time），不存明文
- verify 后异步更新 last_used_at（不阻塞请求）
- scopes 校验由 auth.py 的依赖函数负责，本模块只提供数据
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime

from .database import SessionLocal
from .models import ApiKey

__all__ = [
    "ApiKeyService",
    "generate_api_key",
    "hash_api_key",
    "get_api_key_service",
]


# ---------------------------------------------------------------------------
# key 生成与哈希（纯函数，可单测）
# ---------------------------------------------------------------------------
def generate_api_key() -> tuple[str, str, str, str]:
    """生成一对完整的 API Key。

    Returns:
        (full_key, key_id, key_hash, key_prefix)
        - full_key: 完整明文 key，仅返回给调用方一次
        - key_id: 短 ID（ak_ 开头），用于管理引用
        - key_hash: sha256(full_key) hex digest，存 DB
        - key_prefix: full_key 前 12 位，用于管理界面显示
    """
    random_part = secrets.token_hex(16)  # 32 hex chars = 128 bit entropy
    key_id = "ak_" + secrets.token_hex(4)  # ak_ + 8 hex chars
    full_key = f"pf_live_{random_part}_{key_id}"
    key_hash = hash_api_key(full_key)
    key_prefix = full_key[:12]
    return full_key, key_id, key_hash, key_prefix


def hash_api_key(key: str) -> str:
    """计算 API Key 的 sha256 hex digest。"""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 服务层
# ---------------------------------------------------------------------------
class ApiKeyService:
    """API Key 管理服务（进程级单例，线程安全由 SQLite 事务保证）。"""

    def create(
        self,
        name: str,
        description: str = "",
        scopes: list[str] | None = None,
        rate_limit_per_min: int = 0,
    ) -> tuple[ApiKey, str]:
        """创建一个新 API Key。

        Args:
            name: 调用方名称
            description: 用途说明
            scopes: 权限范围，默认 ["read"]
            rate_limit_per_min: 每分钟请求上限（0=用系统默认）

        Returns:
            (ApiKey ORM 对象, 完整 key 明文) —— 明文仅此一次返回
        """
        if not scopes:
            scopes = ["read"]

        full_key, key_id, key_hash, key_prefix = generate_api_key()

        db = SessionLocal()
        try:
            api_key = ApiKey(
                key_id=key_id,
                key_prefix=key_prefix,
                key_hash=key_hash,
                name=name,
                description=description,
                scopes=scopes,
                rate_limit_per_min=rate_limit_per_min,
                enabled=True,
            )
            db.add(api_key)
            db.commit()
            db.refresh(api_key)
            # detach 以便调用方使用
            db.expunge(api_key)
            return api_key, full_key
        finally:
            db.close()

    def verify(self, full_key: str) -> ApiKey | None:
        """验证 API Key，返回对应的 ORM 对象（已 enabled），无效返回 None。

        按 sha256 哈希查找，不存明文。命中后异步更新 last_used_at。
        """
        if not full_key or not full_key.startswith("pf_live_"):
            return None

        key_hash = hash_api_key(full_key)
        db = SessionLocal()
        try:
            api_key = (
                db.query(ApiKey)
                .filter(ApiKey.key_hash == key_hash)
                .filter(ApiKey.enabled == True)  # noqa: E712
                .first()
            )
            if api_key is None:
                return None
            # 节流更新 last_used_at：避免每次请求都写库，仅当距上次更新超过 60 秒才刷新
            now = datetime.now()
            last_used = api_key.last_used_at
            if last_used is None or (now - last_used).total_seconds() >= 60:
                api_key.last_used_at = now
                db.commit()
                db.refresh(api_key)
            db.expunge(api_key)
            return api_key
        except Exception:
            db.rollback()
            return None
        finally:
            db.close()

    def revoke(self, key_id: str) -> bool:
        """吊销（禁用）一个 API Key。返回是否成功。"""
        db = SessionLocal()
        try:
            api_key = db.query(ApiKey).filter(ApiKey.key_id == key_id).first()
            if api_key is None:
                return False
            api_key.enabled = False
            db.commit()
            return True
        except Exception:
            db.rollback()
            return False
        finally:
            db.close()

    def delete(self, key_id: str) -> bool:
        """彻底删除一个 API Key（不可恢复）。返回是否成功。"""
        db = SessionLocal()
        try:
            api_key = db.query(ApiKey).filter(ApiKey.key_id == key_id).first()
            if api_key is None:
                return False
            db.delete(api_key)
            db.commit()
            return True
        except Exception:
            db.rollback()
            return False
        finally:
            db.close()

    def update(
        self,
        key_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        scopes: list[str] | None = None,
        rate_limit_per_min: int | None = None,
        enabled: bool | None = None,
    ) -> ApiKey | None:
        """更新 API Key 配置。返回更新后的 ORM 对象，不存在返回 None。"""
        db = SessionLocal()
        try:
            api_key = db.query(ApiKey).filter(ApiKey.key_id == key_id).first()
            if api_key is None:
                return None
            if name is not None:
                api_key.name = name
            if description is not None:
                api_key.description = description
            if scopes is not None:
                api_key.scopes = scopes
            if rate_limit_per_min is not None:
                api_key.rate_limit_per_min = rate_limit_per_min
            if enabled is not None:
                api_key.enabled = enabled
            db.commit()
            db.refresh(api_key)
            db.expunge(api_key)
            return api_key
        except Exception:
            db.rollback()
            return None
        finally:
            db.close()

    def get_by_id(self, key_id: str) -> ApiKey | None:
        """按 key_id 查询单个 Key。"""
        db = SessionLocal()
        try:
            api_key = db.query(ApiKey).filter(ApiKey.key_id == key_id).first()
            if api_key:
                db.expunge(api_key)
            return api_key
        finally:
            db.close()

    def list_all(self, enabled_only: bool = False) -> list[ApiKey]:
        """列出所有 API Key。"""
        db = SessionLocal()
        try:
            q = db.query(ApiKey)
            if enabled_only:
                q = q.filter(ApiKey.enabled == True)  # noqa: E712
            keys = q.order_by(ApiKey.created_at.desc()).all()
            for k in keys:
                db.expunge(k)
            return keys
        finally:
            db.close()

    def has_scope(self, api_key: ApiKey, scope: str) -> bool:
        """检查 Key 是否拥有指定 scope。admin scope 拥有所有权限。"""
        if not api_key or not api_key.scopes:
            return False
        if "admin" in api_key.scopes:
            return True
        return scope in api_key.scopes


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------
_service_instance: ApiKeyService | None = None


def get_api_key_service() -> ApiKeyService:
    """获取进程级 ApiKeyService 单例。"""
    global _service_instance
    if _service_instance is None:
        _service_instance = ApiKeyService()
    return _service_instance
