"""感悟报告分析整合流水线。

把分散的能力聚合成一次完整分析（对应规格 reflection_analysis_spec.html）：

    docx ──parse──▶ 学号/姓名 + 原论文题目 + 四段
         ──bind──▶ 自动匹配原论文 paper_id
         ──fidelity──▶ 报告↔原论文 忠实度（reflection_fidelity）
         ──4维──▶ 复用现有 ReflectionReviewer（depth_eval_reflection）
         ──fusion──▶ 5 维均权 + verdict 扩展
         ──▶ 统一结果 dict（供端点返回 / 批阅列表读取）

设计：
    - 与 depth_eval_reflection.py 解耦：仅调用其 ReflectionReviewer.review
    - LLM 不可用时 4 维降级为结构启发式，fidelity 仍可算（退化模式）
    - 不强制写 DB；写库逻辑由调用端点负责
"""

from __future__ import annotations

import hashlib
import os

from .ai_likelihood import compute_ai_likelihood
from .reflection_binding import match_paper_by_title
from .reflection_docx_parser import parse_docx_from_bytes
from .reflection_fidelity import (
    COPY_RATIO_FAIL,
    COVERAGE_FAIL,
    FIDELITY_FAIL,
    compute_coverage,
    compute_fidelity,
)

# 6 维权重（2026-08-04 控制变量实验校准，详见 deliverables/experiment_C_extension_report.md）
# 校准依据：41 篇人工基准 + 论文原文核验，60/40 随机划分 × 50 稳健性验证。
#   - 创新见解：唯一有真实排序能力的千问维度（ρ=0.537，std=0.118）→ 提权 0.35
#   - 覆盖度  ：向量层最可靠维度（ρ=0.270）→ 提权 0.35
#   - 分析深度：中等区分度（ρ=0.365）→ 保持 0.15
#   - 理解准确性/证据支撑：几乎全员满分（std≈0.01-0.02，区分度≈0）→ 降权至 0.05
#     （注意：verdict 层仅对 copy_ratio/fidelity/coverage 生效，理解/证据无独立否决；
#     二者区分度不足，降权后不再稀释总分，但低分也不会被门槛拦截）
#   - 忠实度  ：8-gram 照抄检测已由 verdict 层独立保证（FIDELITY_FAIL → rewrite_required），
#     计分权重降至 0.05，防编造不依赖其权重；rewrite_required 时 average 封顶 0.6 防分叉
# 效果：生产口径 ρ 0.422→0.476，实验 B 口径（千问读论文）ρ 0.412→0.577，无过拟合（划分验证一致）。
W = {
    "understanding_accuracy": 0.05,
    "analysis_depth": 0.15,
    "innovative_insights": 0.35,
    "evidence_support": 0.05,
    "fidelity": 0.05,  # 报告→论文 有据性（防编造，verdict 层独立保证）
    "coverage": 0.35,  # 论文→报告 覆盖度（防遗漏核心，正确性核心）
}

# rewrite_required（照抄/编造）时总分上限：verdict 改写后分数不高于此值
# （权重重构后 coverage 0.35，照抄报告 coverage 虚高，需与 verdict 联动封顶）
REWRITE_AVG_CAP = 0.60


def _get_paper_text_emb(db, paper_id: str | None):
    """取绑定原论文的全文与向量（用于 fidelity 比对）。"""
    full, emb = "", None
    if not paper_id:
        return full, emb
    try:
        from . import crud
        from .models import Paper as PaperORM

        p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
        if p is not None:
            full = getattr(p, "full_text", "") or ""
        try:
            emb = crud.get_paper_embedding(db, paper_id)
        except Exception:  # noqa: BLE001 - reflection pipeline - 子任务异常隔离
            emb = None
    except Exception:  # noqa: BLE001 - reflection pipeline - 子任务异常隔离
        pass
    return full, emb


def _heuristic_four(sections: dict) -> dict:
    """结构启发式：四段齐全度作为 4 维兜底分（LLM 不可用时的降级）。"""
    n = len([k for k in ("q", "tech", "exp", "reflection") if sections.get(k, "").strip()])
    base = n / 4
    return {
        "understanding_accuracy": base,
        "analysis_depth": base,
        "innovative_insights": base,
        "evidence_support": base,
        "average": base,
    }


# —— 诊断字段的严格取值 ——
# 只接受真实的数值/布尔；拿到别的东西（测试替身、字段缺失、上游改了类型）
# 一律退回默认值。诊断是辅助信息，宁可缺失也不能抛异常拖垮评分主链路。
def _safe_int(value, default=0):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return default


def _safe_bool(value, default=False):
    if isinstance(value, (bool, int, float)):
        return bool(value)
    return default


def _safe_str_list(value):
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value]
    return []


def _safe_int_map(value):
    if isinstance(value, dict):
        return {str(k): _safe_int(v) for k, v in value.items()}
    return {}


def analyze_reflection_file(
    path: str,
    db,
    source_paper_id: str | None = None,
) -> dict:
    """对一篇感悟报告 docx 做完整分析，返回统一结果 dict。"""
    with open(path, "rb") as f:
        parse = parse_docx_from_bytes(f.read(), filename=path)

    # 1. 自动绑定原论文（表单 sourcePaperId 优先，否则按题目匹配）
    bound = source_paper_id or (
        match_paper_by_title(parse.paper_title, db) if parse.paper_title else None
    )
    full, emb = _get_paper_text_emb(db, bound)

    # 2. 忠实度层（报告→论文 有据性，防编造）
    fid = compute_fidelity(parse.sections, full, emb)

    # 2.1 反向覆盖度（论文→报告，衡量感悟报告对原论文核心要点的覆盖 → 正确性）
    cov = compute_coverage(parse.sections, full)

    # 2.5 AI 生成疑似度（事后检测，仅供参考 / 不进入 scores/verdict）
    ai = compute_ai_likelihood(parse.raw_text)

    # 3. 现有 4 维（容错）
    four = None
    # 4 维评审的诊断信息（默认值对应「未跑 LLM」）。
    # 这些字段过去被丢弃，导致 CSV 里只剩一个孤零零的 0.3 分数，
    # 无法分辨 R1 是因为「LLM 超时」还是「学生报告真没证据」——
    # 2026-08-05 排查 19 篇污染数据时正是卡在这里。
    diag: dict = {
        "effective_evidence_count": None,
        "hardcoded_overrides": [],
        "parse_failed": False,
        "llm_calls": 0,
        "llm_empty": 0,
        "llm_failed": False,
        "four_truncated": False,
        "evidence_rejections": {},
    }
    # 开发自检开关：PAPERFORGE_BENCH_NO_LLM=1 时跳过 LLM，4 维用结构启发式（快速跑 fidelity）
    if os.environ.get("PAPERFORGE_BENCH_NO_LLM") == "1":
        four = _heuristic_four(parse.sections)
    else:
        try:
            from .depth_eval_reflection import ReflectionReviewer

            rid = f"report_{hashlib.sha1(parse.raw_text.encode()).hexdigest()[:12]}"
            res = ReflectionReviewer().review(
                rid,
                parse.paper_title or "报告",
                parse.raw_text,
                student_id=parse.student_id or "",
                paper_text=full,  # 原论文全文（可选注入 prompt，对照核验理解准确性）
            )
            four = res.scores
        except Exception:  # noqa: BLE001 - reflection pipeline - 子任务异常隔离
            four = None
            diag["llm_failed"] = True
        else:
            # 诊断字段单独提取：这里出任何岔子都只能让诊断降级，
            # 绝不能连累已经拿到的 four（早期版本混在一个 try 里，
            # reviewer 返回对象字段缺失就会把 4 维整体打回启发式）。
            try:
                diag.update(
                    effective_evidence_count=_safe_int(
                        getattr(res, "effective_evidence_count", None), default=None
                    ),
                    hardcoded_overrides=_safe_str_list(getattr(res, "hardcoded_overrides", None)),
                    parse_failed=_safe_bool(getattr(res, "parse_failed", False)),
                    llm_calls=_safe_int(getattr(res, "llm_calls", 0)),
                    llm_empty=_safe_int(getattr(res, "llm_empty", 0)),
                    four_truncated=_safe_bool(getattr(res, "truncated", False)),
                    evidence_rejections=_safe_int_map(getattr(res, "evidence_rejections", None)),
                )
                # LLM 故障判定：解析彻底失败，或任一次调用空返回（超时/连接失败）。
                # 此时四维分数是系统故障的产物，不代表报告质量，调用方须视为无效。
                diag["llm_failed"] = bool(diag["parse_failed"] or diag["llm_empty"])
            except Exception:  # noqa: BLE001 - 诊断降级，不影响评分
                pass
        if not four or "average" not in four:
            four = _heuristic_four(parse.sections)

    # 4. LLM 失败时不把启发式/默认分数伪装成真实的 LLM 评审结果。
    # 确定性 fidelity/coverage 仍保留，供排查和人工复核使用；4 维及其融合分数
    # 置空，调用方可依据 llm_failed 明确区分“系统故障”与“报告质量”。
    llm_failed = bool(diag["llm_failed"])
    if llm_failed:
        four = dict.fromkeys(
            (
                "understanding_accuracy",
                "analysis_depth",
                "innovative_insights",
                "evidence_support",
            ),
            None,
        )

    # 4. 6 维融合（论文↔报告交叉评价：fidelity + coverage）
    scores = {
        "understanding_accuracy": four.get("understanding_accuracy"),
        "analysis_depth": four.get("analysis_depth"),
        "innovative_insights": four.get("innovative_insights"),
        "evidence_support": four.get("evidence_support"),
        "fidelity": fid.fidelity if fid.fidelity is not None else 0.0,
        "coverage": cov.coverage if cov.coverage is not None else 0.0,
    }
    # LLM 失败时平均分也必须为空；否则确定性 fidelity/coverage 的部分加权平均
    # 会制造一个看似真实的总分，继续污染排名/诚信报告。
    if llm_failed:
        avg = None
    else:
        # 用显式权重 W 加权（正确性维度 coverage 权重最高），并对实际参与维度归一化
        weighted = {k: v for k, v in scores.items() if v is not None}
        w_sum = sum(W.get(k, 0.0) for k in weighted)
        if w_sum > 0:
            avg = sum(weighted[k] * W.get(k, 0.0) for k in weighted) / w_sum
        else:
            avg = sum(weighted.values()) / len(weighted) if weighted else 0.0

    # 5. verdict 扩展：
    #    - copy_ratio 过高（大量照抄论文原文）→ rewrite_required
    #    - fidelity 过低 + 非绑定错配（报告编造论点）→ rewrite_required
    #    - coverage 过低（论文核心没讲到，正确性不足）→ needs_depth
    #    - 绑定错配：fidelity 极低且无照抄（论文绑错）→ 仅 advisory，不覆盖 verdict
    verdict = "llm_failed" if llm_failed else four.get("verdict", "needs_evidence")
    copy_ratio = cov.copy_ratio if cov.copy_ratio is not None else (fid.copy_ratio or 0.0)
    if not llm_failed and copy_ratio is not None and copy_ratio >= COPY_RATIO_FAIL:
        verdict = "rewrite_required"
    elif not llm_failed and fid.fidelity is not None and fid.fidelity < FIDELITY_FAIL:
        # 绑定错配保护：fidelity 极低 + 照抄极少 + 无任何有效复述句
        # （报告与绑定论文零交集）→ 疑似论文绑错，仅 advisory，不强制 rewrite_required。
        # 真实编造报告通常仍会有零星句子与论文沾边（grounded_ratio > 0）→ 照常降级。
        mismatch = (
            fid.grounded_ratio is not None
            and fid.grounded_ratio == 0
            and copy_ratio is not None
            and copy_ratio < 0.05
        )
        if not mismatch:
            verdict = "rewrite_required"
    elif not llm_failed and cov.coverage is not None and cov.coverage < COVERAGE_FAIL:
        verdict = "needs_depth"

    # 5.5 分数/verdict 联动：照抄或编造判定后，总分封顶（防「分数虚高但 verdict 改写」分叉）
    #     — 权重重构后 coverage 权重 0.35，照抄报告的 coverage 可能很高，
    #       若不做封顶，总分仍可能不低，与 verdict=rewrite_required 自相矛盾。
    if verdict == "rewrite_required" and avg is not None:
        avg = min(avg, REWRITE_AVG_CAP)

    return {
        "student_id": parse.student_id,
        "student_name": parse.name,
        "paper_title": parse.paper_title,
        "paper_author": parse.paper_author,
        "paper_source": parse.paper_source,
        "bound_paper_id": bound,
        # 注意：不要把 raw_text 全文放进返回值——本 dict 会被整体写进
        # DepthReviewV4.reflection_result["analysis_v2"] 并原样返回给前端，
        # 每篇会平白多出约 10KB。只回字符数。
        "report_chars": len(parse.raw_text or ""),
        "paper_chars": len(full or ""),
        "scores": scores,
        "weights": dict(W),
        "average": round(avg, 4) if avg is not None else None,
        "verdict": verdict,
        "fidelity": fid.fidelity,
        "fidelity_status": fid.status,
        "fidelity_anchors": fid.anchors,
        "stray_claims": fid.stray_claims,
        "copy_ratio": copy_ratio,
        "copy_sentences": getattr(fid, "copy_sentences", []),
        # 修复：four 是 dict（res.scores），dict 上 getattr("truncated") 恒为 False，
        # 导致 truncated 永远报 False。改从 reviewer 结果透传的 diag 取。
        "truncated": diag["four_truncated"],
        # —— 4 维评审诊断（判定分数是否可信的依据）——
        "effective_evidence_count": diag["effective_evidence_count"],
        "hardcoded_overrides": diag["hardcoded_overrides"],
        "parse_failed": diag["parse_failed"],
        "llm_calls": diag["llm_calls"],
        "llm_empty": diag["llm_empty"],
        "llm_failed": diag["llm_failed"],
        "evidence_rejections": diag["evidence_rejections"],
        # —— 论文→报告 反向覆盖度（正确性核心指标）——
        "coverage": cov.coverage,
        "coverage_status": cov.status,
        "coverage_covered": cov.covered,
        "coverage_uncovered": cov.uncovered,
        "sections_present": list(parse.sections.keys()),
        # —— AI 生成疑似度（advisory only，绝不影响 scores/verdict）——
        "ai_likelihood": ai["ai_likelihood"],
        "ai_likelihood_tier": ai["tier"],
        "ai_likelihood_signals": ai["signals"],
        "ai_likelihood_note": ai["note"],
    }
