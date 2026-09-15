# ADR-012: FATAL_VETO 学术诚信护栏

**状态**: Accepted（2026-08-16 扩展：实验审计联动红线）
**日期**: 2026-07-30（机制随 DEPTH v4.2 落地，属「零幻觉 / 学术诚信」承诺核心）

## 背景（Context）
- PaperForge 的核心承诺是「零幻觉 · 学术诚信」。DEPTH 审稿的「一票否决」若设计不当，要么被单条 LLM 误判把 0.7~0.95 的高分论文直接拒稿/大修，要么被篡改参数悄悄放过本应否决的劣质论文。
- 机制定义于 `mock_api/depth_eval_v4.py`：
  - `FATAL_VETO_MIN = 2`：需 **≥2 条独立 fatal 缺陷** 才触发否决（避免单条误判）。
  - `FATAL_VETO_ACCEPT_FLOOR = 0.9`：校准分 ≥ 0.9 **且** LLM 自身显式判 `accept` 时，「高分 + accept」与「LLM 断言的 fatal」自相矛盾 → fatal 标签视为噪声，不触发否决（保护 Transformer 等里程碑论文被 temp=0 贪婪解码过度断言的伪 fatal 误杀）。

## 决策（Decision）
- 否决采用「≥`FATAL_VETO_MIN` 条独立 fatal」门槛（规则 1，最高优先级），且受 `FATAL_VETO_ACCEPT_FLOOR` 分数护栏保护：高分且 LLM 自判 accept 时抑制否决。
- 二者共同构成学术诚信护栏：既防止单点 LLM 噪声误杀好论文，也防止低门槛导致否决形同虚设。
- 该机制是「零幻觉 / 学术诚信」承诺的不可分割部分，**不视为可调优参数，而是护栏**。

## 扩展：实验审计联动红线（2026-08-16）

### 背景
实验审计子系统（`mock_api/experiment_audit/`，P0 系列）与 DEPTH 原本零联动：audit 已检出高危造假 Finding（如 `SUSPICIOUS_DATA_PATTERN`），DEPTH 仍可能给出 accept——同一篇论文两个子系统裁决不一致。

### 决策
- `_assemble_result` 的裁决 override 链末尾追加 `_audit_fraud_redline`：查询该论文**最新一条 `status=completed` 的 ExperimentAudit**，若含 ≥ `AUDIT_FRAUD_REDLINE_MIN`（=2）条 `severity=high` 且 `type ∈ AUDIT_FRAUD_REDLINE_TYPES` 的 Finding，直接升级为 reject。
- 白名单三类（均为确定性算法或双证据链检出的「算出来即证据」，同 STAT_REDLINE 语义，不受 LLM 辩护稀释）：
  - `SUSPICIOUS_DATA_PATTERN`：图内数值造假指纹（VLM 转写 + 统计指纹）
  - `RELABELED_IMAGE_REUSE`：跨论文改标复用（NCC 像素 + VLM 语义双证据链）
  - `IMAGE_TAMPERING_CANDIDATE`：单图内 copy-move / 条带克隆（SIFT+RANSAC 几何验证）
- 多条门槛（=2）与 `FATAL_VETO_MIN` / `STAT_REDLINE_MIN` 同设计：防单条误报误杀。
- 论文未跑过审计时**天然不联动**——用户主动触发审计即主动提供证据，无需环境开关。
- fail-open：查询/解析异常原样返回，绝不阻断审稿管线。
- 触发明细逐条写入审稿日志（`[审计红线]` 前缀），满足「模型可见 ⟺ 有日志」。

### 后果
- 收益：弥合 audit 与 DEPTH 的裁决不一致；像素级/统计级造假证据成为硬否决来源。
- 风险与对策：audit Finding 存在误报可能（如网格纹理伪 inlier）——多条门槛 + `needs_human_review` 语义（Finding 本身要求人工终审）共同兜底。

## 后果（Consequences）
- 收益：否决结论稳健（需多重独立证据）；高分里程碑论文不被伪 fatal 误杀；0.8~0.9 边界高分包仍可被合法否决。
- 后果（误调参）：若将 `FATAL_VETO_MIN` 降为 1，单条 LLM 误判即可拒稿，直接违背学术公正；若抬高 `FATAL_VETO_ACCEPT_FLOOR` 或调高 `FATAL_VETO_MIN`，劣质论文可被悄悄放过，侵蚀「零幻觉」承诺。二者均属破坏性改动，须经评审而非随手修改。

## 备选方案（Alternatives considered）
- 单条 fatal 即否决：对 LLM 噪声过于敏感，误杀率高，否决。
- 无分数护栏的纯计数否决：无法保护高分论文免遭伪 fatal 误杀，否决。
- 把否决阈值完全交给调用方配置：易被误调破坏诚信承诺，否决（保留为代码常量护栏）。

## 相关文件
- `mock_api/depth_eval_v4.py`（`FATAL_VETO_MIN` / `FATAL_VETO_ACCEPT_FLOOR` / 规则 1；扩展：`AUDIT_FRAUD_REDLINE_TYPES` / `AUDIT_FRAUD_REDLINE_MIN` / `_audit_fraud_redline`）
- `mock_api/experiment_audit/`（Finding 产出方：`schemas.FINDING_TYPES` / `service.get_latest_audit`）
- `tests/test_depth_audit_redline.py`（联动红线回归测试，含护栏常量防误调）
- ADR-009（学术诚信合规层：provenance + 引用校验 + advisory AI 检测）
