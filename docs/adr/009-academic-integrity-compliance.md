# ADR-009: 学术诚信合规层（provenance + 引用校验 + advisory AI 检测）

**状态**: Accepted
**日期**: 2026-07-21（Accepted 2026-07-23）

## 背景（Context）
- AI 续写/改写（`continue`/`rewrite`）若被滥用会构成学术不端；AI 可能在提示下插入 `[@论文ID]` 伪造引用。
- 事后 AI 生成检测（如 `ai_likelihood`）只能作参考，**绝不可**自动影响评分/结论，否则变成隐蔽的机器裁决。

## 决策（Decision）
- **provenance**：续写/改写落库即打 `kind`（`continue`|`rewrite`），`get_project_ai_usage()` 汇总，「AI 使用声明」随导出生成（护栏 #1）。
- **引用校验**：`validate_chapter_citations()` 扫描正文 `[@id]` 比对论文库，返回疑似伪造 id 供前端高亮 + 导出标注（护栏 #2）；含 `GET /api/writing/projects/{id}/ai-usage` 端点。
- **advisory AI 检测**：`reflection_pipeline` 的 `ai_likelihood` 仅置于返回体独立字段，**不进入** 5 维 `scores`/`verdict` 融合。

## 后果（Consequences）
- 收益：AI 使用透明可披露；伪造引用可拦截；AI 信号与评分解耦，合规安全。
- 代价：导出/分析多一次聚合与检测计算；需团队纪律——禁止把 `ai_likelihood` 并入结论。

## 备选方案（Alternatives considered）
- 事后水印：易规避，否决。
- 禁用 AI 续写：牺牲核心 UX，否决。
- 将 AI 疑似度并入 verdict：触碰学术公正红线，明确否决（仅作 advisory）。
