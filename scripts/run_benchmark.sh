#!/bin/bash
# PaperForge AI 批处理 Benchmark 一键运行脚本
# 用法：bash scripts/run_benchmark.sh [--base-url URL]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

BASE_URL="${1:-http://127.0.0.1:8770}"

echo "=========================================="
echo "  PaperForge AI 批处理 Benchmark"
echo "=========================================="
echo ""

# 检查后端是否运行
echo "[1/3] 检查后端服务..."
if ! curl -s "$BASE_URL/api/health" > /dev/null 2>&1; then
    echo "错误：后端服务未启动 ($BASE_URL)"
    echo "请先运行：uvicorn mock_api.main:app --port 8770"
    exit 1
fi
echo "后端在线 ✓"
echo ""

# 运行批处理测试
echo "[2/3] 运行批处理 Benchmark..."
echo "注意：总请求数约 21 次，预计耗时 2-5 分钟"
echo ""

python scripts/ai_benchmark_batch.py --base-url "$BASE_URL" --output benchmark_results_batch.json

echo ""
echo "[3/3] 生成报告..."
if [ -f benchmark_report_batch.md ]; then
    echo "报告已生成：benchmark_report_batch.md ✓"
    echo ""
    echo "=========================================="
    echo "  运行完成！"
    echo "=========================================="
    echo ""
    echo "输出文件："
    echo "  - benchmark_results_batch.json  (原始数据)"
    echo "  - benchmark_report_batch.md     (汇总报告)"
    echo "  - completed_batch.json          (断点续传状态)"
else
    echo "警告：报告文件未生成"
    exit 1
fi
