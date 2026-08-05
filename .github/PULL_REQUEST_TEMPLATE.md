<!--
PaperForge PR 模板
配套标准：docs/代码审查标准与流程.md
开 PR 前请确认 pre-commit 已全绿；CI 门禁全绿后才进入 review。
-->

## 改动摘要
<!-- 做了什么、为什么做。一句话讲不清就说明设计有债，先想清楚再提。 -->

## 影响域
<!-- 勾选涉及的范围 -->
- [ ] 后端 Python / FastAPI（`mock_api/`）
- [ ] 前端 React / TypeScript（`web/src/`）
- [ ] 数据库迁移（Alembic）
- [ ] 测试
- [ ] 配置 / 依赖

## 自测情况
<!-- 填你实际跑过的，别空着 -->
- [ ] `pre-commit run --all-files` 全绿
- [ ] 后端：`pytest -m critical -q` 通过（或 `pytest --cov=mock_api --cov-fail-under=70`）
- [ ] 前端：`cd web && npm run format` + `npm run lint` + `npx vitest run` 通过
- [ ] 手动走查了改动涉及的功能路径：<!-- 简述验证了什么 -->

## CI 门禁
<!-- 贴 CI 运行链接或注明状态 -->
- [ ] ruff / mypy / pytest 全绿
- [ ] 前端 lint / format:check / build / vitest 全绿

## 需重点 review 的点
<!-- 主动标出你拿不准的设计、安全判断、破坏性变更，帮 reviewer 省时间 -->
-

## 关联
<!-- 关联 issue / 方案文档章节 -->
- 关联文档：`docs/代码审查标准与流程.md` §__
