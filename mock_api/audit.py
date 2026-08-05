"""API 调用审计日志（Layer 2：调用审计与计量）。

设计：
- 异步线程写入，不阻塞请求
- 从 request.state.auth_result 读取 key_id
- 记录 method / path / status / duration / client_ip
- 查询端点支持按 key_id / 时间范围过滤
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from sqlalchemy import case, func, text
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import ApiCallLog

logger = logging.getLogger(__name__)

# 单线程执行器：保证 SQLite 写入串行，避免并发锁；进程退出时通过 atexit 等待未完成任务，
# 比原始 daemon 线程更可靠，不会丢失待写入的审计日志。
_audit_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit-")


def log_api_call(
    key_id: str,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    client_ip: str = "",
) -> None:
    """异步写入一条 API 调用日志（在新线程中执行，不阻塞请求）。

    Args:
        key_id: 调用方标识（ApiKey.key_id / "global" / "loopback"）
        method: HTTP 方法
        path: 请求路径
        status_code: HTTP 状态码
        duration_ms: 耗时毫秒
        client_ip: 客户端 IP
    """

    def _write():
        db = SessionLocal()
        try:
            log_entry = ApiCallLog(
                key_id=key_id,
                method=method,
                path=path[:500],
                status_code=status_code,
                duration_ms=duration_ms,
                client_ip=client_ip[:64],
            )
            db.add(log_entry)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - 审计日志失败不应影响主请求
            logger.debug("审计日志写入失败: %s", exc)
            db.rollback()
        finally:
            db.close()

    # 提交到单线程池异步执行；进程退出时会等待未完成任务，避免日志丢失
    _audit_executor.submit(_write)


def query_usage_summary(db: Session, hours: int = 24) -> list[dict]:
    """查询最近 N 小时的 per-key 用量统计。

    Returns:
        [{key_id, name, total_calls, success_calls, failed_calls, avg_duration_ms, last_called_at}]
    """
    since = datetime.now() - timedelta(hours=hours)
    # 按 key_id 聚合
    rows = (
        db.query(
            ApiCallLog.key_id,
            func.count(ApiCallLog.id).label("total_calls"),
            func.sum(
                case(
                    (ApiCallLog.status_code < 400, 1),
                    else_=0,
                )
            ).label("success_calls"),
            func.sum(
                case(
                    (ApiCallLog.status_code >= 400, 1),
                    else_=0,
                )
            ).label("failed_calls"),
            func.avg(ApiCallLog.duration_ms).label("avg_duration_ms"),
            func.max(ApiCallLog.timestamp).label("last_called_at"),
        )
        .filter(ApiCallLog.timestamp >= since)
        .group_by(ApiCallLog.key_id)
        .all()
    )

    # 关联 ApiKey 表获取 name
    from .models import ApiKey

    key_names: dict[str, str] = {}
    for ak in db.query(ApiKey).all():
        key_names[ak.key_id] = ak.name

    result = []
    for row in rows:
        key_id = row.key_id
        result.append(
            {
                "keyId": key_id,
                "name": key_names.get(key_id, "全局/本机"),
                "totalCalls": row.total_calls or 0,
                "successCalls": row.success_calls or 0,
                "failedCalls": row.failed_calls or 0,
                "avgDurationMs": round(float(row.avg_duration_ms or 0), 1),
                "lastCalledAt": row.last_called_at.strftime("%Y-%m-%d %H:%M:%S")
                if row.last_called_at
                else None,
            }
        )
    return result


def cleanup_old_logs(db: Session, days: int = 30) -> int:
    """清理 N 天前的审计日志。返回删除行数。"""
    cutoff = datetime.now() - timedelta(days=days)
    result = db.execute(
        text("DELETE FROM api_call_logs WHERE timestamp < :cutoff"),
        {"cutoff": cutoff},
    )
    db.commit()
    return result.rowcount or 0
