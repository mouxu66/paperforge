# 关键架构决策记录（ADR）

PaperForge 框架演进过程中的关键架构决策记录，遵循 [Michael Nygard 的 ADR 格式](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)。

## 索引

| ADR | 标题 | 状态 | 日期 |
|-----|------|------|------|
| 001 | 单体拆分为分层 Blueprint | ✅ Accepted | 2026-07-07 |
| 002 | 全局鉴权 + 安全默认值 | ✅ Accepted | 2026-07-07 |
| 003 | 集中式 Typed 配置 (pydantic-settings) | ✅ Accepted | 2026-07-07 |
| 004 | SSE 事件总线 + 状态一致性 | ✅ Accepted | 2026-07-07 |
| 005 | 全局异常 + 结构化错误信封 | ✅ Accepted | 2026-07-07 |
| 006 | Alembic 作为 Schema 迁移唯一事实源 | ✅ Accepted | 2026-07-14 |
| 007 | OCR 子进程隔离（llama.cpp 不进主进程） | ✅ Accepted | 2026-07-21 |
| 008 | VRAM 互斥调度器（8GB 卡刚需） | ✅ Accepted | 2026-07-21 |
| 009 | 学术诚信合规层（provenance + 引用校验 + advisory AI 检测） | ✅ Accepted | 2026-07-21 |
| 010 | figure-trigger 铁律（PAPERFORGE_DISABLE_FIGURE_TRIGGER） | ✅ Accepted（2026-07-31 重评：保留，理由改防 text/vision Qwen 显存争抢） | 2026-07-30 |
| 011 | DEPTH 校准偏移决策（SCORE_OFFSET / DEPTH_AUTO_OFFSET / DEFAULT_OFFSET_TABLE） | ✅ Accepted | 2026-07-24 |
| 012 | FATAL_VETO 学术诚信护栏 | ✅ Accepted | 2026-07-30 |
| 013 | VRAM 调度重构：text-Qwen↔vision-Qwen 双模型仲裁 | 🔶 Proposed | 2026-07-31 |
| 014 | DEPTH × 感悟报告 评分严谨化改造（专家评审面板 + 引用真值校验） | 🔶 Proposed | 2026-08-06 |

## ADR 模板

新建 ADR 时使用以下模板：

```markdown
# ADR-NNNN: 标题

**状态**: Proposed | Accepted | Deprecated | Superseded by ADR-NNNN

**日期**: YYYY-MM-DD

## 背景

## 决策

## 后果（收益 / 权衡）
```
