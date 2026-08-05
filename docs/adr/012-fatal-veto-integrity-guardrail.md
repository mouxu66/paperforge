# ADR-012: FATAL_VETO 学术诚信护栏

**状态**: Accepted
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

## 后果（Consequences）
- 收益：否决结论稳健（需多重独立证据）；高分里程碑论文不被伪 fatal 误杀；0.8~0.9 边界高分包仍可被合法否决。
- 后果（误调参）：若将 `FATAL_VETO_MIN` 降为 1，单条 LLM 误判即可拒稿，直接违背学术公正；若抬高 `FATAL_VETO_ACCEPT_FLOOR` 或调高 `FATAL_VETO_MIN`，劣质论文可被悄悄放过，侵蚀「零幻觉」承诺。二者均属破坏性改动，须经评审而非随手修改。

## 备选方案（Alternatives considered）
- 单条 fatal 即否决：对 LLM 噪声过于敏感，误杀率高，否决。
- 无分数护栏的纯计数否决：无法保护高分论文免遭伪 fatal 误杀，否决。
- 把否决阈值完全交给调用方配置：易被误调破坏诚信承诺，否决（保留为代码常量护栏）。

## 相关文件
- `mock_api/depth_eval_v4.py`（`FATAL_VETO_MIN` / `FATAL_VETO_ACCEPT_FLOOR` / 规则 1）
- ADR-009（学术诚信合规层：provenance + 引用校验 + advisory AI 检测）
