"""GLM 视觉审计编排 PoC：4.7 统筹 + flash 初筛 + thinking 执行重扫。

角色与数据流（agentic 编排，4.7 不读图，符合纯文本模态）：
- 阶段0 初筛 GLM-4v-flash（视觉，便宜稳）：全图初步读数 + OCR 锚点 A+B 交叉验证。
- 阶段1 规划 GLM-4.7-Flash（纯文本，强规划/工具遵循）：读各图信号(几何检测/OCR/图注/初筛读数/A+B冲突)
        → 决定哪些图需要视觉复扫 + 给 thinking 的具体聚焦指令(focus_hint)。
- 阶段2 复扫 GLM-4.1v-thinking-flash（视觉，准）：被点名的图带 focus_hint + OCR 锚点重扫(A+B 不变)。
- 阶段3 收敛（文本）：综合初筛+复扫 → 最终风险定级。
- 阶段4 复核 云端 GLM-4.7-Flash（纯文本，独立第二意见）：重点筛查本地文本模型产出，挑需人工复看的图。

默认：文本规划/收敛走本地 Ornstein-V2 @8080（PAPERFORGE_ORCH_LOCAL=1，无限流零成本）；
      视觉重扫走云端 GLM-4.1v-thinking-flash（限流小）；最终 4.7 复核走云端（固定）。
      若 PAPERFORGE_ORCH_LOCAL 未置 1，则规划/收敛也走云端 4.7（全云端模式）。
运行：
  $env:PAPERFORGE_ORCH_LOCAL='1'   # 文本规划/收敛用本地 8080
  $env:PAPERFORGE_GLM_VISION_ENABLED='1'   # 视觉重扫 + 4.7 复核需要 GLM key
  python scripts/run_glm_orchestrate.py [paper_id]
覆盖模型：GLM_SCAN_MODEL(初筛) / GLM_EXEC_MODEL(复扫) / GLM_ORCH_MODEL(云端规划兜底) / GLM_VERIFY_MODEL(4.7复核)
"""
import os
import re
import sys
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Semaphore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mock_api.database import SessionLocal
from mock_api.models import PaperFigure
from mock_api.settings import get_settings
from mock_api.experiment_audit.figures import (
    analyze_figure_semantic_glm,
    _parse_glm_json,
    _compare_ocr_vs_glm,
    detect_axis_line_gaps,
    detect_panel_scale_inconsistency,
    detect_legend_color_clash,
    _figure_id,
)

GLM_SCAN_MODEL = os.environ.get("GLM_SCAN_MODEL", "glm-4v-flash")      # 初筛（便宜稳）
GLM_EXEC_MODEL = os.environ.get("GLM_EXEC_MODEL", "glm-4.1v-thinking-flash")  # 复扫（准）
GLM_ORCH_MODEL = os.environ.get("GLM_ORCH_MODEL", "glm-4.7-flash")     # 规划/收敛（云端复核/兜底）
GLM_VERIFY_MODEL = os.environ.get("GLM_VERIFY_MODEL", "glm-4.7-flash")  # 4.7 重点筛查（最终复核）
# 文本规划/收敛改用本地 Ornstein-V2 @8080（无限流、零成本）；视觉重扫仍走云端 GLM。
ORCH_LOCAL = os.environ.get("PAPERFORGE_ORCH_LOCAL", "0") == "1"
LOCAL_TEXT_URL = os.environ.get("PAPERFORGE_LOCAL_TEXT_URL", "http://127.0.0.1:8080/v1/chat/completions")
LOCAL_TEXT_MODEL = os.environ.get("PAPERFORGE_LOCAL_TEXT_MODEL", "local")

# 并发度：glm-4v-flash 实测 ~98RPM 无限速，可激进并发；
# glm-4.1v-thinking 会 1305 过载，保守并发 + 内部退避重试。均可用环境变量覆盖。
SCAN_CONCURRENCY = int(os.environ.get("PAPERFORGE_SCAN_CONCURRENCY", "6"))
RESCAN_CONCURRENCY = int(os.environ.get("PAPERFORGE_RESCAN_CONCURRENCY", "3"))


def _call_glm_text(user_text: str, timeout: int = 90) -> str:
    """纯文本调用（规划器/收敛器）。经 OpenAIProvider 统一抽象 + 指数退避，避开 4.7 并发限流。"""
    import requests as _requests
    from mock_api.llm.base import ChatMessage
    from mock_api.llm.openai_provider import OpenAIProvider

    st = get_settings()
    if not st.glm_vision_api_key:
        raise RuntimeError("未配置 PAPERFORGE_GLM_VISION_API_KEY，无法调用 GLM")
    base_url = os.environ.get("PAPERFORGE_GLM_TEXT_BASE_URL") or st.glm_vision_base_url or ""
    if not base_url.startswith("http"):
        # P4：base_url 缺失/空串时显式回落智谱默认端点，而非拼出非法 URL 静默失败
        base_url = "https://open.bigmodel.cn/api/paas/v4"
    provider = OpenAIProvider(
        api_key=st.glm_vision_api_key,
        model=GLM_ORCH_MODEL,
        base_url=base_url,
        timeout=timeout,
    )
    messages = [ChatMessage(role="user", content=user_text)]
    last = ""
    # 4.7 限速严重（实测多为 429/1305），多给重试机会
    for attempt in range(8):
        try:
            result = provider.chat(
                messages, temperature=0.1, max_tokens=2048, enable_thinking=False
            )
            content = (result.content or "").strip()
            if content:
                return content
            print(f"    [debug] 4.7 空 content, raw={content[:200]}")
            last = "empty content"
        except _requests.exceptions.HTTPError as exc:
            resp = getattr(exc, "response", None)
            code = resp.status_code if resp is not None else None
            last = f"HTTP {code}: {getattr(resp, 'text', '')[:160]}"
            # 429(限流)/1305(智谱平台过载)/502/503/504 指数退避重试
            if code in (429, 502, 503, 504, 1305):
                back = min(2**attempt * 3.0, 30)
                print(f"    [退避] HTTP {code} 等待 {back:.0f}s 后重试({attempt+1}/8)")
                time.sleep(back)
                continue
            break
        except Exception as exc:  # noqa: BLE001
            last = repr(exc)
            time.sleep(min(2**attempt * 1.5, 12))
            continue
    raise RuntimeError(f"GLM 文本调用失败: {last}")


def _call_local_text(user_text: str, timeout: int = 120) -> str:
    """本地文本模型(Ornstein-V2 @8080)调用：规划/收敛用，无限流、零成本。"""
    import requests

    payload = {
        "model": LOCAL_TEXT_MODEL,
        "messages": [{"role": "user", "content": user_text}],
        "temperature": 0.1,
        "max_tokens": 2048,
        "stream": False,
    }
    for attempt in range(3):
        try:
            r = requests.post(LOCAL_TEXT_URL, json=payload, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"    [本地模型] 连接失败 {exc!r}，重试({attempt+1}/3)")
            time.sleep(2)
            continue
        if r.status_code == 200:
            msg = r.json()["choices"][0]["message"]
            return (msg.get("content") or msg.get("reasoning_content") or "").strip()
        print(f"    [本地模型] HTTP {r.status_code}: {r.text[:160]}")
        time.sleep(2)
    raise RuntimeError("本地文本模型调用失败（8080 未起？）")


def _call_orch_text(user_text: str, timeout: int = 120) -> str:
    """规划/收敛统一入口：ORCH_LOCAL=1 走本地 8080，否则走云端 4.7。"""
    if ORCH_LOCAL:
        return _call_local_text(user_text, timeout)
    return _call_glm_text(user_text, timeout)


def _extract_json_block(text: str) -> dict:
    if not text:
        return {}
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        t = t[s : e + 1]
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        return {}


def _signal_block(fig: PaperFigure, image_path: str) -> tuple[str, dict]:
    """阶段0+初筛：几何检测 + OCR + 图注 + flash 初步视觉读数 + A+B 交叉 → 单图信号。"""
    fid = _figure_id(fig)
    det = {}
    for name, fn in (
        ("axis_gap", detect_axis_line_gaps),
        ("panel_scale", detect_panel_scale_inconsistency),
        ("legend_clash", detect_legend_color_clash),
    ):
        try:
            res = fn(image_path) if os.path.exists(image_path) else None
        except Exception:  # noqa: BLE001
            res = None
        if res:
            det[name] = res.get("claim") or res.get("title") or str(res)
    ocr = fig.ocr_text or ""
    caption = fig.caption_text or ""
    # 初步视觉读数（flash 初筛）
    init_json = {}
    init_cross = None
    if os.path.exists(image_path):
        init_resp = analyze_figure_semantic_glm(
            image_path, caption, ocr, model=GLM_SCAN_MODEL
        )
        init_json = _parse_glm_json(init_resp)
        init_cross = _compare_ocr_vs_glm(init_json, ocr, glm_raw=init_resp)

    lines = [f"【{fid}】 page={fig.page}"]
    lines.append(f"图注: {caption[:200]}")
    lines.append(f"OCR文字: {ocr[:200] if ocr else '(无)'}")
    if det:
        lines.append("几何检测(OpenCV): " + "; ".join(f"{k}={v}" for k, v in det.items()))
    else:
        lines.append("几何检测(OpenCV): 无异常")
    if init_json:
        lines.append(
            "初步视觉读数(flash): ticks="
            + json.dumps(init_json.get("tick_values") or init_json.get("printed_numbers"), ensure_ascii=False)[:160]
        )
        if init_json.get("risk_flags"):
            lines.append("初步风险标记: " + json.dumps(init_json.get("risk_flags"), ensure_ascii=False)[:120])
    else:
        lines.append("初步视觉读数(flash): 无")
    if init_cross is None:
        lines.append("A+B交叉(初筛): 无数字可比")
    elif init_cross.get("conflict"):
        lines.append("A+B交叉(初筛): ⚠ 冲突(缺失 " + json.dumps(init_cross.get("missing_in_glm"), ensure_ascii=False) + ")")
    else:
        lines.append("A+B交叉(初筛): 一致")
    return "\n".join(lines), {
        "figure": fid,
        "num": fig.figure_number,
        "page": fig.page,
        "path": image_path,
        "ocr": ocr,
        "caption": caption,
        "det": det,
        "init_json": init_json,
        "init_cross": init_cross,
    }


def _match_sig(item: dict, signals: list[dict]) -> dict | None:
    """把 4.7 规划项鲁棒匹配回 signal。

    兼容多种命名格式：
    - 'Figure 11' / 'Figure 11 (page 11)'
    - OCR 图号格式 'Figure(p18#1)' / 'Figure (p41#1)'（page#idx）
    按图号(+可选页码/idx)匹配，同名多图用页码或 idx 消歧。
    """
    fid = item.get("figure", "")
    # 标准格式：Figure 11 (page 11)
    m = re.search(r"Figure\s*(\d+)", fid, re.I)
    # OCR 图号格式：Figure(p18#1) / Figure (p41#1)
    pm = re.search(r"\(p\s*(\d+)\s*#\s*(\d+)\)", fid, re.I)
    if pm:
        page = int(pm.group(1))
        cands = [s for s in signals if s.get("page") == page]
        # idx 用 figure_number 近似消歧（同页多图）
        return cands[0] if cands else None
    if not m:
        return None
    num = m.group(1)
    pgm = re.search(r"page\s*(\d+)", fid, re.I)
    page = int(pgm.group(1)) if pgm else None
    cands = [s for s in signals if str(s.get("num")) == num]
    if page is not None:
        cands = [s for s in cands if s.get("page") == page]
    return cands[0] if cands else None


def main() -> int:
    paper_id = sys.argv[1] if len(sys.argv) > 1 else "pr_1612.08810"
    st = get_settings()
    if not st.glm_vision_api_key:
        print("✗ 未配置 PAPERFORGE_GLM_VISION_API_KEY；请先设环境变量再运行。")
        return 2

    db = SessionLocal()
    figs = (
        db.query(PaperFigure)
        .filter(PaperFigure.paper_id == paper_id)
        .order_by(PaperFigure.page)
        .all()
    )
    db.close()
    if not figs:
        print(f"✗ 无 figure 记录: {paper_id}")
        return 2

    uploads = os.path.join(ROOT, "uploads", "figures", paper_id)
    blocks = []
    signals = []
    valid_figs = [f for f in figs if f.figure_path]
    print(f"▶ 阶段0：flash 初筛 + 确定性检测（{len(valid_figs)} 张图，并发 {SCAN_CONCURRENCY}）…")
    t0 = time.perf_counter()
    # 并发初筛：glm-4v-flash 无限速，逐张串行太慢；按原顺序收集结果后打印
    sem = Semaphore(SCAN_CONCURRENCY)
    results = [None] * len(valid_figs)

    def _do_scan(idx, fig):
        ip = os.path.join(uploads, os.path.basename(fig.figure_path))
        with sem:
            return idx, _signal_block(fig, ip)

    with ThreadPoolExecutor(max_workers=SCAN_CONCURRENCY) as ex:
        futs = [ex.submit(_do_scan, i, f) for i, f in enumerate(valid_figs)]
        for fut in as_completed(futs):
            idx, (b, sig) = fut.result()
            results[idx] = (b, sig)
    for b, sig in results:
        blocks.append(b)
        signals.append(sig)
        cx = sig.get("init_cross")
        flag = "⚠冲突" if (cx or {}).get("conflict") else ("无数字" if cx is None else "一致")
        print(f"  {sig['figure']}: A+B初筛={flag} 几何={'有' if sig['det'] else '无'}")
    print(f"  初筛耗时 {time.perf_counter()-t0:.1f}s（并发 {SCAN_CONCURRENCY}）\n")

    # ── 阶段1：4.7 规划（纯文本，串行）──
    # P3/P4：规划只关心"值得重扫"的候选，不应把全部图塞进本地模型上下文
    # （大图数论文会把 24576 token 窗口撑爆 → 400 崩溃）。先按信号筛出可疑图。
    suspicious_idx = [
        i for i, s in enumerate(signals)
        if (s.get("init_cross") or {}).get("conflict") or s.get("det") or (s.get("init_cross") is None)
    ]
    plan_blocks = [blocks[i] for i in suspicious_idx] if suspicious_idx else blocks
    # 超长保护：本地窗口约 24k token，留出余量；超出则截断并标注
    MAX_PLAN_CHARS = 28000
    truncated = False
    if sum(len(b) for b in plan_blocks) > MAX_PLAN_CHARS:
        acc = 0
        cut = []
        for b in plan_blocks:
            if acc + len(b) > MAX_PLAN_CHARS:
                truncated = True
                break
            cut.append(b)
            acc += len(b)
        plan_blocks = cut
    plan_prompt = (
        "你是论文图审计的统筹规划器（纯文本，看不到图）。下面是各图已有的文本信号：\n"
        "- 几何检测(OpenCV)：axis_gap=断轴/尺度, panel_scale=子图尺度不一致, legend_clash=图例颜色冲突\n"
        "- OCR 文字（数字锚点）\n"
        "- 图注\n"
        "- 初步视觉读数(flash) 与 A+B交叉(初筛)：⚠冲突 表示 OCR 数字视觉模型没读到\n\n"
        "请判断哪些图『值得让更强的视觉模型(thinking)带着聚焦指令重新扫描』，输出严格 JSON：\n"
        '{"needs_rescan":[{"figure":"Figure 4","focus_hint":"只核对 y 轴下半段负值区的刻度数字与单位","reason":"..."}],'
        '"rationale":"..."}\n'
        "规则：\n"
        "1. 优先选：A+B冲突⚠、几何检测异常、或初步读数明显缺失/可疑的图。\n"
        "2. focus_hint 必须具体可操作（指明区域/轴/数字类型），thinking 会据此只聚焦该处。\n"
        "3. 最多选 6 张；已确认无误的图不要选。\n\n"
        + ("注意：因图数较多，以下仅为初筛可疑的子集信号（非全部图）。\n" if truncated else "")
        + "各图信号：\n"
        + "\n\n".join(plan_blocks)
    )
    orch_label = "本地 Ornstein-V2" if ORCH_LOCAL else "4.7"
    print(f"▶ 阶段1：{orch_label} 规划中…")
    t0 = time.perf_counter()
    plan_raw = _call_orch_text(plan_prompt)
    plan = _extract_json_block(plan_raw)
    # 规划空结果重试一次：本地模型少见；云端 4.7 平台过载(1305)偶发返回空/截断
    if not plan.get("needs_rescan"):
        print("  [空规划] 首次解析无 needs_rescan，退避后重试一次规划调用…")
        time.sleep(3)
        plan_raw = _call_orch_text(plan_prompt)
        plan = _extract_json_block(plan_raw)
    print(f"  规划耗时 {time.perf_counter()-t0:.1f}s")
    needs = plan.get("needs_rescan") or []
    # 兜底：规划仍为空但初筛存在 A+B ⚠ 冲突图 → 强制纳入重扫，避免限流导致整篇漏审计
    if not needs:
        conflict_sigs = [
            s for s in signals
            if (s.get("init_cross") or {}).get("conflict")
        ]
        if conflict_sigs:
            print(f"  [兜底] 规划为空，初筛有 {len(conflict_sigs)} 张 A+B⚠冲突图，强制纳入重扫")
            needs = [
                {"figure": s["figure"], "focus_hint": "核对 OCR 与视觉读数冲突的刻度数字与单位", "reason": "初筛A+B冲突兜底"}
                for s in conflict_sigs
            ]
    # 按 figure 名去重，避免 4.7 重复点名导致重复重扫（浪费 thinking 调用）
    seen: set[str] = set()
    dedup = []
    for n in needs:
        key = (n.get("figure") or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(n)
    if len(dedup) < len(needs):
        print(f"  [去重] 规划 {len(needs)} 项 → 去重后 {len(dedup)} 项（去掉 {len(needs)-len(dedup)} 个重复）")
    needs = dedup
    print(f"  4.7 选定需重扫 {len(needs)} 张：{[n.get('figure') for n in needs]}")
    print(f"  规划理由: {plan.get('rationale','')[:200]}\n")
    print(f"  [debug] 4.7规划原文前500字:\n{plan_raw[:500]}\n")

    # ── 阶段2：thinking 执行重扫（带 focus_hint，复用 A+B）──
    print(f"▶ 阶段2：thinking 聚焦重扫中（并发 {RESCAN_CONCURRENCY}）…")
    rescan = []
    matched = [(item, _match_sig(item, signals)) for item in needs]
    valid_items = [(item, sig) for item, sig in matched if sig]
    skipped = [(item, None) for item, sig in matched if not sig]
    for item, _ in skipped:
        print(f"  ⚠ 规划项 {item.get('figure', '')} 无对应图，跳过")
    res_results = [None] * len(valid_items)

    def _do_rescan(idx, item, sig):
        ip = sig["path"]
        focus = item.get("focus_hint", "")
        t0 = time.perf_counter()
        with rsem:
            resp = analyze_figure_semantic_glm(
                ip, sig["caption"], sig["ocr"], model=GLM_EXEC_MODEL, focus_hint=focus
            )
        dt = time.perf_counter() - t0
        gj = _parse_glm_json(resp)
        cross = _compare_ocr_vs_glm(gj, sig["ocr"], glm_raw=resp)
        return idx, dt, gj, cross, focus, item

    rsem = Semaphore(RESCAN_CONCURRENCY)
    with ThreadPoolExecutor(max_workers=RESCAN_CONCURRENCY) as ex:
        futs = [ex.submit(_do_rescan, i, item, sig) for i, (item, sig) in enumerate(valid_items)]
        for fut in as_completed(futs):
            idx, dt, gj, cross, focus, item = fut.result()
            fid = item.get("figure", "")
            print(f"  → {fid} 聚焦: {focus[:80]}  重扫耗时 {dt:.1f}s  "
                  f"OCR×GLM={'⚠冲突' if (cross or {}).get('conflict') else '一致/无'}")
            res_results[idx] = {
                "figure": fid,
                "focus_hint": focus,
                "reason": item.get("reason", ""),
                "glm_json": gj,
                "ocr_cross": cross,
            }
    rescan = [r for r in res_results if r]

    # ── 阶段3：4.7 收敛定级 ──
    print("\n▶ 阶段3：4.7 收敛定级中…")
    rescan_text = "\n\n".join(
        f"【{r['figure']}】 聚焦={r['focus_hint']}\n"
        f"复扫读数={json.dumps(r['glm_json'], ensure_ascii=False)[:400]}\n"
        f"A+B交叉={json.dumps(r['ocr_cross'], ensure_ascii=False)[:200] if r['ocr_cross'] else '无数字可比'}"
        for r in rescan
    ) if rescan else "(无重扫)"
    converge_prompt = (
        "你是审计收敛者（纯文本）。下面是对部分图用 thinking 视觉模型聚焦重扫后的结果，已含初筛阶段信息。"
        "请对每张重扫图给出最终判定，输出严格 JSON：\n"
        '{"verdicts":[{"figure":"Figure 4","final_severity":"low|medium|high",'
        '"verdict":"确认风险|已澄清|仍需人工","note":"..."}]}\n'
        "规则：以 OCR 锚点为准；A+B 冲突且 OCR 数字明确时优先信 OCR；仅视觉不确定且无锚点才标『仍需人工』。\n\n"
        "重扫结果：\n" + rescan_text
    )
    verdict_raw = _call_orch_text(converge_prompt)
    verdict = _extract_json_block(verdict_raw)
    print(f"{orch_label} 收敛判定：")
    for v in verdict.get("verdicts") or []:
        print(f"  {v.get('figure')}: [{v.get('final_severity')}] {v.get('verdict')} — {v.get('note','')[:120]}")

    # 阶段4：云端 4.7 重点筛查（第二意见）——复核本地文本模型产出的判定，挑出需人工再看的
    print("\n▶ 阶段4：云端 4.7 重点筛查（复核本地模型判定）…")
    t0 = time.perf_counter()
    verify_prompt = (
        f"你是资深审计复核员（纯文本）。下方是本地文本模型（{orch_label}）对部分图聚焦重扫后的最终判定。"
        "请作为独立第二意见复核：哪些判定你认同，哪些你认为定级过松/过紧需人工再看。输出严格 JSON：\n"
        '{"review":[{"figure":"Figure 4","agree":true|false,'
        '"flag":"无需处理|建议人工复核|定级偏差","note":"..."}]}\n'
        "规则：以 OCR 锚点为准；A+B 冲突且 OCR 明确时必为风险；仅视觉不确定且无锚点才『仍需人工』。\n\n"
        "本地模型判定：\n" + rescan_text
        + "\n\n本地模型收敛结论：\n" + json.dumps(verdict, ensure_ascii=False)[:600]
    )
    # 阶段4 固定走云端 4.7 复核；4.7 限速严重(实测 1305/429 高频)，失败仅告警不阻塞主流程
    try:
        verify_raw = _call_glm_text(verify_prompt)  # 固定走云端 4.7
    except RuntimeError as exc:
        print(f"  [4.7 复核] 调用失败（限速/过载），跳过独立复核：{exc}")
        verify_raw = ""
    verify = _extract_json_block(verify_raw)
    print(f"  4.7 复核耗时 {time.perf_counter()-t0:.1f}s")
    need_human = []
    for r in verify.get("review") or []:
        flag = r.get("flag", "无需处理")
        mark = "⚠" if flag != "无需处理" else "✓"
        print(f"  {mark} {r.get('figure')}: agree={r.get('agree')} {flag} — {r.get('note','')[:100]}")
        if flag in ("建议人工复核", "定级偏差"):
            need_human.append(r.get("figure"))
    if need_human:
        print(f"  [4.7 筛查] 建议人工复核的图：{need_human}")
    else:
        print("  [4.7 筛查] 本地模型判定全部通过，无需人工复核")

    print(f"\n完成。初筛 {len(signals)} 张，规划选 {len(needs)} 张，复扫 {len(rescan)} 张。")
    print(f"文本模型={orch_label}，4.7 复核={'已执行' if verify.get('review') else '无产出'}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
