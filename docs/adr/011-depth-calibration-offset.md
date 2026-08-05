# ADR-011: DEPTH 校准偏移决策（SCORE_OFFSET / DEPTH_AUTO_OFFSET / DEFAULT_OFFSET_TABLE）

**状态**: Accepted
**日期**: 2026-07-24（校准决策拍板日）

## 背景（Context）
- 校准实测（`blind_review_comparison_2026-07-24`）：DEPTH 相对人工盲评系统性偏高 +0.09~+0.12，导致 verdict 与人工判断错位。
- PeerRead 校准实证（`peerread_calibration_2026-07-24`）：DEPTH 绝对分标定**不跨语料鲁棒**——库内 2020–2026 现代 arXiv 论文偏高 ~+0.09；PeerRead 2007–2017（N=198）整体偏低，需 +0.18 修复（注意 N=40 小样本曾误估 +0.34 过度修正）。
- 单一全局偏移被证伪，改为按 (source, year) 分档查表。依据见 `deliverables/` 研究报告（`algorithm_detailed_report.md` 等）。

## 决策（Decision）
- **全局分数平移层** `PAPERFORGE_DEPTH_SCORE_OFFSET`（浮点）对最终分做全局平移。优先级：显式 env > 校准集自动估计（`calib_papers/runs/calib_offset.json`）> `0.0`（关闭，向后兼容）。
- **自动偏移开关** `DEPTH_AUTO_OFFSET`（注意：无 `PAPERFORGE_` 前缀；`=1/true/yes/on` 启用）：启用时读取 `calib_offset.json` 自动估计偏移。默认 `0`（关闭）。
- **分档偏移表** `DEFAULT_OFFSET_TABLE`（生产生效 P0 规则，无需 env 即应用）：
  - `"default"`: `-0.09`（库内现代论文校准，保持）
  - `"peerread"`: `+0.18`（基于 N=198 大规模估计）
  - key 解析优先级：`source:YEAR` > `source:ERA` > `source` > `ERA` > `default`。
  - `PAPERFORGE_DEPTH_OFFSET_TABLE`（JSON）会**叠加**到 `DEFAULT_OFFSET_TABLE`（同键覆盖），便于实验仅覆盖某 source。
- **默认全关、向后兼容**：`PAPERFORGE_DEPTH_SCORE_OFFSET` 未设且 `DEPTH_AUTO_OFFSET=0` 时偏移为 `0.0`，行为与旧版完全一致。

## 后果（Consequences）
- 收益：verdict 与人工盲评对齐；分档表解决跨语料非鲁棒问题；默认关闭保证向后兼容与可回退。
- 风险：该偏移是 400+ 篇评分的静默因子。**被当做 bug 误删会无声改变全部评分走向**（无报错、无告警），必须作为有意校准保留。

## 备选方案（Alternatives considered）
- 不校准（维持原始分）：verdict 系统性偏离人工评审，否决。
- 单一全局固定偏移（如一律 -0.09）：被 PeerRead 实证证伪（跨语料非鲁棒），否决。
- 复用他人偏移值：禁止；任何新来源（SNOR / ARR-DC 等）必须单独跑基线 + 偏移扫描。

## 相关文件
- `mock_api/depth_calibration.py`（`get_score_offset()` / `DEFAULT_OFFSET_TABLE` / `DEPTH_AUTO_OFFSET`）
- `calib_papers/runs/calib_offset.json`
- `deliverables/algorithm_detailed_report.md`
