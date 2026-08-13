"""P0-11 图内数值造假指纹审计（Qwen3-VL 数值转写 + 统计指纹）。

链路：PaperFigure 图片 → Qwen3-VL 转写数值（结构化）→ 统计指纹：

- 等差数字 / 重复数字 / 恒定偏移 / Benford 偏离（复用 depth_eval_v4 的零 LLM helper）
- 跨组重复值（同一数值出现在两个不同组/行——复制粘贴痕迹）
- 末位数字偏好（人肉随机数指纹）

所有 Finding 类型 SUSPICIOUS_DATA_PATTERN（severity=high），needs_human_review=True：
这些是「造假线索」而非定罪证据，最终由人工核对原图/原始数据确认。
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import PaperFigure
from ..pdf_parser import _get_uploads_dir
from .figures import _figure_id
from .schemas import make_finding

logger = logging.getLogger(__name__)

_TABLE_NUMBERS_PROMPT = """图里可能包含一张数据表或带数值的统计图。请把图中可见的所有数值逐行转写出来，每行固定格式：

组别|数值

例如：WT|0.763641。要求：
- 组别用该行/该列最左侧或上方的文字标签（如 WT、H186R、对照组、组1 等）；
- 数值照抄，精确到原图小数位，不要四舍五入、不要加单位；
- 一个数值一行，按表格从上到下、从左到右的顺序；看不清的格子跳过不写。"""


def vision_available() -> bool:
    """视觉模型端点是否已配置（P0-11 的硬依赖，缺则显式 skipped）。"""
    try:
        from ..settings import get_settings

        return bool(get_settings().vision_http_url)
    except Exception:  # noqa: BLE001 - 探测失败视同不可用
        return False


def transcribe_figure_numbers(image_path: str, caption: str = "", timeout: int = 120) -> str:
    """Qwen3-VL 转写图内数值；不可用/失败返回空串（fail-open）。"""
    path = Path(image_path)
    if not path.exists():
        return ""
    try:
        import base64

        import requests

        from ..settings import get_settings
        from ..vram_scheduler import vram_guard

        vision_url = get_settings().vision_http_url
        if not vision_url:
            return ""
        img_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
        payload = {
            "model": "qwen3-vl",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {"type": "text", "text": _TABLE_NUMBERS_PROMPT},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 1024,
            "stream": False,
        }
        url = f"{vision_url.rstrip('/')}/v1/chat/completions"
        with vram_guard("vision"):
            resp = requests.post(url, json=payload, timeout=timeout)
            resp.raise_for_status()
            return (resp.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001 - VLM 不可用属正常降级
        logger.warning("[audit] 图内数值转写失败: %s", exc)
        return ""


# 负向 lookbehind 避免把 label 里的数字（如 H186R、Group1）误当数值
_NUM_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _to_float(s: str) -> float | None:
    """把一格文本解析为数值；非数值/NA/空白返回 None（label 里的数字不误认）。"""
    s = s.strip()
    if not s or s.lower() in {"na", "n/a", "?", "-", "—"}:
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def parse_number_series(transcript: str) -> list[tuple[str, list[float]]]:
    """把 VLM 转写文本解析为 [(label, [vals]), ...]，按 label 聚合同名行数值。

    兼容两种格式：
    - 「label|value」逐行（_TABLE_NUMBERS_PROMPT 约定）
    - markdown 表格行「| label | v1 | v2 | ... |」
    """
    series: dict[str, list[float]] = {}
    order: list[str] = []

    def add(label: str, vals: list[float]) -> None:
        label = label.strip()
        if not label or not vals:
            return
        if label not in series:
            series[label] = []
            order.append(label)
        series[label].extend(vals)

    for raw in transcript.splitlines():
        line = raw.strip().strip("`")
        if not line:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue
            vals = [v for v in (_to_float(c) for c in cells[1:]) if v is not None]
            add(cells[0], vals)
            continue

        # 「label|value」：先按分隔符拆，label 里含数字也不误读（如 H186R|0.8）
        m_sep = re.match(r"^(.*?)\s*[|\t,，:：]\s*(.+)$", line)
        if m_sep:
            label, rest = m_sep.group(1), m_sep.group(2)
            vals = [float(x.group(0)) for x in _NUM_RE.finditer(rest)]
            add(label, vals)
            continue

        # 无分隔符：按空白切，第一个数值出现之前的部分为 label
        label_parts: list[str] = []
        vals: list[float] = []
        for tok in line.split():
            v = _to_float(tok)
            if v is not None:
                vals.append(v)
            elif not vals:
                label_parts.append(tok)
        add(" ".join(label_parts), vals)

    return [(l, series[l]) for l in order]


def detect_cross_group_duplicates(series: list[tuple[str, list[float]]]) -> list[str]:
    """同一数值出现在 ≥2 个不同组 → 跨组复制粘贴痕迹。"""
    owners: dict[str, list[str]] = defaultdict(list)
    display: dict[str, str] = {}
    for label, vals in series:
        for v in vals:
            key = f"{v:.6f}"
            owners[key].append(label)
            display.setdefault(key, str(v))
    flags: list[str] = []
    for key, labels in owners.items():
        uniq = list(dict.fromkeys(labels))
        if len(uniq) >= 2:
            flags.append(
                f"[跨组重复] 数值 {display[key]} 同时出现在 {len(uniq)} 个不同组"
                f"（{'、'.join(uniq)}），不同组精确到 6 位小数完全相同，疑似复制粘贴"
            )
    return flags


def detect_digit_preference(values: list[float]) -> str | None:
    """末位数字分布偏离均匀（卡方 p<0.05，n≥8）→ 人肉随机数指纹。"""
    last = [str(v).split(".")[1][-1] for v in values if "." in str(v) and str(v).split(".")[1]]
    n = len(last)
    if n < 8:
        return None
    obs = Counter(last)
    exp = n / 10.0
    chi2 = sum(((obs.get(d, 0) - exp) ** 2) / exp for d in "0123456789")
    if chi2 <= 16.92:  # 9 自由度，p=0.05 临界值
        return None
    top = max("0123456789", key=lambda d: obs.get(d, 0))
    return (
        f"[末位偏好] {n} 个数值中 {obs.get(top, 0)} 个末位为 {top}"
        f"（期望≈{exp:.1f}，χ²={chi2:.1f}，p<0.05），"
        f"末位分布严重不均匀，人手编造「随机数」的典型指纹"
    )


def _decimal_places(v: float) -> int:
    """返回浮点数的小数位数（字符串表示）。"""
    s = f"{v:.10f}".rstrip("0")  # 去掉末尾 0 但保留至少 1 位
    if "." not in s:
        return 0
    return len(s.split(".")[1])


def detect_decimal_precision_consistency(
    series: list[tuple[str, list[float]]],
) -> str | None:
    """同一组内多个值的小数精度完全一致 → 人造数据指纹。

    真实测量值的精度会因仪器/读数/四舍五入而不同；独立重复实验的比值
    精度不可能完全一致（如 3 次实验的 A/C 比值全部是 7 位小数）。
    命中条件：≥3 个值全部精度相同，且精度 ≥5 位小数。
    """
    for label, vals in series:
        if len(vals) < 3:
            continue
        precisions = [_decimal_places(v) for v in vals]
        if len(set(precisions)) == 1 and precisions[0] >= 5:
            return (
                f"[精度一致] {label} 的 {len(vals)} 个数值全部为 {precisions[0]} 位小数，"
                f"独立实验的精度不可能完全一致，疑似从同一数据源复制"
            )
    return None


def detect_complementary_groups(
    series: list[tuple[str, list[float]]],
) -> list[str]:
    """检测两组数值是否互补（和为常数）或数值高度相似。

    真实实验中，两个不同处理组的数值之和不会恰好是常数（如 100）；
    如果 WT 和 M 的每对数值之和都恰好是同一个数，说明数据是人为构造的。
    """
    flags: list[str] = []
    for i in range(len(series)):
        la, va = series[i]
        for j in range(i + 1, len(series)):
            lb, vb = series[j]
            if len(va) != len(vb) or len(va) < 3:
                continue
            # 检查是否互补（和为常数，容忍 0.01 误差）
            sums = [a + b for a, b in zip(va, vb)]
            mean_sum = sum(sums) / len(sums)
            if all(abs(s - mean_sum) < 0.01 for s in sums):
                flags.append(
                    f"[互补] {la} 与 {lb} 的 {len(va)} 对数值之和全部为 {mean_sum:.2f}，"
                    f"不同组数值不可能完全互补，疑似人为构造"
                )
            # 检查数值高度相似（相对差异 <5%）
            if len(va) == len(vb):
                diffs = [abs(a - b) / max(abs(a), abs(b), 1e-10) for a, b in zip(va, vb)]
                mean_diff = sum(diffs) / len(diffs)
                if mean_diff < 0.05 and len(va) >= 3:
                    flags.append(
                        f"[高度相似] {la} 与 {lb} 的 {len(va)} 对数值平均相对差异仅 {mean_diff * 100:.1f}%，"
                        f"不同处理组的数值不可能如此接近"
                    )
    return flags


def detect_cross_figure_duplicates(
    fig_data: list[tuple[str, list[tuple[str, list[float]]]]],
) -> list[dict]:
    """跨表完全复制检测：不同 PaperFigure 之间共享完全相同的数值。

    对比策略：用 6 位精度将所有数值哈希化，统计每个数值出现在哪些 figure 中。
    出现在 ≥2 个不同 figure 的数值是铁证——不同实验条件的数据不可能完全一致。

    Args:
        fig_data: [(figure_id, series), ...] 每张 figure 的转写结果

    Returns:
        每个命中的 figure 产出一个 dict，包含 figure_id、重复数值列表、来源 figure 信息
    """
    if len(fig_data) < 2:
        return []

    # 数值 → 出现在哪些 figure 中
    val_figures: dict[str, list[str]] = defaultdict(list)
    val_display: dict[str, str] = {}
    # 每个 figure 的所有数值（6位精度 key）
    fig_val_keys: dict[str, set[str]] = defaultdict(set)

    for fig_id, series in fig_data:
        for _, vals in series:
            for v in vals:
                key = f"{v:.6f}"
                val_figures[key].append(fig_id)
                val_display.setdefault(key, str(v))
                fig_val_keys[fig_id].add(key)

    # 找出在 ≥2 个 figure 中共享的数值
    shared_keys = {key for key, figs in val_figures.items() if len(set(figs)) >= 2}
    if not shared_keys:
        return []

    # 计算每对 figure 之间的共享数值数量
    fig_ids = [fid for fid, _ in fig_data]
    pair_details: list[tuple[str, str, set[str]]] = []
    for i in range(len(fig_ids)):
        for j in range(i + 1, len(fig_ids)):
            fid_a, fid_b = fig_ids[i], fig_ids[j]
            shared = fig_val_keys[fid_a] & fig_val_keys[fid_b] & shared_keys
            if shared:
                pair_details.append((fid_a, fid_b, shared))

    if not pair_details:
        return []

    # 为每个涉及的 figure 生成 flag（避免重复）
    results: list[dict] = []
    flagged_figs: set[str] = set()
    for fid_a, fid_b, shared in pair_details:
        n = len(shared)
        sample = [val_display[k] for k in sorted(shared)[:3]]

        for fid in (fid_a, fid_b):
            if fid in flagged_figs:
                continue
            flagged_figs.add(fid)
            other_fid = fid_b if fid == fid_a else fid_a
            total_fid = len(fig_val_keys[fid])
            overlap_pct = n / total_fid * 100 if total_fid else 0
            results.append(
                {
                    "figure_id": fid,
                    "other_figure_id": other_fid,
                    "shared_count": n,
                    "overlap_pct": round(overlap_pct, 1),
                    "sample_values": sample,
                }
            )
    return results


def transcribe_multi(
    image_path: str, caption: str = "", n_runs: int = 3, timeout: int = 120
) -> list[str]:
    """对同一张图跑 n_runs 次转写，返回所有原始文本列表。

    多遍转写取多数可抵消 VLM 的随机性标签配对噪声，提高跨组重复检测的稳定性。
    """
    transcripts: list[str] = []
    for i in range(n_runs):
        logger.debug("[audit] 多遍转写 %d/%d: %s", i + 1, n_runs, image_path)
        t = transcribe_figure_numbers(image_path, caption, timeout=timeout)
        if t:
            transcripts.append(t)
    return transcripts


def merge_transcripts(transcripts: list[str]) -> list[tuple[str, list[float]]]:
    """合并多次转写结果，按组别聚合数值并取众数（多数投票）。

    核心思想：对每个 label，收集所有遍出现过的值，用 6 位精度做众数投票。
    某个值在至少 ceil(N/2) 遍中出现才保留，这样可消除 VLM 单遍转写时
    因标签配对不稳导致的跨组重复误判。
    """
    if not transcripts:
        return []
    if len(transcripts) == 1:
        return parse_number_series(transcripts[0])

    # 收集所有转写的 series
    all_series: list[list[tuple[str, list[float]]]] = []
    for t in transcripts:
        s = parse_number_series(t)
        if s:
            all_series.append(s)

    if not all_series:
        return []
    if len(all_series) == 1:
        return all_series[0]

    # 按 label 聚合所有遍的值
    label_all_vals: dict[str, list[list[float]]] = defaultdict(list)
    for series in all_series:
        for label, vals in series:
            label_all_vals[label].append(vals)

    # 对每个 label，用值本身做众数投票
    threshold = len(all_series) // 2 + 1  # 至少出现这么多遍才保留
    merged: list[tuple[str, list[float]]] = []
    for label, val_lists in label_all_vals.items():
        if len(val_lists) == 1:
            merged.append((label, val_lists[0]))
            continue
        # 统计每个值（6位精度）在多少遍中出现
        value_presence: dict[str, int] = Counter()
        value_display: dict[str, float] = {}
        # 保持第一遍的出现顺序
        first_seen_order: list[str] = []
        for vl in val_lists:
            seen_in_this_run: set[str] = set()
            for v in vl:
                key = f"{v:.6f}"
                if key not in seen_in_this_run:
                    seen_in_this_run.add(key)
                    value_presence[key] += 1
                    value_display.setdefault(key, v)
                    if key not in first_seen_order:
                        first_seen_order.append(key)
        # 取众数（至少 threshold 遍出现）
        result = [value_display[k] for k in first_seen_order if value_presence[k] >= threshold]
        if result:
            merged.append((label, result))
    return merged


def check_figure_number_patterns(
    db: Session, paper_id: str, *, allow_vlm: bool = True
) -> list[dict]:
    """P0-11 入口：对论文所有 PaperFigure 做「图内数值 → 统计指纹」审计。

    无 figure 记录时返回空（上游 figure 抽取未完成，不误报）。

    检测逻辑：
    1. 对每张图跑多遍转写取多数（默认 3 遍），消除标签噪声
    2. 跨表完全复制检测（不同 figure 共享相同数值 → 铁证）
    3. 单图内统计指纹（等差/重复/恒定偏移/Benford/跨组重复/末位偏好）
    """
    figs = (
        db.query(PaperFigure)
        .filter(PaperFigure.paper_id == paper_id)
        .order_by(PaperFigure.page, PaperFigure.figure_index)
        .all()
    )
    if not figs:
        return []

    uploads = _get_uploads_dir() / "figures" / paper_id
    findings: list[dict] = []

    # 第一轮：收集所有 figure 的转写结果
    fig_data: list[tuple[str, list[tuple[str, list[float]]], PaperFigure, list[str]]] = []
    for fig in figs:
        img_path = uploads / Path(fig.figure_path).name if fig.figure_path else None
        if not (img_path and img_path.exists()):
            continue
        if not allow_vlm:
            continue

        transcripts = transcribe_multi(str(img_path), fig.caption_text or "")
        if not transcripts:
            continue
        series = merge_transcripts(transcripts)
        if not series:
            continue

        fid = _figure_id(fig)
        fig_data.append((fid, series, fig, transcripts))

    if not fig_data:
        return []

    # 跨表完全复制检测（铁证）
    if len(fig_data) >= 2:
        cross_flags = detect_cross_figure_duplicates(
            [(fid, series) for fid, series, _, _ in fig_data]
        )
        for cf in cross_flags:
            fid = cf["figure_id"]
            other = cf["other_figure_id"]
            n = cf["shared_count"]
            pct = cf["overlap_pct"]
            sample = "、".join(cf["sample_values"][:3])
            fig_obj = next((f for f_id, _, f, _ in fig_data if f_id == fid), None)
            if fig_obj is None:
                continue
            findings.append(
                make_finding(
                    "SUSPICIOUS_DATA_PATTERN",
                    title=f"{fid} 与 {other} 之间存在跨表数据复制",
                    page=fig_obj.page,
                    claim=(
                        f"[跨表完全复制] {fid} 与 {other} 共享 {n} 个完全相同的数值"
                        f"（占 {fid} 数值的 {pct}%），不同实验条件的数据不可能完全一致"
                    ),
                    computed=(
                        f"共享数值示例：{sample}\n"
                        f"不同实验条件的 figure 数据逐位完全相同，疑似从同一数据源复制粘贴"
                    ),
                    method="Qwen3-VL 图内数值转写 + 跨表数值完全复制检测",
                    evidence_sources=[
                        {
                            "type": "figure",
                            "figure_id": fid,
                            "other_figure_id": other,
                            "shared_count": n,
                            "overlap_pct": pct,
                            "sample_values": cf["sample_values"],
                        }
                    ],
                    normal_explanation=(
                        "不同实验条件的 figure 数据逐位完全相同，这在真实实验中不可能发生；"
                        "这通常意味着数据从同一数据源复制粘贴，是绝对的人工造假证据"
                    ),
                    needs_human_review=True,
                )
            )

    # 第二轮：单图内统计指纹
    for fid, series, fig, transcripts in fig_data:
        all_vals = [v for _, vals in series for v in vals]

        try:
            from ..depth_eval_v4 import statistical_flags_from_series

            flags = statistical_flags_from_series(series)
        except Exception as exc:  # noqa: BLE001 - 指纹复用失败不影响本地指纹
            logger.warning("[audit] 复用统计指纹失败: %s", exc)
            flags = []
        flags += detect_cross_group_duplicates(series)
        pref = detect_digit_preference(all_vals)
        if pref:
            flags.append(pref)
        # 精度一致性（比值小数位数完全相同）
        prec = detect_decimal_precision_consistency(series)
        if prec:
            flags.append(prec)
        # 对照组互补/高度相似
        comp = detect_complementary_groups(series)
        flags += comp
        if not flags:
            continue

        findings.append(
            make_finding(
                "SUSPICIOUS_DATA_PATTERN",
                title=f"{fid} 图内数值呈人造数据指纹",
                page=fig.page,
                claim=flags[0],
                computed="\n".join(flags),
                method="Qwen3-VL 图内数值转写 + 等差/跨组重复/末位偏好/Benford 统计指纹",
                evidence_sources=[
                    {
                        "type": "figure",
                        "figure_id": fid,
                        "page": fig.page,
                        "snippet": "\n".join(transcripts)[:300],
                    }
                ],
                normal_explanation=(
                    "这些是统计上的造假线索而非定罪证据，需人工核对原图/原始数据；"
                    "VLM 数值转写可能有个别数字抄错，请以原文表格为准"
                ),
                needs_human_review=True,
            )
        )
    return findings
