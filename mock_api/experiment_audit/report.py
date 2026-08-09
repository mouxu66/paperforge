"""P0-10 审计报告生成（JSON → 手写 HTML，浏览器存 PDF）。

与 integrity_report.py 同原则：报告内容逐字来自数据库记录，
不做 LLM 转述；normal_explanation 作为免责列呈现，防止审计被
误读为「定罪结论」。
"""

from __future__ import annotations

import html
from datetime import datetime

from .schemas import FINDING_TYPES, SEVERITY_ORDER, coerce_findings

_SEVERITY_LABEL = {"high": "高", "medium": "中", "low": "低"}
_SEVERITY_COLOR = {"high": "#c0392b", "medium": "#d68910", "low": "#7f8c8d"}

_CSS = """
body { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 32px;
       color: #2c3e50; line-height: 1.55; }
h1 { font-size: 22px; border-bottom: 2px solid #2c3e50; padding-bottom: 8px; }
.meta { color: #7f8c8d; font-size: 13px; margin-bottom: 20px; }
.summary span { display: inline-block; margin-right: 16px; padding: 2px 10px;
                border-radius: 10px; color: #fff; font-size: 13px; }
table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 13px; }
th, td { border: 1px solid #d5dbdb; padding: 6px 9px; vertical-align: top;
         text-align: left; }
th { background: #f4f6f7; }
.sev { color: #fff; border-radius: 4px; padding: 1px 8px; font-size: 12px;
       white-space: nowrap; }
.code { font-family: Consolas, monospace; font-size: 12px; background: #f8f9f9;
        padding: 2px 5px; border-radius: 3px; word-break: break-all; }
.note { color: #7f8c8d; font-size: 12px; margin-top: 24px; border-top:
        1px solid #d5dbdb; padding-top: 10px; }
.checks td:first-child { font-family: Consolas, monospace; }
h2 { font-size: 16px; margin-top: 28px; }
"""


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _finding_row(f: dict) -> str:
    sev = f.get("severity", "low")
    evidence = "; ".join(
        (
            e.get("table_id")
            or e.get("figure_id")
            or (f"p{e.get('page')}" if e.get("page") else "")
            or (e.get("snippet") or "")[:60]
        )
        for e in f.get("evidence_sources") or []
    )
    review = "是" if f.get("needs_human_review") else "否"
    return (
        "<tr>"
        f"<td>{_esc(f.get('finding_id'))}</td>"
        f"<td><span class='sev' style='background:{_SEVERITY_COLOR.get(sev, '#999')}'>"
        f"{_SEVERITY_LABEL.get(sev, sev)}</span></td>"
        f"<td>{_esc(f.get('type'))}<br><b>{_esc(f.get('title'))}</b></td>"
        f"<td>{_esc(f.get('page') or '-')}</td>"
        f"<td>{_esc(f.get('claim') or '-')}</td>"
        f"<td>{_esc(f.get('computed') or '-')}</td>"
        f"<td><span class='code'>{_esc(f.get('method') or '-')}</span></td>"
        f"<td>{_esc(evidence) or '-'}</td>"
        f"<td>{_esc(f.get('normal_explanation') or '-')}</td>"
        f"<td>{review}</td>"
        "</tr>"
    )


def render_audit_html(audit, paper_title: str = "") -> str:
    """渲染审计记录为完整 HTML 页面。

    Args:
        audit: ExperimentAudit ORM 记录（或含同名字段的对象）
        paper_title: 论文标题（展示用）
    """
    # 读路径校验：DB 脏数据降级为占位 Finding，不击穿报告渲染
    findings = coerce_findings(audit.findings)
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.get("severity", "low"), 3))
    checks = list(audit.checks_run or [])

    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f.get("severity", "low")] = counts.get(f.get("severity", "low"), 0) + 1

    summary = "".join(
        f"<span style='background:{_SEVERITY_COLOR[s]}'>{_SEVERITY_LABEL[s]} {n}</span>"
        for s, n in counts.items()
        if n
    )
    if not summary:
        summary = "<span style='background:#27ae60'>未发现问题</span>"

    finding_rows = "".join(_finding_row(f) for f in findings) or (
        "<tr><td colspan='10'>本次审计未发现需要关注的问题。</td></tr>"
    )

    check_rows = "".join(
        "<tr>"
        f"<td>{_esc(c.get('check'))}</td>"
        f"<td>{_esc(c.get('status'))}</td>"
        f"<td>{_esc(c.get('duration_ms', '-'))} ms</td>"
        f"<td>{_esc(c.get('findings', '-'))}</td>"
        f"<td>{_esc(c.get('reason') or '-')}</td>"
        "</tr>"
        for c in checks
    )

    completed = getattr(audit, "completed_at", None)
    pdf_hash = getattr(audit, "source_pdf_hash", "") or "-"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>实验审计报告 — {_esc(paper_title or getattr(audit, "paper_id", ""))}</title>
<style>{_CSS}</style>
</head>
<body>
<h1>论文实验审计报告</h1>
<div class="meta">
  论文：{_esc(paper_title or getattr(audit, "paper_id", "-"))}<br>
  审计 ID：{_esc(getattr(audit, "id", "-"))} ｜ 状态：{_esc(getattr(audit, "status", "-"))}
  ｜ 完成时间：{_esc(completed or "-")}<br>
  PDF SHA-256：<span class="code">{_esc(pdf_hash)}</span> ｜ 报告生成：{generated_at}
</div>
<div class="summary">{summary}</div>

<h2>审计发现（{len(findings)} 条）</h2>
<table>
<tr><th>编号</th><th>严重度</th><th>类型 / 标题</th><th>页码</th><th>论文声称</th>
<th>审计计算</th><th>检测方法</th><th>证据</th><th>良性解释</th><th>需人工复核</th></tr>
{finding_rows}
</table>

<h2>检测项运行摘要</h2>
<table class="checks">
<tr><th>检测项</th><th>状态</th><th>耗时</th><th>发现数</th><th>备注</th></tr>
{check_rows}
</table>

<div class="note">
本报告由确定性规则与本地模型自动生成，所有 Finding 均附「良性解释」，
仅作为审稿辅助线索，不构成对论文作者的任何指控；
标记「需人工复核」的条目必须由人工终审后方可引用。
</div>
</body>
</html>"""


def finding_type_catalog() -> list[dict]:
    """返回 10 种 Finding 类型目录（供前端说明页使用）。"""
    return [{"type": t, **meta} for t, meta in FINDING_TYPES.items()]
