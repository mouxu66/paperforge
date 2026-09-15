@echo off
REM PaperForge 三层视觉审计编排启动器（智谱 GLM 上游）
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PAPERFORGE_ORCH_LOCAL=1
set PAPERFORGE_GLM_VISION_ENABLED=1
set PAPERFORGE_GLM_VISION_PROVIDER=glm
set PAPERFORGE_GLM_VISION_API_KEY=3fa27087485a44bc9ccc4ae652217ffc.F47ISVRvVZX3EjXd
set PAPERFORGE_GLM_VISION_MODEL=glm-4v-flash
set PAPERFORGE_GLM_VISION_BASE_URL=https://open.bigmodel.cn/api/paas/v4
set PAPERFORGE_GLM_VISION_MAX_CONCUR=8
set PAPERFORGE_GLM_TEXT_API_KEY=3fa27087485a44bc9ccc4ae652217ffc.F47ISVRvVZX3EjXd
set PAPERFORGE_GLM_TEXT_BASE_URL=https://open.bigmodel.cn/api/paas/v4
cd /d %~dp0\..
python -u scripts\run_glm_orchestrate.py %* > scripts\runs\orch_glm.log 2>&1
endlocal
