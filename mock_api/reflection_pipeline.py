"""感悟报告分析整合流水线。

把分散的能力聚合成一次完整分析（对应规格 reflection_analysis_spec.html）：

    docx ──parse──▶ 学号/姓名 + 原论文题目 + 四段
         ──bind──▶ 自动匹配原论文 paper_id
         ──fulltext──▶ 构建论文全文补充（全局摘要 + 关键句）
         ──fidelity──▶ 报告↔原论文 忠实度（reflection_fidelity）
         ──4维──▶ 复用现有 ReflectionReviewer（depth_eval_reflection）
         ──fusion──▶ 5 维均权 + verdict 扩展
         ──▶ 统一结果 dict（供端点返回 / 批阅列表读取）

设计：
    - 与 depth_eval_reflection.py 解耦：仅调用其 ReflectionReviewer.review
    - LLM 不可用时 4 维降级为结构启发式，fidelity 仍可算（退化模式）
    - 不强制写 DB；写库逻辑由调用端点负责
    - ADR-014 P9：论文全文补充（build_fulltext_context）接入感悟报告评审，
      让 LLM 看到的不只是论文前 N 字，而是全文摘要 + 关键句的丰富上下文。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .ai_likelihood import compute_ai_likelihood
from .depth_eval_v4 import _statistical_plausibility_check
from .reflection_binding import match_paper_by_title
from .reflection_calibration import apply_dim_offsets
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


def _get_paper_title_abstract(db, paper_id: str | None) -> str:
    """取绑定原论文的「标题 + 摘要」参考文本（供 LLM 判断理解准确性）。

    2026-08-12 实测：把论文全文/长补充喂给本机 9B 模型（Ornstein）会压扁打分
    区分度（11 篇跨度 0.112 vs 标题+摘要 0.425，MAE 也更差）——模型把「论文质量」
    和「报告质量」混为一谈。改为只注入标题+摘要：保留 UA 校验锚点（MAE 0.046），
    又不触发论文美化效应。忠实度（fidelity/coverage）仍由嵌入层用全文独立保证。

    fail-open：查询失败返回 ''（= 不注入论文参考，与旧行为一致）。
    """
    if not paper_id:
        return ""
    try:
        from .models import Paper as PaperORM

        p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
        if p is None:
            return ""
        title = (getattr(p, "title", "") or "").strip()
        abstract = (getattr(p, "abstract", "") or "").strip()
        parts = [
            x
            for x in (
                f"论文标题：{title}" if title else "",
                f"论文摘要：{abstract}" if abstract else "",
            )
            if x
        ]
        return "\n\n".join(parts)
    except Exception:  # noqa: BLE001 - fail-open，绝不影响主评审流程
        return ""


def _lookup_source_paper_id_by_docx(db, path: str) -> str | None:
    """通过报告 docx 路径反查数据库里已绑定的源论文 ID。"""


def _load_figure_series(
    db, paper_id: str | None
) -> list[tuple[str, list[tuple[str, list[float]]]]]:
    """从数据库加载 PaperFigure 数据，转换为 (figure_id, series) 格式。

    用于 figure 级别的造假检测（跨表复制、末位偏好、精度一致、互补等）。
    失败时返回空列表（fail-open）。
    """
    if not paper_id:
        return []
    try:
        from .experiment_audit.figures import _figure_id
        from .experiment_audit.table_numbers import merge_transcripts, transcribe_multi
        from .models import PaperFigure
        from .pdf_parser import _get_uploads_dir

        figs = (
            db.query(PaperFigure)
            .filter(PaperFigure.paper_id == paper_id)
            .order_by(PaperFigure.page, PaperFigure.figure_index)
            .all()
        )
        if not figs:
            return []

        uploads = _get_uploads_dir() / "figures" / paper_id
        result = []
        for fig in figs:
            img_path = uploads / Path(fig.figure_path).name if fig.figure_path else None
            if not (img_path and img_path.exists()):
                continue
            transcripts = transcribe_multi(str(img_path), fig.caption_text or "")
            if not transcripts:
                continue
            series = merge_transcripts(transcripts)
            if series:
                result.append((_figure_id(fig), series))
        return result
    except Exception:  # noqa: BLE001 - figure 加载失败不影响主流程
        return []


def _run_figure_fraud_detection(
    fig_series: list[tuple[str, list[tuple[str, list[float]]]]],
) -> list[str]:
    """对 figure 级别数据运行造假检测，返回警告字符串列表。

    覆盖：跨表完全复制、末位偏好、精度一致、互补/高度相似。
    """
    if not fig_series:
        return []

    from .experiment_audit.table_numbers import (
        detect_complementary_groups,
        detect_cross_figure_duplicates,
        detect_decimal_precision_consistency,
        detect_digit_preference,
    )

    flags: list[str] = []

    # 跨表完全复制检测
    if len(fig_series) >= 2:
        cross = detect_cross_figure_duplicates(fig_series)
        for cf in cross:
            flags.append(
                f"[跨表复制] {cf['figure_id']} 与 {cf['other_figure_id']} 共享 "
                f"{cf['shared_count']} 个完全相同的数值（{cf['overlap_pct']}%）"
            )

    # 对每个 figure 做单图内检测
    for fig_id, series in fig_series:
        all_vals = [v for _, vals in series for v in vals]

        # 末位偏好
        pref = detect_digit_preference(all_vals)
        if pref:
            flags.append(f"[{fig_id}] {pref}")

        # 精度一致
        prec = detect_decimal_precision_consistency(series)
        if prec:
            flags.append(f"[{fig_id}] {prec}")

        # 互补/高度相似
        comp = detect_complementary_groups(series)
        for c in comp:
            flags.append(f"[{fig_id}] {c}")

    return flags


def _lookup_source_paper_id_by_docx(db, path: str) -> str | None:
    """通过报告 docx 路径反查数据库里已绑定的源论文 ID。

    上传时报告以 Paper 记录入库，reflection_docx_path 指向 docx 文件、
    source_paper_id 指向原论文。若调用方未显式传 source_paper_id，
    先按文件路径反查（比标题匹配可靠得多——很多报告没有「论文题目：」
    标签，标题匹配会拿正文第一句去匹配而失败）。

    fail-open：查询异常返回 None，不影响主流程（回退到标题匹配）。
    """
    if not path:
        return None
    try:
        from .models import Paper as PaperORM

        def _real_sid(row) -> str | None:
            """只认真实字符串的 source_paper_id（测试 Mock 的任意属性会自建
            truthy Mock，不能当绑定用）。"""
            sid = getattr(row, "source_paper_id", None)
            return sid if isinstance(sid, str) and sid else None

        row = db.query(PaperORM).filter(PaperORM.reflection_docx_path == str(path)).first()
        if row is not None:
            sid = _real_sid(row)
            if sid:
                return sid
        # 路径可能存在差异（绝对/相对），再按文件名尾部模糊匹配兜底
        import os as _os

        fname = _os.path.basename(str(path))
        if fname:
            rows = db.query(PaperORM).filter(PaperORM.reflection_docx_path.like(f"%{fname}")).all()
            for row in rows:
                sid = _real_sid(row)
                if sid:
                    return sid
        # 最终兜底：按文件名中的学号匹配报告记录。上传目录文件名带
        # reflection_NNN_ 前缀（reflection_004_999900000005-学生03.docx），
        # 而 DB 存的 docx_path 是原始路径（...\999900000005-学生03.docx）——
        # basename 匹配不上，但两者都含学号，报告 id = reflection_<学号>。
        import re as _re

        sid_match = _re.search(r"(20\d{10})", str(path))
        if sid_match:
            sid = sid_match.group(1)
            row = db.query(PaperORM).filter(PaperORM.id == f"reflection_{sid}").first()
            if row is not None:
                return _real_sid(row)
        return None
    except Exception:  # noqa: BLE001 - reflection pipeline - 子任务异常隔离
        return None


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


def _safe_dict(value):
    """安全透传 dict 字段（缺失/非 dict 一律返回 {}）。"""
    return dict(value) if isinstance(value, dict) else {}


def analyze_reflection_file(
    path: str,
    db,
    source_paper_id: str | None = None,
) -> dict:
    """对一篇感悟报告 docx 做完整分析，返回统一结果 dict。"""
    with open(path, "rb") as f:
        parse = parse_docx_from_bytes(f.read(), filename=path)

    # 1. 自动绑定原论文（表单 sourcePaperId 优先，否则按 docx 路径反查 DB，
    #    最后才回退到题目匹配——很多报告没有「论文题目：」标签，标题匹配会
    #    拿正文第一句去匹配而失败，但上传时已通过 source_paper_id 绑定过）
    bound = (
        source_paper_id
        or _lookup_source_paper_id_by_docx(db, path)
        or (match_paper_by_title(parse.paper_title, db) if parse.paper_title else None)
    )
    full, emb = _get_paper_text_emb(db, bound)

    # 1.5 figure 级别造假检测（跨表复制、末位偏好、精度一致、互补/高度相似）
    #    需要 VLM 转写，仅在 vision_http_url 配置时执行；失败静默跳过。
    _fig_series = _load_figure_series(db, bound)
    _figure_flags = _run_figure_fraud_detection(_fig_series)

    # 2. 忠实度层（报告→论文 有据性，防编造）
    fid = compute_fidelity(parse.sections, full, emb)

    # 2.1 反向覆盖度（论文→报告，衡量感悟报告对原论文核心要点的覆盖 → 正确性）
    cov = compute_coverage(parse.sections, full)

    # 2.5 AI 生成疑似度（事后检测，仅供参考 / 不进入 scores/verdict）
    ai = compute_ai_likelihood(parse.raw_text)

    # 2.6 引用真值校验（ADR-014 P4，解决 W8：感悟报告同样可能编造参考文献）
    # 默认自动执行 offline 模式（本地 DOI 抽取 + 引用编号一致性）；
    # PAPERFORGE_CITATION_VERIFY=0 可关闭；=1 开启 Crossref 在线核验。
    # 结果会在第 5 步被 verdict 硬校验层消费：fabricated_suspected → rewrite_required
    # （一票否决级，对齐论文侧 FATAL_VETO）；inconsistent → needs_evidence。
    # 校验本身 fail-open（网络/限流异常降级为 unknown，绝不误杀报告）。
    citation_integrity: dict = {}
    _cv_flag = os.environ.get("PAPERFORGE_CITATION_VERIFY")
    if _cv_flag is not None and _cv_flag.strip() == "0":
        citation_integrity = {}
    else:
        try:
            from .integrity.citation_verifier import assess_citation_integrity

            citation_integrity = assess_citation_integrity(
                parse.raw_text,
                verify_online=_cv_flag is not None
                and _cv_flag.strip().lower() in ("1", "true", "on", "yes"),
            )
        except Exception:  # noqa: BLE001 - 引用校验异常隔离，不影响主流程
            citation_integrity = {}

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
        "paper_preview_chars": None,
        "evidence_rejections": {},
        "score_uncertainty": {},
    }
    # 开发自检开关：PAPERFORGE_BENCH_NO_LLM=1 时跳过 LLM，4 维用结构启发式（快速跑 fidelity）
    reviewer_verdict: str | None = None  # 评审器最终 verdict（含 crossval 加分），供 verdict 基线

    # ADR-014 P9 停用（2026-08-12 实测）：原论文全文补充（正文原文块 + 关键句，
    # ≈5000 字）与全文预览一样会压扁本机 9B 模型的打分区分度——模型拿到大段论文
    # 正文后把「论文质量」混为「报告质量」，11 篇同批实验：全文+补充跨度 0.112、
    # 标题+摘要跨度 0.425、无论文 0.300，且标题+摘要的 UA 校验 MAE 仅 0.046。
    # 忠实度（fidelity/coverage）由嵌入层用全文独立保证，不受影响。
    paper_supplement = ""

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
                # 2026-08-12：不再喂论文全文，只喂「标题+摘要」作为 UA 校验锚点
                # （见 _get_paper_title_abstract）；fidelity/coverage 仍由嵌入层用全文独立保证。
                paper_text=_get_paper_title_abstract(db, bound),
                paper_supplement=paper_supplement,
            )
            four = res.scores
            # verdict 是结果对象的独立字段（不在 scores dict 里），单独取出作基线；
            # 否则 four.get("verdict") 永远取到默认值，inconsistent 降级分支会失活。
            # 防御：只接受真实 str（测试替身 Mock 的任意属性会自建 truthy Mock）。
            _rv = getattr(res, "verdict", None)
            reviewer_verdict = _rv if isinstance(_rv, str) and _rv else None
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
                    paper_preview_chars=_safe_int(
                        getattr(res, "paper_preview_chars", None), default=None
                    ),
                    evidence_rejections=_safe_int_map(getattr(res, "evidence_rejections", None)),
                    # ADR-014 P2：不确定门控报告（bootstrap CI），透传给上层供复核/展示。
                    score_uncertainty=_safe_dict(getattr(res, "score_uncertainty", None)),
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
    # 确定性校准层：对 4 维 LLM 分逐维减去金标拟合的系统偏差（II +0.074 等）。
    # 只作用于 LLM 分、绝不触碰 fidelity/coverage（向量层独立保证）；
    # llm_failed 时 four 全为 None，apply_dim_offsets 原样保留 None 不造分。
    four_cal = apply_dim_offsets(four)
    scores = {
        "understanding_accuracy": four_cal.get("understanding_accuracy"),
        "analysis_depth": four_cal.get("analysis_depth"),
        "innovative_insights": four_cal.get("innovative_insights"),
        "evidence_support": four_cal.get("evidence_support"),
        "fidelity": fid.fidelity if fid.fidelity is not None else 0.0,
        "coverage": cov.coverage if cov.coverage is not None else 0.0,
    }
    copy_ratio = cov.copy_ratio if cov.copy_ratio is not None else (fid.copy_ratio or 0.0)
    # ── 照抄封杀：copy_ratio ≥ COPY_RATIO_FAIL → fidelity/coverage 向量分全部置零 ──
    # 照抄报告在向量层天然高分（每句都能在论文里找到高余弦匹配），占权重 40%。
    # 此前只联动 verdict 不改原始分，造成「verdict=rewrite_required 但 coverage=0.85」
    # 的分叉——老师一算就能发现照抄者仍有 40% 的向量保底分。
    # 现直接清零：照抄 ≠ 忠实，抄来的句子不算「覆盖了核心要点」。
    copy_crushed = not llm_failed and copy_ratio is not None and copy_ratio >= COPY_RATIO_FAIL
    if copy_crushed:
        scores["fidelity"] = 0.0
        scores["coverage"] = 0.0
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
    #    - 引用真值（ADR-014 P4，仅 PAPERFORGE_CITATION_VERIFY 开启时非空）：
    #      fabricated_suspected（Crossref 查无 DOI，疑似编造参考文献）→ rewrite_required
    #      （一票否决级，对齐论文侧 FATAL_VETO）；inconsistent（文中引用 vs 参考文献
    #      列表不一致，漏引/虚列）→ 至少 needs_evidence
    #    - copy_ratio 过高（大量照抄论文原文）→ rewrite_required
    #    - fidelity 过低 + 非绑定错配（报告编造论点）→ rewrite_required
    #    - coverage 过低（论文核心没讲到，正确性不足）→ needs_depth
    #    - 绑定错配：fidelity 极低且无照抄（论文绑错）→ 仅 advisory，不覆盖 verdict
    verdict = (
        "llm_failed" if llm_failed else (reviewer_verdict or four.get("verdict", "needs_evidence"))
    )
    citation_override_reason = ""
    if copy_crushed:
        citation_override_reason = (
            f"照抄封杀: copy_ratio={copy_ratio:.2%} ≥ {COPY_RATIO_FAIL:.0%}，"
            f"向量层 fidelity/coverage 均置零"
        )
    if not llm_failed and citation_integrity:
        _cv_integrity_flag = citation_integrity.get("integrity_flag")
        if _cv_integrity_flag == "fabricated_suspected":
            verdict = "rewrite_required"
            citation_override_reason = (
                "引用真值校验: 疑似编造参考文献（Crossref 查无 DOI）→ verdict=rewrite_required"
            )
        elif _cv_integrity_flag == "inconsistent" and verdict not in (
            "rewrite_required",
            "needs_depth",
            "needs_evidence",
        ):
            verdict = "needs_evidence"
            citation_override_reason = (
                "引用真值校验: 文中引用与参考文献列表不一致 → verdict=needs_evidence"
            )
    # 照抄/编造/覆盖度硬规则：仅在引用否决未把 verdict 锁为 rewrite_required 时评估
    # （弱规则不得覆盖强裁决）。
    if not llm_failed and verdict != "rewrite_required":
        if copy_ratio is not None and copy_ratio >= COPY_RATIO_FAIL:
            verdict = "rewrite_required"
        elif fid.fidelity is not None and fid.fidelity < FIDELITY_FAIL:
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
        elif cov.coverage is not None and cov.coverage < COVERAGE_FAIL:
            verdict = "needs_depth"

    # 5.5 分数/verdict 联动：照抄或编造判定后，总分封顶（防「分数虚高但 verdict 改写」分叉）
    #     — 权重重构后 coverage 权重 0.35，照抄报告的 coverage 可能很高，
    #       若不做封顶，总分仍可能不低，与 verdict=rewrite_required 自相矛盾。
    if verdict == "rewrite_required" and avg is not None:
        avg = min(avg, REWRITE_AVG_CAP)

    # 5.6 统计红旗后置扣分（对齐论文侧 depth_eval_v4.py 的 stat_penalty 逻辑）
    #    报告原文 + 原论文 + figure 级别的统计红旗信号均参与扣分，最高 0.15。
    #    等差/重复/恒定偏移/Benford 等零 LLM 信号，检测学生引用的数据是否可信。
    _report_flags = _statistical_plausibility_check(parse.raw_text)
    _paper_flags = _statistical_plausibility_check(full or "")
    _all_stat_flags = _report_flags + _paper_flags + _figure_flags
    stat_penalty = 0.0
    for _f in _all_stat_flags:
        if _f.startswith("[std过低]") or _f.startswith("[p值不可能]"):
            stat_penalty += 0.05
        elif (
            _f.startswith("[表格文本矛盾]")
            or _f.startswith("[消融数字过整]")
            or any(_f.startswith(p) for p in ("[等差]", "[重复]", "[恒定偏移]", "[Benford]"))
        ):
            stat_penalty += 0.03
        # figure 级别造假信号（跨表复制、末位偏好、精度一致、互补/高度相似）
        elif any(_f.startswith(p) for p in ("[跨表复制]", "[Figure", "[")):
            # 末位偏好/精度一致/互补等信号来自 _run_figure_fraud_detection
            if (
                "末位偏好" in _f
                or "精度一致" in _f
                or "互补" in _f
                or "高度相似" in _f
                or "跨表复制" in _f
            ):
                stat_penalty += 0.03
    stat_penalty = min(stat_penalty, 0.15)
    if stat_penalty > 0 and avg is not None:
        avg = max(0.0, avg - stat_penalty)

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
        # 本次评审实际注入 prompt 的原论文预览字数（动态预算后；LLM 失败时可能为 None）
        "paper_preview_chars": diag["paper_preview_chars"],
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
        "copy_crushed": copy_crushed,
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
        # ADR-014 P2：分数不确定性（bootstrap 95% CI；仅 PAPERFORGE_UNCERTAINTY_GATE 开启时非空）
        "score_uncertainty": diag["score_uncertainty"],
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
        # —— 引用真值校验（ADR-014 P4，fabricated 已进 verdict 硬校验层）——
        "citation_integrity": citation_integrity,
        "citation_override_reason": citation_override_reason,
        # —— 统计合理性检测（零 LLM，复用论文侧检测，对报告同样生效）——
        "statistical_flags": _report_flags,
        # —— 论文原文统计检测（检测学生引用的论文是否有数据篡改信号）——
        "paper_statistical_flags": _paper_flags,
        # —— Figure 级别造假检测（跨表复制、末位偏好、精度一致、互补/高度相似）——
        "figure_fraud_flags": _figure_flags,
        # —— 统计红旗扣分（对齐论文侧 stat_penalty，最高 0.15）——
        "stat_penalty": round(stat_penalty, 4),
    }
